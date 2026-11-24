# edge-tts 语音合成服务

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

或用 compose（需先设置 `TTS_BASE_URL`）：

```bash
export TTS_BASE_URL=http://192.168.1.10:8099
docker compose up -d
```

大陆网络构建可换源：

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t edge-tts:latest .
```

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
| `TTS_KEEP_FILES` | `silence4s.mp3` | 清理白名单，逗号分隔 |
| `TTS_PRUNE_TTL` | `3600` | 残留文件过期时间（秒） |
| `TTS_BASE_URL` | 空 | 返回 URL 的前缀，为空时按请求 Host 推断 |

## 关于静音文件

分段播报常在音频列表尾部追加静音，用于给停止指令留出窗口。这类文件由调用方自行生成并放入缓存目录，同时加进 `TTS_KEEP_FILES`，否则会被清理逻辑删除。

## 许可

[MIT](LICENSE)
