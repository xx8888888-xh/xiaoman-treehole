# syntax=docker/dockerfile:1
# 小满树洞 · 单镜像多目标（web / mock / tts 共用同一运行时）
# TTS 服务不内置任何第三方语音库 / 模型：默认 provider=none，由前端
# 「安卓系统 TTS / 浏览器 speechSynthesis」接管；需要服务端合成时用环境变量
# 显式启用可选 provider（不新增运行时 pip 依赖，见 server/tts_server.py）。
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
# 静态站单一真源：只消费 build_assets.sh 产出的 android assets/www，
# 不再 COPY web/（否则容器内容会和工作树/APK 漂移，红队 P1-1）。
COPY android/app/src/main/assets/www/ ./web/
COPY scripts/verify_manifest.py ./scripts/verify_manifest.py
COPY scripts/verify_assets.py ./scripts/verify_assets.py

# 静态站 / mock API / TTS 三个端口
EXPOSE 8901 8902 8903

# 静态站（compose 里 web 服务用这个 target）；mock / tts 由 compose 覆盖 command
CMD ["python", "-m", "http.server", "8901", "--bind", "0.0.0.0", "--directory", "web"]
