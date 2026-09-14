#!/usr/bin/env bash
# 技术文档发布器：把一个本地 md 文件提交到【固定分支】并推送，与当前 checkout 无关。
#
# 用法：bash tools/tech-doc/publish-doc.sh <源文件> <仓库内目标路径> [提交信息]
# 例：  bash tools/tech-doc/publish-doc.sh /tmp/draft.md "doc/技术文档/2026-09-14-连接池泄漏排查.md"
#
# 环境变量：
#   TECHDOC_BRANCH   目标分支，默认 main
#   TECHDOC_RETRIES  推送重试次数，默认 6（本机 SSH 经代理有 ~37% 抖动，见 day-info/scripts/publish.sh）
#   TECHDOC_NO_PUSH  设为 1 则只建本地提交、不推送
#
# 设计要点：
#   1) 当前 checkout 不在目标分支时，用 hash-object/commit-tree 直接造提交，不切分支、不碰工作区、不碰 index；
#   2) 【推送成功之后】才移动本地 refs/heads/<分支>。顺序反过来的话，一旦远端已前进，
#      本地分支就会和远端分叉，重试反而卡死。先推后落 ref，失败时本地状态干净如初。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BRANCH="${TECHDOC_BRANCH:-main}"
RETRIES="${TECHDOC_RETRIES:-6}"
SRC="${1:-}"
DEST="${2:-}"
MSG="${3:-}"

die() { echo "!! $*" >&2; exit 1; }

