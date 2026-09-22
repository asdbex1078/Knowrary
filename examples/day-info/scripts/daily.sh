#!/usr/bin/env bash
# Day-Info 每日收集：采集入池 + 本地提交 +（凭证/密钥就绪时）推送。
# 供「Day-Info 每日收集（静默入池）」定时任务调用；也可手动执行。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# 先切到 day-info 再采集：collect.py 读写 day-info/state.json（45 天 URL 去重台账），
# 在落后的分支上跑会读到过期台账、去重静默失效。此时工作区还干净，切换最稳。
# 切回原分支由流程最后一步 publish.sh 负责。
bash day-info/scripts/ensure-branch.sh

python3 day-info/scripts/collect.py --repo-dir "$ROOT"
bash day-info/scripts/publish.sh "day-info: 收集池 $(date +%F)"
