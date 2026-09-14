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

# 推送重试次数（SSH 通道经代理转发时抖动明显，见下方注释）
PUSH_RETRIES="${DAYINFO_PUSH_RETRIES:-6}"
PUSH_ERRLOG="$(mktemp -t dayinfo-push)"
trap 'rm -f "$PUSH_ERRLOG"' EXIT

# 重试包装：$1 = 最多尝试次数，其余参数为要执行的命令
retry() {
  local max="$1"; shift
  local attempt=1
  while [ "${attempt}" -le "${max}" ]; do
    if "$@" 2>>"${PUSH_ERRLOG}"; then return 0; fi
    if [ "${attempt}" -lt "${max}" ]; then
      local wait_s=$(( attempt * 3 ))
      [ "${wait_s}" -gt 15 ] && wait_s=15
      echo "== 第 ${attempt}/${max} 次失败，${wait_s}s 后重试… =="
      sleep "${wait_s}"
    fi
    attempt=$(( attempt + 1 ))
  done
  return 1
}

# 通道 1：HTTPS。有凭证文件就用它，否则交给系统凭据助手（macOS 钥匙串）。
# 不做重试：本机到 github.com:443 的 TLS 握手本身失败，重试无法改善。
if [ -n "$CREDS" ]; then
  git config credential.helper "store --file=$CREDS"
  echo "== 使用凭证文件：${CREDS} =="
else
  echo "== 未找到凭证文件，改试系统凭据助手 / SSH =="
fi
if GIT_TERMINAL_PROMPT=0 push_with_timeout "$HTTPS_URL" "HEAD:$BRANCH" 2>>"${PUSH_ERRLOG}"; then
  echo "== 已推送 ${BRANCH}（HTTPS） =="
  exit 0
fi
echo "== HTTPS 推送未成功，改试 SSH… =="

# 通道 2：SSH。有专用 deploy key 就用它，否则用系统默认密钥
# （本机 ~/.ssh/config 已把 github.com 指向 ssh.github.com:443，并用 id_ed25519_github）。
# 该链路经代理时会间歇性「TCP 已建立后被对端立即关闭」。2026-09-14 实测连续 8 次探测
# 失败 3 次（约 37%，特征均为 Connection closed by 198.18.0.x port 443），
# 属于链路抖动而非凭证失效。默认重试 6 次，可将失败率压到 0.3% 以下；
# 单次失败不能判定为推送失败。
if [ -n "$SSH_KEY" ]; then
  SSH_CMD="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20"
else
  SSH_CMD="ssh -o BatchMode=yes -o ConnectTimeout=20"
fi
export GIT_SSH_COMMAND="${SSH_CMD}"
if retry "${PUSH_RETRIES}" push_with_timeout "$SSH_URL" "HEAD:$BRANCH"; then
  echo "== 已推送 ${BRANCH}（SSH） =="
  exit 0
fi

echo "== 推送未成功（已保留本地提交，下次自动重试）。已重试 ${PUSH_RETRIES} 次 =="
echo "== 最后一条错误：$(tail -n 1 "${PUSH_ERRLOG}" 2>/dev/null) =="
exit 0
