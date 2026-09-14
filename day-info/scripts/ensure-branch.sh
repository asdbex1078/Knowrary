#!/usr/bin/env bash
# 确保仓库位于 day-info 分支——必须在任何读写 day-info/ 数据的步骤【之前】调用。
#
# 为什么要这么早切：collect.py 每次运行都读写 day-info/state.json（45 天 URL 去重台账）。
# 若此时 checkout 在 main 等落后分支上，它读到的是过期台账，去重会静默失效，把已收录的条目
# 重新入池；而且等到 publish.sh 再想切分支时，state.json 已经「两分支不同 + 有本地改动」，
# git 会拒绝切换，任务直接失败。工作区干净的时候切，才是稳的。
#
# 原分支记在 .git/dayinfo-orig-ref，由流程最后一步 publish.sh 负责切回。
#
# 用法：bash day-info/scripts/ensure-branch.sh [分支名]
# 关闭自动切换：DAYINFO_AUTO_SWITCH=0（此时不在目标分支就直接失败）
#
# 【刻意保持精简】本文件运行期间会被 git checkout 替换成目标分支上的版本。
# bash 按字节偏移惰性读取脚本，只有文件小于首次读取块（8KB）才不会读到替换后的内容。
# 往这里加东西前先看 wc -c，务必远小于 8192。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BRANCH="${1:-${DAYINFO_BRANCH:-day-info}}"
ORIG_FILE="$(git rev-parse --git-path dayinfo-orig-ref)"
CUR="$(git symbolic-ref -q --short HEAD || echo "")"

if [ "$CUR" = "$BRANCH" ]; then
  echo "== 已在 ${BRANCH} 分支 =="
  exit 0
fi

fail() { echo "!! 需要在「${BRANCH}」上操作，但当前是「${CUR:-游离 HEAD}」。"; echo "!! $1"; exit 1; }

[ "${DAYINFO_AUTO_SWITCH:-1}" = "1" ] \
  || fail "已设 DAYINFO_AUTO_SWITCH=0 关闭自动切换，请先 git checkout ${BRANCH}。"

# 有进行中的 git 操作时切分支会毁现场
for op in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
  [ -e "$(git rev-parse --git-path "$op")" ] \
    && fail "检测到进行中的 git 操作（${op}），此时切分支会破坏现场，请先处理完。"
done

# 游离 HEAD 时记 commit sha，否则记分支名
if [ -n "$CUR" ]; then ORIG="$CUR"; else ORIG="$(git rev-parse HEAD)"; fi

# 本地没有目标分支就从 origin 建
if ! git rev-parse -q --verify "refs/heads/${BRANCH}" >/dev/null; then
  git fetch -q origin "$BRANCH" 2>/dev/null || true
  git rev-parse -q --verify "refs/remotes/origin/${BRANCH}" >/dev/null \
    || fail "本地与 origin 都没有分支 ${BRANCH}。"
  git branch -q --track "$BRANCH" "origin/${BRANCH}" || fail "从 origin/${BRANCH} 创建本地分支失败。"
  echo "== 本地没有 ${BRANCH}，已从 origin/${BRANCH} 创建 =="
fi

# 用普通 checkout：它会拒绝覆盖本地改动，这是最后一道数据安全网，绝不用 -f
git checkout -q "$BRANCH" 2>/dev/null \
  || fail "git 拒绝切换——工作区有会被覆盖的本地改动。请先提交或 stash（本脚本绝不用 -f 强切）。"

printf '%s\n' "$ORIG" > "$ORIG_FILE"
echo "== 已从「${ORIG}」切到「${BRANCH}」（流程结束时由 publish.sh 切回） =="
