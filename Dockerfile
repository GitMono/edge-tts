# edge-tts 语音合成服务
#
# 构建：
#   docker build -t edge-tts:latest .
#   docker buildx build --platform linux/amd64 -t edge-tts:latest --load .   # 为 x86 设备构建
#
# 大陆网络可换源：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#
# 运行：
#   docker run -d --name edge-tts -p 8099:8099 --restart unless-stopped \
#     -v "$PWD/cache":/cache \
#     -e TTS_VOICE=zh-CN-XiaoxiaoNeural -e TTS_RATE=+5% \
#     -e TTS_BASE_URL=http://<局域网IP>:8099 \
#     edge-tts:latest

FROM python:3.12-slim

# 关闭 pip 缓存与字节码写入，减小镜像体积
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 允许构建时换源
ARG PIP_INDEX_URL=https://pypi.org/simple

# 依赖版本固定，避免上游变更导致行为漂移
COPY requirements.txt ./
RUN pip install -i "$PIP_INDEX_URL" -r requirements.txt

WORKDIR /app
COPY server.py ./

# 缓存目录，运行时由卷挂载覆盖为宿主机路径
RUN mkdir -p /cache

EXPOSE 8099

# 服务自带 /healthz；slim 镜像无 curl，用 python 发请求
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8099", "--workers", "1"]
