# edge-tts TTS Service

🇺🇸 **English** | 🇨🇳 [简体中文](README.zh.md)

![License](https://img.shields.io/badge/license-MIT-blue.svg) ![Python](https://img.shields.io/badge/python-3.12-3776AB.svg) ![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg) ![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

Wraps Microsoft Edge's online TTS into a cached HTTP endpoint, so speech-playback applications can synthesize audio on demand.

It targets **segmented long-text playback**: the caller splits a paragraph into segments and issues several synthesis requests; the service processes them serially, caches by content hash, and returns URLs the client can play directly — repeated text never consumes upstream quota twice.

## Features

- **Content-hash cache**: filenames derive from `md5(text|voice|rate)`, so identical text is synthesized only once
- **Serial synthesis**: concurrency defaults to 1, avoiding rate limits triggered by a shared egress IP
- **Adaptive timeout**: estimated from character count (125 ms per char, clamped to 30–150 s), so long text is not cut off early
- **Retry**: up to two attempts per synthesis before reporting failure
- **Static cache directory**: `/cache/{file}.mp3` is served directly
- **Active and passive cleanup**: callers can pin files via `/prune`; leftovers are reclaimed by TTL

## Quick Start

```bash
docker build -t edge-tts:latest .
docker run -d --name edge-tts -p 8099:8099 --restart unless-stopped \
  -v "$PWD/cache":/cache \
  -e TTS_BASE_URL=http://192.168.1.10:8099 \
  edge-tts:latest
```

Or with compose (create `docker-compose.yml` per [Configuration](#configuration) first):

```bash
export TTS_BASE_URL=http://192.168.1.10:8099
docker compose up -d
```

Behind a slow network, build against a mirror:

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t edge-tts:latest .
```

## docker-compose

Compose consolidates image build, port mapping, cache volume and environment into one file, which suits long-running deployments. Use the `docker run` form above for a quick trial; reach for compose when you need restart-on-boot, repeated tuning, or orchestration alongside other services.

### Prerequisites

- Docker Engine 20.10+ with Compose V2 (the command is `docker compose`, not `docker-compose`)
- `TTS_BASE_URL` set to an address **reachable by the playback device**; it defaults to `http://localhost:8099`, in which case the device cannot open the returned audio links
- Port 8099 free on the host

### Configuration

Save the following as `docker-compose.yml` in the project root and adjust as needed:

```yaml
services:
  edge-tts:
    image: edge-tts:latest
    container_name: edge-tts
    build:
      context: .
      args:
        # Use a mirror behind a slow network, e.g. https://pypi.tuna.tsinghua.edu.cn/simple
        PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}

    ports:
      - "8099:8099"

    volumes:
      # Cache lives beside the project for easy backup and cleanup
      - ./cache:/cache

    environment:
      - TZ=Asia/Shanghai
      # Voice: zh-CN-XiaoyiNeural (female) / zh-CN-XiaoxiaoNeural (more natural) / zh-CN-YunxiNeural (male)
      - TTS_VOICE=${TTS_VOICE:-zh-CN-XiaoxiaoNeural}
      # Speaking rate, typically -50% to +100%
      - TTS_RATE=${TTS_RATE:-+5%}
      # Max characters per request; longer input returns 413
      - TTS_MAX_CHARS=8000
      - TTS_CACHE_DIR=/cache
      # Base synthesis timeout in seconds; scales with text length
      - TTS_TIMEOUT=20
      # Concurrent syntheses: 1 means serial, lowering rate-limit risk
      - TTS_QUEUE=1
      # Persistent-file whitelist: listed files are never cleaned up
      - TTS_KEEP_FILES=prompt.mp3
      # Age in seconds after which leftovers are swept if /prune was never called
      - TTS_PRUNE_TTL=3600
      # URL prefix returned to callers; must be reachable by the playback device
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

Two things to check: `TTS_BASE_URL` must be reachable from the playback device; keep `TTS_QUEUE` at 1 to avoid upstream rate limits. Configure persistent files per [About Persistent Files](#about-persistent-files); see [Environment Variables](#environment-variables) for the full list.

### Common Commands

```bash
# Start (builds the image on first run)
docker compose up -d

# Check status and health
docker compose ps

# Follow logs
docker compose logs -f --tail=100

# Stop and remove the container (keeps the cache volume)
docker compose down

# Stop only, keep the container
docker compose stop

# Rebuild after changing server.py or Dockerfile
docker compose up -d --build

# Recreate to apply environment changes
docker compose up -d --force-recreate
```

### Notes

- **Do not wipe the cache casually.** `down -v` removes the cache volume too, forcing every text to be re-synthesized upstream — slow and prone to rate limiting. Use plain `down` day to day; cached files stay in `./cache`.
- **Change ports in pairs.** Editing `ports` without updating `TTS_BASE_URL` leaves the returned links pointing at the old port, and the player gets a 404.
- **Wait for the health check on first start.** A returned `up -d` does not mean the service is ready; call the API once `docker compose ps` shows `healthy`. While pip installs are running, `unhealthy` within `start_period` is expected.
- **Keep `TTS_QUEUE` at 1.** The upstream applies per-egress-IP rate limits, and concurrent synthesis is more likely to hit a 403. To speed things up, increase segment length on the caller side rather than concurrency.

### Troubleshooting

| Symptom | Where to look |
|---|---|
| Returned URL cannot be opened | Whether `TTS_BASE_URL` is reachable from the device, and whether the port matches `ports` |
| Synthesis returns 403 or times out often | Set `TTS_QUEUE` back to 1, check whether the egress IP is throttled, raise `TTS_TIMEOUT` |
| Container stays `unhealthy` | `docker compose logs` to see whether uvicorn started, whether 8099 is occupied |
| Cache keeps growing | Whether the caller invokes `/prune` periodically, and whether `TTS_PRUNE_TTL` is set too high |
| Files that should persist get deleted | Whether they were added to `TTS_KEEP_FILES` (empty by default) |

## API

### `POST /tts`

Synthesize a piece of text.

```json
{"text": "Text to synthesize", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+5%"}
```

`voice` and `rate` are optional and fall back to the environment variables. Response:

```json
{
  "url": "http://192.168.1.10:8099/cache/4ec579b2a59878ee47ee6b4ebb54bcae.mp3",
  "file": "4ec579b2a59878ee47ee6b4ebb54bcae.mp3",
  "duration": 2.28,
  "cached": false,
  "chars": 9
}
```

`duration` is the actual audio length in seconds, for the caller to schedule playback; `cached` indicates a cache hit.

### `GET /cache/{name}.mp3`

Serves a cached audio file. Only filenames made of `[A-Za-z0-9]` are accepted, which prevents path traversal.

### `POST /prune`

Deletes cached files that are not in `keep` and are older than the grace period.

```json
{"keep": ["4ec579b2a59878ee47ee6b4ebb54bcae.mp3", "prompt.mp3"]}
```

### `GET /healthz`

```json
{"ok": true, "edge_tts": "7.3.1", "files": 5, "queue": 1}
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TTS_VOICE` | `zh-CN-XiaoxiaoNeural` | Default voice |
| `TTS_RATE` | `+0%` | Default speaking rate |
| `TTS_MAX_CHARS` | `8000` | Max characters per request; larger input returns 413 |
| `TTS_CACHE_DIR` | `/cache` | Cache directory |
| `TTS_TIMEOUT` | `20` | Base synthesis timeout in seconds; scales up with text length |
| `TTS_QUEUE` | `1` | Concurrent syntheses; 1 means serial |
| `TTS_KEEP_FILES` | empty | Persistent-file whitelist, comma-separated; listed files are never cleaned up |
| `TTS_PRUNE_TTL` | `3600` | Age in seconds after which leftovers are reclaimed |
| `TTS_BASE_URL` | empty | URL prefix for returned links; inferred from the request Host when empty |

## Voices

`TTS_VOICE` takes an edge-tts voice identifier. Upstream currently offers 318 voices across many languages; the 14 Chinese ones are listed below.

| Voice | Gender | Use case | Style |
|---|---|---|---|
| `zh-CN-XiaoxiaoNeural` | Female | News, novel | Warm |
| `zh-CN-XiaoyiNeural` | Female | Cartoon, novel | Lively |
| `zh-CN-YunjianNeural` | Male | Sports, novel | Passionate |
| `zh-CN-YunxiNeural` | Male | Novel | Lively, sunny |
| `zh-CN-YunxiaNeural` | Male | Cartoon, novel | Cute |
| `zh-CN-YunyangNeural` | Male | News | Professional, reliable |
| `zh-CN-liaoning-XiaobeiNeural` | Female | Northeastern dialect | Humorous |
| `zh-CN-shaanxi-XiaoniNeural` | Female | Shaanxi dialect | Bright |
| `zh-HK-HiuGaaiNeural` | Female | Cantonese, general | Friendly |
| `zh-HK-HiuMaanNeural` | Female | Cantonese, general | Friendly |
| `zh-HK-WanLungNeural` | Male | Cantonese, general | Friendly |
| `zh-TW-HsiaoChenNeural` | Female | Taiwanese, general | Friendly |
| `zh-TW-HsiaoYuNeural` | Female | Taiwanese, general | Friendly |
| `zh-TW-YunJheNeural` | Male | Taiwanese, general | Friendly |

The catalog is maintained upstream, so names and counts shift between releases. List the current set:

```bash
python -m edge_tts --list-voices
```

## About Persistent Files

Files in the cache directory are reclaimed by `/prune` and the TTL sweep, with two exceptions: names passed in the caller's `keep` list, and names declared in `TTS_KEEP_FILES`.

The whitelist is empty by default, so every fixed asset you want to keep must be listed explicitly:

```bash
TTS_KEEP_FILES=prompt.mp3,intro.mp3
```

If omitted, those files get reclaimed and the caller has to regenerate or re-synthesize them.

## License

[MIT](LICENSE)
