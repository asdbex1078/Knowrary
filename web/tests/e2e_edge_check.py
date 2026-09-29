#!/usr/bin/env python3
"""校验已有边的端到端自测：真无头 Chrome + 本地假 review 接口。

    .venv/bin/python web/tests/e2e_edge_check.py

2026-09-29（开发实施计划 §10 #2）：图里已经在的边，review 角色逐条判方向 / 类型 / 成不成立。
两个入口：检查器「校验这个点的关系」、欠账面板「审某个领域」（先算要审几条、分几批，再开始）。
每条问题一键修（换方向 / 改类型 / 删掉，落到 md 原本那一行上）或忽略；审过没改过的不重审。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_canvas as E  # noqa: E402  (壳子全在这儿：free_port / launch_chrome / Page / get)

# 按边 id 给的固定结论：e2e_canvas.FILES 里那几条边
VERDICTS = {"甲->乙#部件": {"verdict": "reverse", "why": "乙才是整体，甲是它的一部分"},
            "丙->甲#依赖": {"verdict": "retype", "suggest": "基于", "why": "丙是在甲的基础上做的，不是运行时依赖"},
            "丁->乙#依赖": {"verdict": "drop", "why": "丁和乙之间没有这层关系"}}
CALLS: list = []


class FakeReview(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][0]["content"]
        CALLS.append(1)
        time.sleep(2)                    # 让进度条看得见
        ids = re.findall(r"^\[(.+?)\]$", prompt, re.M)
        checks = [{"id": i, **VERDICTS.get(i, {"verdict": "ok", "why": ""})} for i in ids]
        out = json.dumps({"model": "fake-review", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps({"checks": checks}, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def fake_review(tmp: Path) -> Path:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeReview)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = tmp / "llm.json"
    cfg.write_text(json.dumps({"providers": {"fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                                      "model": "fake-review", "api_key": "x"}},
                               "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg


ROWS = """(sel) => JSON.stringify([...document.querySelectorAll(sel + ' .edge-check-row')].map((r) => ({
  id: r.dataset.check, text: r.textContent.replace(/\\s+/g, ''),
  acts: [...r.querySelectorAll('.pend-acts .btn')].map((b) => b.dataset.act) })))"""


async def rows(page, scope: str) -> dict:
    got = await page.ev(f"({ROWS})({json.dumps(scope)})")
    return {r["id"]: r for r in json.loads(got or "[]")}


async def pick(page, name: str) -> None:
    await page.ev(f"""(() => {{ const i = document.querySelector('.search input');
      i.value = {json.dumps(name)}; i.dispatchEvent(new Event('input', {{ bubbles: true }})); }})()""")
    await E.poll(page, "document.querySelectorAll('.search .hits li').length", lambda v: (v or 0) > 0)
    await page.ev("document.querySelector('.search .hits li').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))")
    await E.poll(page, "document.querySelector('.insp .node-title')?.textContent || ''", lambda v: name in (v or ""), timeout=8)
    await asyncio.sleep(0.6)


async def case_inspector(page, ck, vault: Path) -> None:
    await pick(page, "甲")
    await E.click_text(page, ".insp .tabs button", "关系")
    await asyncio.sleep(0.3)
    await page.ev("document.querySelector('.insp [data-act=\"check-edges\"]').click()")
    bar = await E.poll(page, "document.querySelector('.insp [data-edge-run]')?.textContent.replace(/\\s+/g, '') || ''",
                       bool, timeout=6)
    ck.add("审的时候看得见：审谁、第几批、等了几秒", "在审「甲」" in (bar or "") and "第1/1批" in (bar or ""), str(bar))
    got = await E.poll(page, f"({ROWS})('.insp')", lambda v: v and len(json.loads(v)) == 2, timeout=10)
    got = {r["id"]: r for r in json.loads(got or "[]")}
    ck.add("这个点的两条问题都挂出来：方向反了、类型不对（带建议）", "方向反了" in got.get("甲->乙#部件", {}).get("text", "")
           and "类型不对→基于" in got.get("丙->甲#依赖", {}).get("text", ""), str(got)[:300])
    ck.add("按结论给修法", got.get("甲->乙#部件", {}).get("acts") == ["reverse", "ignore"]
           and got.get("丙->甲#依赖", {}).get("acts") == ["retype", "ignore"], str(got)[:300])
    ck.add("依据和谁审的也在", "乙才是整体" in got.get("甲->乙#部件", {}).get("text", "")
           and "fake-review" in got.get("甲->乙#部件", {}).get("text", ""), "")

    await page.ev("document.querySelector('.insp [data-check=\"甲->乙#部件\"] [data-act=\"reverse\"]').click()")
    await E.poll(page, "!document.querySelector('.insp [data-check=\"甲->乙#部件\"]')", bool, timeout=10)
    a, b = (vault / "nodes/组A/甲.md").read_text("utf-8"), (vault / "nodes/组A/乙.md").read_text("utf-8")
    ck.add("换方向落到 md：甲那行删掉，乙那头写一行反方向的", "部件:: [[乙]]" not in a and "- 部件:: [[甲]]" in b, b[-80:])


async def case_digest(page, ck, vault: Path) -> None:
    await page.ev("""document.querySelector('aside .icon-btn[title*="收起"]')?.click()""")
    await E.open_rail(page, "欠账")
    await E.poll(page, "!!document.querySelector('aside [data-section=\"edge-check\"]')", bool, timeout=8)
    await page.ev("document.querySelector('aside [data-scope=\"测试\"]').click()")
    plan = await E.poll(page, "document.querySelector('aside [data-edge-plan]')?.textContent.replace(/\\s+/g, '') || ''",
                        bool, timeout=6)
    ck.add("按领域审：先说清要审几条、几次调用、跳过几条审过的", "要审2条" in (plan or "") and "1次review调用" in (plan or "")
           and "1条审过" in (plan or ""), str(plan))
    calls = len(CALLS)
    await page.ev("document.querySelector('aside [data-act=\"start-edge-check\"]').click()")
    got = await E.poll(page, f"({ROWS})('aside')", lambda v: v and "丁->乙#依赖" in v, timeout=12)
    got = {r["id"]: r for r in json.loads(got or "[]")}
    ck.add("只审了没审过的那两条（一次调用）", len(CALLS) == calls + 1, f"calls {calls}->{len(CALLS)}")
    ck.add("「不成立」给「删掉」", got.get("丁->乙#依赖", {}).get("acts") == ["drop", "ignore"], str(got)[:200])

    await page.ev("document.querySelector('aside [data-check=\"丁->乙#依赖\"] [data-act=\"drop\"]').click()")
    await E.poll(page, "!document.querySelector('aside [data-check=\"丁->乙#依赖\"]')", bool, timeout=10)
    ck.add("删掉落到 md", "依赖:: [[乙]]" not in (vault / "nodes/组B/丁.md").read_text("utf-8"), "")
    await page.ev("document.querySelector('aside [data-check=\"丙->甲#依赖\"] [data-act=\"ignore\"]').click()")
    await E.poll(page, "!document.querySelector('aside [data-check=\"丙->甲#依赖\"]')", bool, timeout=8)
    ck.add("忽略的不再挂着，md 没动", "- 依赖:: [[甲]]" in (vault / "nodes/组B/丙.md").read_text("utf-8"), "")

    await page.ev("document.querySelector('aside [data-scope=\"测试\"]').click()")
    plan = await E.poll(page, "document.querySelector('aside [data-edge-plan]')?.textContent.replace(/\\s+/g, '') || ''",
                        bool, timeout=6)
    ck.add("都审过、没改过：不用再审", "不用再审" in (plan or ""), str(plan))


async def run(api: str, cdp: str, vault: Path, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await E.wait_render(page, 4)
            await case_inspector(page, ck, vault)
            await case_digest(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-edgecheck-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "KNOWRARY_LLM_CONFIG": str(fake_review(tmp)), "NO_PROXY": "127.0.0.1,localhost",
               "no_proxy": "127.0.0.1,localhost"}
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            E.wait_for(api + "/api/health")
            chrome, cdp = E.launch_chrome(tmp, api)
            time.sleep(3)
            asyncio.run(run(api, cdp, vault, results))
        finally:
            for proc in (chrome, server):
                if proc:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"  {'✓' if ok else '✗'} {name}" + (f"（{detail}）" if detail else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    sys.exit(1 if failed or not results else 0)


if __name__ == "__main__":
    main()
