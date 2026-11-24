"""edge-tts 语音合成服务。

功能：接收文本，调用微软 Edge 在线语音合成，落地为 mp3，并以静态 URL 提供，
供音箱、客户端等任意调用方拉流播放。相同文本命中缓存直接返回，避免重复外网请求。

用法：uvicorn server:app --host 0.0.0.0 --port 8099

接口：
    POST /tts               {"text": "...", "voice": "...", "rate": "+5%"}
                            返回 {"url", "file", "duration", "cached", "chars"}
    POST /prune             {"keep": ["a.mp3"]}，返回 {"removed", "left"}
    GET  /cache/<name>.mp3  音频文件（支持 Range）
    GET  /healthz           健康检查
    GET  /                  端点说明

环境变量：
    TTS_VOICE        默认音色，默认 zh-CN-XiaoxiaoNeural
    TTS_RATE         默认语速，默认 +0%
    TTS_MAX_CHARS    单次文本上限，默认 8000
    TTS_CACHE_DIR    缓存目录，默认 /cache
    TTS_TIMEOUT      合成超时基数（秒），默认 20，长文本按字数上浮
    TTS_BASE_URL     返回 URL 的主机前缀，留空则取请求的 base_url
    TTS_QUEUE        同时合成数，默认 1（串行，避免同 IP 触发限流）
    TTS_KEEP_FILES   常驻文件白名单（逗号分隔），列入者不参与任何清理
    TTS_PRUNE_TTL    合成时顺带清理的过期时长（秒），默认 3600
"""

import asyncio
import hashlib
import os
import time
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from mutagen.mp3 import MP3
from pydantic import BaseModel, Field

CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", "/cache"))
VOICE = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
RATE = os.getenv("TTS_RATE", "+0%")
MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "8000"))
TIMEOUT = float(os.getenv("TTS_TIMEOUT", "20"))
BASE_URL = os.getenv("TTS_BASE_URL", "").rstrip("/")
QUEUE = max(1, int(os.getenv("TTS_QUEUE", "1")))

# 常驻文件白名单：文件名在此集合中的文件不参与清理，适用于各类固定资源
KEEP_FILES = {
    name.strip()
    for name in os.getenv("TTS_KEEP_FILES", "").split(",")
    if name.strip()
}

# 过期时间：超过这个时长仍未清理的残留（进程崩溃、超时、客户端漏调）会被顺带删除
PRUNE_TTL = float(os.getenv("TTS_PRUNE_TTL", "3600"))

app = FastAPI(title="edge-tts", version="1.0.0")

# 合成闸门：微软接口对同 IP 有频率限制，默认串行，避免批量预生成时被 429/403
_gate = asyncio.Semaphore(QUEUE)


class TtsRequest(BaseModel):
    """合成请求体。"""

    text: str = Field(min_length=1, description="待合成文本")
    voice: str | None = Field(default=None, description="覆盖默认音色，如 zh-CN-XiaoxiaoNeural")
    rate: str | None = Field(default=None, description="覆盖默认语速，如 +5%")


def _cache_key(text: str, voice: str, rate: str) -> str:
    """计算缓存文件名（不含扩展名）。

    参数:
        text: 文本内容
        voice: 音色名
        rate: 语速参数
    返回:
        str: 32 位十六进制摘要
    """
    raw = f"{text}|{voice}|{rate}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def _duration(path: Path) -> float:
    """读取 mp3 时长（秒）。

    参数:
        path: mp3 文件路径
    返回:
        float: 时长，解析失败返回 0
    """
    try:
        return round(float(MP3(str(path)).info.length), 2)
    except Exception:
        return 0.0


async def _synth(text: str, voice: str, rate: str, path: Path) -> None:
    """调用 edge-tts 生成 mp3，成功后才落到正式文件名。

    参数:
        text: 文本内容
        voice: 音色名
        rate: 语速参数
        path: 目标文件路径
    异常:
        HTTPException: 两次尝试后仍失败，返回 502
    """
    tmp = path.with_suffix(".part")
    # 每字 125ms 的合成预算，封顶 150 秒；串行下不会排队，只需覆盖单次真实耗时
    timeout = max(30.0, min(150.0, len(text) / 8))
    last: Exception | None = None
    for attempt in range(2):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate)
            await asyncio.wait_for(comm.save(str(tmp)), timeout=timeout)
            if tmp.stat().st_size < 1024:
                raise RuntimeError("服务端返回空音频")
            os.replace(tmp, path)
            return
        except Exception as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            if attempt < 1:
                await asyncio.sleep(1 + attempt)
    raise HTTPException(status_code=502, detail=f"合成失败: {last}")


