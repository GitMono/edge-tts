# edge-tts 语音合成服务

🇺🇸 [English](README.md) | 🇨🇳 **简体中文**

![License](https://img.shields.io/badge/license-MIT-blue.svg) ![Python](https://img.shields.io/badge/python-3.12-3776AB.svg) ![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg) ![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

把 Microsoft Edge 的在线 TTS 封装成一个带缓存的 HTTP 接口，供语音播报类应用按需合成音频。

面向的场景是「长文本分段播报」：调用方把一段话切成多段并发出多个合成请求，服务串行处理、按内容哈希缓存，客户端可以直接用返回的 URL 播放，重复文本不会重复消耗上游额度。

## 特性

- **内容哈希缓存**：文件名由 `md5(text|voice|rate)` 决定，同样的文本只合成一次
- **串行合成**：默认并发数为 1，避免同一出口 IP 触发上游限流
- **超时自适应**：按字数估算超时（每字 125ms，30–150 秒），长文本不会被过早切断
- **失败重试**：单次合成最多尝试两次，仍失败才返回错误
- **静态缓存目录**：`/cache/{file}.mp3` 可直接对外提供音频
- **主动与被动清理**：客户端可调 `/prune` 保留指定文件，未清理的残留按 TTL 自动回收

## 快速开始

```bash
docker build -t edge-tts:latest .
docker run -d --name edge-tts -p 8099:8099 --restart unless-stopped \
  -v "$PWD/cache":/cache \
  -e TTS_BASE_URL=http://192.168.1.10:8099 \
  edge-tts:latest
```

或用 compose（先按下方[配置说明](#配置说明)创建 `docker-compose.yml`）：

```bash
export TTS_BASE_URL=http://192.168.1.10:8099
docker compose up -d
```

大陆网络构建可换源：

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t edge-tts:latest .
```

## docker-compose

compose 把镜像构建、端口映射、缓存卷和环境变量收敛到一处，适合长期部署。单次试跑用上面的 `docker run` 即可；需要开机自启、反复调参或与其他服务编排时用 compose。

### 前置条件

- Docker Engine 20.10+ 与 Compose V2（命令是 `docker compose`，不是 `docker-compose`）
- 已设置 `TTS_BASE_URL`，值为**播放设备可达**的地址；未设置时默认 `http://localhost:8099`，设备将无法打开返回的音频链接
- 宿主机 8099 端口未被占用

### 配置说明

将以下内容保存为 `docker-compose.yml`，置于项目根目录后按需调整：

```yaml
# edge-tts 部署配置
#
# 使用前把 TTS_BASE_URL 改成音箱可达的地址（服务返回给调用方的音频直链前缀）：
#   export TTS_BASE_URL=http://192.168.1.10:8099
#   docker compose up -d
#
# 镜像可本地构建（docker build -t edge-tts:latest .）或从别处导入（docker load）

services:
  edge-tts:
    image: edge-tts:latest
    container_name: edge-tts
    build:
      context: .
      args:
        # 大陆网络可换成 https://pypi.tuna.tsinghua.edu.cn/simple
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}

    ports:
      - "8099:8099"

    volumes:
      # mp3 缓存与配置同目录，方便备份和定期清理
      - ./cache:/cache

    environment:
      - TZ=Asia/Shanghai
      # 音色：zh-CN-XiaoyiNeural（女声）/ zh-CN-XiaoxiaoNeural（更自然）/ zh-CN-YunxiNeural（男声）
      - TTS_VOICE=${TTS_VOICE:-zh-CN-XiaoxiaoNeural}
      # 语速：范围通常 -50%~+100%
      - TTS_RATE=${TTS_RATE:-+5%}
      # 单次文本上限，超过直接 413，避免误喂超长文本
      - TTS_MAX_CHARS=8000
      - TTS_CACHE_DIR=/cache
      # 合成超时基数（秒），长文本会按字数自动上浮
      - TTS_TIMEOUT=20
      # 并发合成数：1 表示串行，降低被上游限流的概率
      - TTS_QUEUE=1
      # 常驻文件白名单：列入的文件不参与任何清理，静音文件也必须显式列出
      - TTS_KEEP_FILES=silence4s.mp3,silencestop.mp3
      # 过期时长（秒）：客户端没来得及调 /prune 时，超过此时长的残留会被顺带删除
      - TTS_PRUNE_TTL=3600
      # 返回给调用方的 URL 前缀，必须是播放设备可达的地址
      - TTS_BASE_URL=${TTS_BASE_URL:-http://localhost:8099}

    restart: unless-stopped

    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8099/healthz',timeout=3)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

三处需要确认：`TTS_BASE_URL` 必须是播放设备可达的地址；`TTS_KEEP_FILES` 默认为空，静音文件也要显式列出；`TTS_QUEUE` 保持 1 以避免触发上游限流。完整变量清单见[环境变量](#环境变量)。

### 常用操作

```bash
# 启动（首次会构建镜像）
docker compose up -d

# 查看状态与健康
docker compose ps

# 跟踪日志
docker compose logs -f --tail=100

# 停止并移除容器（保留缓存卷）
docker compose down

# 只停止，不删容器
docker compose stop

# 改了 server.py 或 Dockerfile 后重建
docker compose up -d --build

# 改了环境变量后重启生效
docker compose up -d --force-recreate
```

### 注意事项

- **缓存不要随手清**。`down -v` 会连同缓存卷一起删除，之后所有文本都要重新向上游合成，既慢又可能触发限流。日常用 `down` 即可，缓存文件留在 `./cache`。
- **改端口要成对改**。只改 `ports` 而不改 `TTS_BASE_URL`，接口返回的链接仍指向旧端口，播放端会 404。
- **首次启动等健康检查通过**。`up -d` 返回不代表服务可用，`docker compose ps` 看到 `healthy` 再调用接口；构建期 pip 安装较慢时，`start_period` 内的 `unhealthy` 属于正常过程。
- **`TTS_QUEUE` 保持为 1**。上游对同一出口 IP 有频率限制，并发合成更容易触发 403；确实要提速时优先加大调用方的段长，而不是提高并发。

### 故障排查

| 现象 | 排查方向 |
|---|---|
| 接口返回的 URL 打不开 | `TTS_BASE_URL` 是否为设备可达地址、端口是否与 `ports` 一致 |
| 合成返回 403 或频繁超时 | `TTS_QUEUE` 调回 1、检查出口 IP 是否被限流、适当加大 `TTS_TIMEOUT` |
| 容器状态一直 `unhealthy` | `docker compose logs` 看 uvicorn 是否起来、8099 是否被占用 |
| 缓存文件越来越多 | 调用方是否定期调 `/prune`；`TTS_PRUNE_TTL` 是否被设得过大 |
| 需要长期保留的文件被清理 | 是否已加进 `TTS_KEEP_FILES`（白名单默认为空，静音文件同样要显式列出） |

## 接口

### `POST /tts`

合成一段文本。

```json
{"text": "要合成的文本", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+5%"}
```

`voice`、`rate` 可省略，回退到环境变量。响应：

```json
{
  "url": "http://192.168.1.10:8099/cache/4ec579b2a59878ee47ee6b4ebb54bcae.mp3",
  "file": "4ec579b2a59878ee47ee6b4ebb54bcae.mp3",
  "duration": 2.28,
  "cached": false,
  "chars": 9
}
```

`duration` 为音频实际时长（秒），供调用方排期播放；`cached` 表示是否命中已有缓存。

### `GET /cache/{name}.mp3`

返回缓存的音频文件，仅接受 `[A-Za-z0-9]` 构成的文件名，防目录穿越。

### `POST /prune`

删除缓存中不在 `keep` 列表且超过宽限期的文件。

```json
{"keep": ["4ec579b2a59878ee47ee6b4ebb54bcae.mp3", "silence4s.mp3"]}
```

### `GET /healthz`

```json
{"ok": true, "edge_tts": "7.3.1", "files": 5, "queue": 1}
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | 默认音色 |
| `TTS_RATE` | `+0%` | 默认语速 |
| `TTS_MAX_CHARS` | `8000` | 单次文本上限，超过返回 413 |
| `TTS_CACHE_DIR` | `/cache` | 缓存目录 |
| `TTS_TIMEOUT` | `20` | 合成超时基数（秒），实际按字数上浮 |
| `TTS_QUEUE` | `1` | 并发合成数，1 为串行 |
| `TTS_KEEP_FILES` | 空 | 常驻文件白名单，逗号分隔；列入者不参与任何清理 |
| `TTS_PRUNE_TTL` | `3600` | 残留文件过期时间（秒） |
| `TTS_BASE_URL` | 空 | 返回 URL 的前缀，为空时按请求 Host 推断 |

## 关于常驻文件

缓存目录里的文件默认会被 `/prune` 与 TTL 回收，只有两类例外：客户端在 `/prune` 的 `keep` 中指定保留的，以及通过 `TTS_KEEP_FILES` 声明的。

白名单默认为空，所有需要长期保留的固定素材都必须显式列出，包括分段播报使用的静音文件：

```bash
TTS_KEEP_FILES=silence4s.mp3,silencestop.mp3
```

漏配的后果是这些文件被回收后，调用方需要重新生成或重新合成。

## 许可

[MIT](LICENSE)
