#!/usr/bin/env bash
# Day-Info 提交助手：git add day-info + 提交 + 推送（HTTPS → SSH 双通道自动切换）。
# 用法：bash day-info/scripts/publish.sh "提交信息"
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
MSG="${1:-day-info: 更新 $(date +%F)}"
BRANCH="${DAYINFO_BRANCH:-day-info}"

HTTPS_URL="https://github.com/asdbex1078/Knowrary.git"
SSH_URL="git@github.com:asdbex1078/Knowrary.git"

# macOS 没有 timeout，只有装了 coreutils 才有 gtimeout；缺失时降级为不设超时
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi
# LC_ALL=C：强制 git 用英文报错，下面 is_non_ff 的识别才稳（中文环境里英文正则会漏判）
push_with_timeout() {
  if [ -n "$TIMEOUT_BIN" ]; then LC_ALL=C "$TIMEOUT_BIN" 90 git push -q "$@"; else LC_ALL=C git push -q "$@"; fi
}

# 非快进 = 远端分支已被另一份克隆 / 另一台机器推进过，本地落后。
# 这类失败重试多少次都一样，必须立刻停手让人来处理，不能混在「链路抖动」里空转。
is_non_ff() {
  tail -n 8 "${PUSH_ERRLOG}" 2>/dev/null \
    | grep -qiE 'non-fast-forward|fetch first|\[rejected\]|updates were rejected'
}
abort_if_non_ff() {
  if is_non_ff; then
    echo "!! 推送被拒：远端 ${BRANCH} 已领先本地（不是链路抖动，重试无用）。"
    echo "!! 本地提交已保留。请先 git pull --rebase origin ${BRANCH} 核对后再重跑。"
    exit 1
  fi
}

# 分支护栏：本脚本只在目标分支上执行。
# 原先直接 git commit 打在「当前分支」上，再 push HEAD:$BRANCH——
# 一旦在别的分支上跑，提交就落错分支，还会把那条分支的内容推到 $BRANCH 上去。
CUR_BRANCH="$(git symbolic-ref -q --short HEAD || echo "")"
if [ "$CUR_BRANCH" != "$BRANCH" ]; then
  echo "!! 当前分支是「${CUR_BRANCH:-游离 HEAD}」，本脚本只在「${BRANCH}」上执行。"
  echo "!! 未做任何提交与推送。请先 git checkout ${BRANCH} 再重跑。"
  exit 1
fi

# 只提交 day-info 路径：用 --only 形式，避免把用户此前 git add 的其它文件一起卷进这次提交
git add day-info
if git diff --cached --quiet -- day-info; then
  echo "== 无变更需要提交 =="
else
  git commit -q -m "$MSG" -- day-info
  echo "== 已本地提交：$(git log -1 --format='%h %s') =="
fi

# 凭证文件优先级：环境变量 → 仓库本地 .secrets/
CREDS=""
for candidate in "${KNOWRARY_DAYINFO_CREDS:-}" \
                 "$ROOT/.secrets/git-credentials"; do
  if [ -n "$candidate" ] && [ -s "$candidate" ]; then CREDS="$candidate"; break; fi
done

SSH_KEY=""
for candidate in "${KNOWRARY_DAYINFO_SSH_KEY:-}" \
                 "$ROOT/.secrets/github_deploy_key"; do
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
    abort_if_non_ff
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

# 推送成功后手动刷新远端跟踪引用。
# 用 URL（而非 remote 名）推送时 git 不会更新 refs/remotes/<remote>，后果是：
#   1) 编辑器（IDEA 等）会一直显示「领先 origin 若干提交」，与实际不符；
#   2) 之后任何基于跟踪引用的 --force-with-lease 都会拿到过期值而被拒。
# 这里补上 git 对具名 remote 本应做的事。
mark_pushed() {
  if git remote | grep -qx origin; then
    git update-ref "refs/remotes/origin/${BRANCH}" "$(git rev-parse HEAD)" 2>/dev/null || true
  fi
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
  mark_pushed
  echo "== 已推送 ${BRANCH}（HTTPS） =="
  exit 0
fi
abort_if_non_ff
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
  mark_pushed
  echo "== 已推送 ${BRANCH}（SSH） =="
  exit 0
fi

echo "== 推送未成功（已保留本地提交，下次自动重试）。已重试 ${PUSH_RETRIES} 次 =="
echo "== 最后一条错误：$(tail -n 1 "${PUSH_ERRLOG}" 2>/dev/null) =="
exit 0
