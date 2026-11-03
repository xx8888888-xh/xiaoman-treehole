# syntax=docker/dockerfile:1
# 小满树洞 · 单镜像多目标（web / mock / tts 共用运行时，tts 额外装 edge-tts）
# 所有容器统一加 label com.dsh.project=treehole（见 docker-compose.yml）
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XIAOMAN_BIND_HOST=0.0.0.0 \
    TTS_CACHE_DIR=/data/tts \
    XIAOMAN_DATA_DIR=/data/sessions \
    XIAOMAN_ALLOW_ORIGINS=http://127.0.0.1:8901

WORKDIR /app
COPY server/ ./server/
COPY web/ ./web/

# 静态站 / mock API / TTS 三个端口
EXPOSE 8901 8902 8903

# 默认：静态站点（compose 里 web 服务用这个 target）
CMD ["python", "-m", "http.server", "8901", "--bind", "0.0.0.0", "--directory", "web"]

# TTS 目标：额外装 edge-tts（缓存目录由 TTS_CACHE_DIR + volume 提供）
FROM runtime AS tts
RUN pip install --no-cache-dir "edge-tts>=6.1,<8"
CMD ["python", "server/tts_server.py"]