[ -n "$SRC" ] && [ -n "$DEST" ] || die "用法：publish-doc.sh <源文件> <仓库内目标路径> [提交信息]"
[ -f "$SRC" ] || die "源文件不存在：$SRC"
case "$DEST" in
  /*|*..*) die "目标路径必须是仓库内相对路径，且不含 ..：$DEST" ;;
esac
MSG="${MSG:-docs: 新增技术文档 $(basename "$DEST" .md)}"

LOCAL_REF="refs/heads/$BRANCH"
REMOTE_REF="refs/remotes/origin/$BRANCH"
CUR_BRANCH="$(git symbolic-ref -q --short HEAD || echo "")"
NO_PUSH="${TECHDOC_NO_PUSH:-0}"

fetch_branch() {
  git fetch -q origin "$BRANCH" 2>/dev/null \
    || echo "== fetch 失败（离线或远端暂无此分支），改用本地 ${BRANCH} 作为基准 =="
}

# 定基：本地 ref 与远端 ref 取更新的那个；分叉则拒绝替用户做决定
resolve_base() {
  local l r
  l="$(git rev-parse -q --verify "$LOCAL_REF" || true)"
  r="$(git rev-parse -q --verify "$REMOTE_REF" || true)"
  if [ -n "$l" ] && [ -n "$r" ]; then
    if   [ "$l" = "$r" ];                        then echo "$l"
    elif git merge-base --is-ancestor "$l" "$r"; then echo "$r"   # 本地落后 → 基于远端
    elif git merge-base --is-ancestor "$r" "$l"; then echo "$l"   # 本地领先 → 基于本地，连带推上去
    else die "本地 ${BRANCH} 与 origin/${BRANCH} 已分叉，请先自行合并，脚本不替你做决定"
    fi
  elif [ -n "$l" ]; then echo "$l"
  elif [ -n "$r" ]; then echo "$r"
  else die "找不到分支 ${BRANCH}（本地和 origin 都没有）"
  fi
}

# 造提交：设置 NEW_COMMIT / BASE_COMMIT。内容无变化时返回 1
build_commit() {
  local base blob tree tmp_index
  base="$(resolve_base)"
  blob="$(git hash-object -w "$SRC")"
  tmp_index="$(mktemp -t techdoc-index)"
  GIT_INDEX_FILE="$tmp_index" git read-tree "$base"
  GIT_INDEX_FILE="$tmp_index" git update-index --add --cacheinfo "100644,$blob,$DEST"
  tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"
  rm -f "$tmp_index"
  if [ "$tree" = "$(git rev-parse "$base^{tree}")" ]; then
    echo "== 内容与 ${BRANCH} 上已有版本完全一致，无需提交 =="
    return 1
  fi
  NEW_COMMIT="$(git commit-tree "$tree" -p "$base" -m "$MSG")"
  BASE_COMMIT="$base"
  return 0
}

# 推送成功后把本地 ref 挪到新提交（失败只告警：远端已经对了，本地下次 fetch 也能追上）
land_local_ref() {
  local prev
  prev="$(git rev-parse -q --verify "$LOCAL_REF" || echo "")"
  git update-ref "$LOCAL_REF" "$NEW_COMMIT" "$prev" 2>/dev/null \
    || echo "== 提醒：本地 ${BRANCH} ref 期间被改动，未同步；远端已是最新，下次 fetch 即可 =="
  git update-ref "$REMOTE_REF" "$NEW_COMMIT" 2>/dev/null || true
}

fetch_branch

# ---------- 分支 A：当前就 checkout 在目标分支 ----------
# 此时若只挪 ref，工作区会与 ref 脱节（新文件被 git status 报成 deleted）。
# 改走「落盘 + 路径受限提交」：只提交这一个路径，工作区其它未提交改动一概不动。
if [ "$CUR_BRANCH" = "$BRANCH" ]; then
  mkdir -p "$(dirname "$ROOT/$DEST")"
  cp "$SRC" "$ROOT/$DEST"
  git add -- "$DEST"
  if git diff --cached --quiet -- "$DEST"; then
    echo "== 内容无变化，无需提交 =="; exit 0
  fi
  git commit -q -m "$MSG" -- "$DEST"
  echo "== 已提交到当前分支 ${BRANCH}：$(git log -1 --format='%h %s') =="
  MODE="worktree"
else
  build_commit || exit 0
  echo "== 已造好提交 ${NEW_COMMIT:0:7}（基于 ${BASE_COMMIT:0:7}），当前分支 ${CUR_BRANCH} 与工作区未受影响 =="
  MODE="plumbing"
fi

if [ "$NO_PUSH" = "1" ]; then
  [ "$MODE" = "plumbing" ] && land_local_ref
  echo "== TECHDOC_NO_PUSH=1，已落到本地 ${BRANCH}，跳过推送 =="
  exit 0
fi

# ---------- 推送 ----------
ERRLOG="$(mktemp -t techdoc-push)"
trap 'rm -f "$ERRLOG"' EXIT

# LC_ALL=C：让 git 用英文报错，下面的非快进识别才稳（中文环境下英文正则会漏判）
push_once() {
  if [ "$MODE" = "worktree" ]; then
    LC_ALL=C git push -q origin "$LOCAL_REF:$LOCAL_REF" 2>>"$ERRLOG"
  else
    LC_ALL=C git push -q origin "$NEW_COMMIT:$LOCAL_REF" 2>>"$ERRLOG"
  fi
}
is_non_ff() { tail -n 8 "$ERRLOG" | grep -qiE 'non-fast-forward|fetch first|\[rejected\]|updates were rejected'; }

attempt=1
rebuilds=0
REBUILD_MAX=3
while [ "$attempt" -le "$RETRIES" ]; do
  if push_once; then
    [ "$MODE" = "plumbing" ] && land_local_ref
    echo "== 已推送 origin/${BRANCH} =="
    echo "== https://github.com/asdbex1078/Knowrary/blob/${BRANCH}/${DEST} =="
    exit 0
  fi

  if is_non_ff; then
    echo "== 远端 ${BRANCH} 已前进，基于最新 origin/${BRANCH} 重造提交… =="
    [ "$MODE" = "worktree" ] \
      && die "当前 checkout 在 ${BRANCH} 且远端已前进，请自行 git pull --rebase 后重跑"
    rebuilds=$(( rebuilds + 1 ))
    [ "$rebuilds" -le "$REBUILD_MAX" ] \
      || die "连续 ${REBUILD_MAX} 次重造仍被拒，停手。请检查 origin/${BRANCH} 状态后重跑"
    PREV_BASE="$BASE_COMMIT"
    fetch_branch
    : > "$ERRLOG"
    build_commit || exit 0        # 重造后内容一致 → 说明别人已把同一篇推上去了
    # 被拒说明远端已前进，但 fetch 拿回来的基准却没变 → 网络/权限问题，再重造多少次都一样
    [ "$BASE_COMMIT" != "$PREV_BASE" ] \
      || die "推送被拒，但 fetch 没拿到新的 origin/${BRANCH}（基准仍是 ${PREV_BASE:0:7}）。请检查网络或推送权限"
    echo "== 已重造：${NEW_COMMIT:0:7}（基于 ${BASE_COMMIT:0:7}）=="
    continue                       # 立刻重推，不必等退避
  fi

  if [ "$attempt" -lt "$RETRIES" ]; then
    wait_s=$(( attempt * 3 )); [ "$wait_s" -gt 15 ] && wait_s=15
    echo "== 第 ${attempt}/${RETRIES} 次推送失败，${wait_s}s 后重试… =="
    sleep "$wait_s"
  fi
  attempt=$(( attempt + 1 ))
done

echo "!! 推送失败，已重试 ${RETRIES} 次。"
if [ "$MODE" = "worktree" ]; then
  echo "!! 提交已在本地 ${BRANCH} 上，不会丢，可稍后重跑本脚本或手动 git push origin ${BRANCH}"
else
  echo "!! 本地分支未被改动（提交对象 ${NEW_COMMIT:0:7} 已在对象库里，不会丢），稍后重跑本脚本即可"
fi
echo "!! 最后一条错误：$(tail -n 1 "$ERRLOG" 2>/dev/null)"
exit 1
