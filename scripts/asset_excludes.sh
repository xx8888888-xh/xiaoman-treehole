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
)