def _prune_cache(keep: set[str], grace: float) -> int:
    """删除缓存目录中不在 keep 内、且超过宽限期的 mp3。

    参数:
        keep: 需要保留的文件名集合
        grace: 宽限期（秒），mtime 在此之内的文件不删；传 0 表示不留宽限
    返回:
        int: 被删除的文件数量
    """
    now = time.time()
    removed = 0
    for f in CACHE_DIR.glob("*.mp3"):
        if f.name in keep or f.name in KEEP_FILES:
            continue
        try:
            if grace > 0 and now - f.stat().st_mtime < grace:
                continue
            f.unlink()
            removed += 1
        except OSError:
            continue
    return removed


@app.post("/tts")
async def tts(req: TtsRequest, request: Request) -> JSONResponse:
    """合成文本并返回可播放 URL。

    参数:
        req: 请求体
        request: 用于推断对外主机地址
    返回:
        JSONResponse: url / file / duration / cached / chars
    """
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 为空")
    if len(text) > MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"text 超过 {MAX_CHARS} 字上限")

    voice = req.voice or VOICE
    rate = req.rate or RATE
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(text, voice, rate)}.mp3"

    cached = path.exists()
    if not cached:
        async with _gate:
            if not path.exists():
                await _synth(text, voice, rate, path)
        removed = _prune_cache({path.name}, PRUNE_TTL)
        if removed:
            print(f"顺带清理 {removed} 个过期缓存")

    base = BASE_URL or str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "url": f"{base}/cache/{path.name}",
            "file": path.name,
            "duration": _duration(path),
            "cached": cached,
            "chars": len(text),
        }
    )


@app.get("/cache/{name}")
async def get_cache(name: str) -> FileResponse:
    """提供缓存目录下的 mp3。

    参数:
        name: 文件名，仅允许字母数字与 .mp3 后缀
    返回:
        FileResponse: 音频流
    """
    if not name.endswith(".mp3") or not name[:-4].isalnum():
        raise HTTPException(status_code=400, detail="非法文件名")
    path = CACHE_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"}
    )


class PruneRequest(BaseModel):
    """清理请求体。"""

    keep: list[str] = Field(
        default_factory=list, description="需要保留的文件名，通常传本次播报用到的 mp3"
    )


@app.post("/prune")
async def prune(req: PruneRequest | None = None) -> JSONResponse:
    """清理已生成的语音缓存。

    参数:
        req: 可选请求体，keep 内的文件不删；不传则只保留白名单资源
    返回:
        JSONResponse: removed 删除数量，left 剩余文件数
    """
    keep = {n.strip() for n in (req.keep if req else []) if n and n.strip()}
    removed = _prune_cache(keep, 0)
    left = len(list(CACHE_DIR.glob("*.mp3"))) if CACHE_DIR.is_dir() else 0
    return JSONResponse({"removed": removed, "left": left})


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """健康检查。

    返回:
        JSONResponse: 服务状态、版本、缓存文件数量
    """
    try:
        version = edge_tts.__version__
    except Exception:
        version = "unknown"
    count = len(list(CACHE_DIR.glob("*.mp3"))) if CACHE_DIR.is_dir() else 0
    return JSONResponse({"ok": True, "edge_tts": version, "files": count, "queue": QUEUE})


@app.get("/")
async def index() -> JSONResponse:
    """根路径，便于浏览器直接确认服务在线。

    返回:
        JSONResponse: 端点说明
    """
    return JSONResponse(
        {
            "service": "edge-tts",
            "post": "/tts",
            "prune": "/prune",
            "cache": "/cache/<hash>.mp3",
            "health": "/healthz",
        }
    )