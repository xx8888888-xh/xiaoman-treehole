#!/usr/bin/env bash
# 发布物排除规则（唯一口径）。
# build_assets.sh（tar 复制）与 verify_assets.sh（文件清单比对）共用，
# 避免两份规则漂移。排除项（审计 android D22）：node_modules、npm 清单、
# 测试音频 selftest、文档截图 docs/shots（非运行时资源）、模型回滚备份 *.orig。
ASSET_TAR_EXCLUDES=(
  --exclude=node_modules
  --exclude=package.json
  --exclude=package-lock.json
  --exclude=biome.json
  --exclude=tests
  --exclude='assets/audio/selftest.*'
  --exclude='docs/shots'
  --exclude=docs
  --exclude='*.orig'
  --exclude=ASSET_MANIFEST.json
  # Hiyori 全档纹理（4.12MiB）无任何路径引用：合并主干后也不得再打进发布物
  --exclude='assets/models/hiyori/Hiyori.2048'
  # 全尺寸基准分层（2.06MiB）：设备永远按档取图（pickTier 必给 s070/s050，
  # mapPath 把 art/layers/ 换成档内同路径），基准层运行时不请求 → 不打进 APK
  --exclude='assets/live2d/original/art/layers'
  # 缩略图（210KiB）：设置页不展示模型缩略图，无任何代码引用
  --exclude='assets/live2d/original/art/thumb.png'
)
