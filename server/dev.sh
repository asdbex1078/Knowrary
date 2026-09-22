#!/usr/bin/env bash
# 启动本地服务：./server/dev.sh [端口]
# 知识库由设置页选定（~/.knowrary/config.json），KNOWRARY_VAULT 可临时覆盖。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8765}"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "缺少 .venv：python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt"; exit 1; }
# 这里**不再默认 export KNOWRARY_VAULT**：设了它就等于把库钉死，设置页上切库不生效
# （2026-09-22 拆分前它默认指向仓库自身，那时仓库就是 vault）。
# 起服务前探一次：在 Claude Code 会话里跑 `claude -p` 会每次调用都失败，
# 而它是零配置的默认 provider —— 不提前说，就要等发出第一句话才炸（见 nested_cli_warning）
"$PY" -c "
import sys; sys.path.insert(0, '$REPO')
from server import vaults
try:
    vault = vaults.current_vault()
except vaults.NoVaultSelected:
    print('还没选知识库：打开页面后在「设置 → 知识库」里选一个目录', file=sys.stderr)
else:
    print(f'vault = {vault}')
    sys.path.insert(0, '$REPO/tools/knowrary')
    import llm_backend as b
    w = b.nested_cli_warning(b.load_config()[0])
    print(w, file=sys.stderr) if w else None
" || true
echo "打开 http://127.0.0.1:$PORT/  （前端产物 web/dist；开发前端用 cd web && npm run dev）"
exec "$PY" -m uvicorn server.app:app --host 127.0.0.1 --port "$PORT" --reload --app-dir "$REPO"
