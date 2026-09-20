#!/usr/bin/env bash
# 启动本地服务：./server/dev.sh [端口]
# vault 默认取仓库根目录，可用 KNOWRARY_VAULT 覆盖。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8765}"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "缺少 .venv：python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt"; exit 1; }
export KNOWRARY_VAULT="${KNOWRARY_VAULT:-$REPO}"
echo "vault = $KNOWRARY_VAULT"
# 起服务前探一次：在 Claude Code 会话里跑 `claude -p` 会每次调用都失败，
# 而它是零配置的默认 provider —— 不提前说，就要等发出第一句话才炸（见 nested_cli_warning）
"$PY" -c "
import sys; sys.path.insert(0, '$REPO/tools/knowrary')
import llm_backend as b
cfg, _ = b.load_config(__import__('pathlib').Path('$KNOWRARY_VAULT'))
w = b.nested_cli_warning(cfg)
print(w, file=sys.stderr) if w else None
" || true
echo "打开 http://127.0.0.1:$PORT/  （前端产物 web/dist；开发前端用 cd web && npm run dev）"
exec "$PY" -m uvicorn server.app:app --host 127.0.0.1 --port "$PORT" --reload --app-dir "$REPO"
