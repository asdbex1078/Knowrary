#!/usr/bin/env bash
# Day-Info 提交助手：git add day-info + 提交 + 推送（HTTPS → SSH 双通道自动切换）。
# 用法：bash day-info/scripts/publish.sh "提交信息"
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
MSG="${1:-day-info: 更新 $(date +%F)}"
BRANCH="${DAYINFO_BRANCH:-day-info-for-autoclaw}"

HTTPS_URL="https://github.com/asdbex1078/Knowrary.git"
SSH_URL="git@github.com:asdbex1078/Knowrary.git"

# macOS 没有 timeout，只有装了 coreutils 才有 gtimeout；缺失时降级为不设超时
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi
push_with_timeout() {
  if [ -n "$TIMEOUT_BIN" ]; then "$TIMEOUT_BIN" 90 git push -q "$@"; else git push -q "$@"; fi
}

git add day-info
if git diff --cached --quiet; then
  echo "== 无变更需要提交 =="
else
  git commit -q -m "$MSG"
  echo "== 已本地提交：$(git log -1 --format='%h %s') =="
fi

# 凭证文件优先级：环境变量 → 仓库本地 .secrets/ → AutoClaw 旧路径（兼容）
CREDS=""
for candidate in "${KNOWRARY_DAYINFO_CREDS:-}" \
                 "$ROOT/.secrets/git-credentials" \
                 "/root/.openclaw-autoclaw/workspace/.secrets/git-credentials"; do
  if [ -n "$candidate" ] && [ -s "$candidate" ]; then CREDS="$candidate"; break; fi
done

SSH_KEY=""
for candidate in "${KNOWRARY_DAYINFO_SSH_KEY:-}" \
                 "$ROOT/.secrets/github_deploy_key" \
                 "/root/.openclaw-autoclaw/workspace/.secrets/github_deploy_key"; do
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then SSH_KEY="$candidate"; break; fi
done

# 通道 1：HTTPS。有凭证文件就用它，否则交给系统凭据助手（macOS 钥匙串）
if [ -n "$CREDS" ]; then
  git config credential.helper "store --file=$CREDS"
  echo "== 使用凭证文件：$CREDS =="
else
  echo "== 未找到凭证文件，改用系统凭据助手 =="
fi
if GIT_TERMINAL_PROMPT=0 push_with_timeout "$HTTPS_URL" "HEAD:$BRANCH" 2>/dev/null; then
  echo "== 已推送 $BRANCH（HTTPS） =="
  exit 0
fi
echo "== HTTPS 推送未成功，改试 SSH… =="

# 通道 2：SSH。有专用 deploy key 就用它，否则用系统默认密钥
if [ -n "$SSH_KEY" ]; then
  SSH_CMD="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20"
else
  SSH_CMD="ssh -o BatchMode=yes -o ConnectTimeout=20"
fi
if GIT_SSH_COMMAND="$SSH_CMD" push_with_timeout "$SSH_URL" "HEAD:$BRANCH" 2>/dev/null; then
  echo "== 已推送 $BRANCH（SSH） =="
  exit 0
fi

echo "== 推送未成功（已保留本地提交，下次自动重试）。检查：A) PAT 写入 .secrets/git-credentials；或 B) Deploy Key 已添加到 GitHub 并启用写权限 =="
exit 0
