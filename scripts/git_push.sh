#!/usr/bin/env bash
# 一键把指定分支（默认当前分支）推送到远端同名分支（不存在则新建）。
# 凭据：从仓库外的 /workspace/.secrets/github.token 读取 PAT（绝不入库、不写入 git config）。
# 用法：
#   scripts/git_push.sh                 # 推送当前分支
#   scripts/git_push.sh <branch>        # 推送指定分支
# 环境变量：
#   XIAOMAN_GITHUB_TOKEN_FILE  覆盖默认 token 路径
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN_FILE="${XIAOMAN_GITHUB_TOKEN_FILE:-/workspace/.secrets/github.token}"
BRANCH="${1:-$(git -C "$REPO" rev-parse --abbrev-ref HEAD)}"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "❌ 缺少 GitHub 凭据：$TOKEN_FILE" >&2
  echo "   请把 Fine-grained PAT（Contents: Read and write）写入该文件并 chmod 600。" >&2
  exit 1
fi

case "$BRANCH" in
  main|master|HEAD)
    echo "❌ 拒绝直接推送 $BRANCH —— 请使用特性分支（铁律：新开分支再推）。" >&2
    exit 1
    ;;
esac

# 生成一次性凭据助手（仅从密钥文件读取，不落任何明文到仓库/配置）
helper="$(mktemp)"
trap 'rm -f "$helper"' EXIT
cat > "$helper" <<'EOS'
#!/usr/bin/env bash
case "$1" in
  get) printf 'username=x-access-token\npassword=%s\n' "$(tr -d '\r\n' < "${XIAOMAN_GITHUB_TOKEN_FILE:-/workspace/.secrets/github.token}")" ;;
esac
EOS
chmod +x "$helper"

cd "$REPO"
echo "▶ 推送 $BRANCH -> origin/$BRANCH ..."
git -c credential.helper="$helper" push -u origin "$BRANCH"
echo "✅ 已推送：origin/$BRANCH"
