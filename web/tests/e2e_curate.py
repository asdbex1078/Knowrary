#!/usr/bin/env python3
"""Curator 说理由的端到端自测：真无头 Chrome + 本地假 review 接口。

    .venv/bin/python web/tests/e2e_curate.py

2026-09-29（开发实施计划 §10 #3）：Inbox 上每条推荐带把握和理由；邻居投票判不出的那一块，
手动点「问 AI 该放哪」，问的时候看得见（谁在想、等了几秒），结论挂在条目上，一键把整块放进它说的分组。
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

CALLS: list = []
ISLAND = "---\nname: {n}\nfield: 没这个框\ndesc: {n} 的摘要\n---\n# {n}\n\n正文\n{rels}"


class FakeReview(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][0]["content"]
        CALLS.append(prompt)
        time.sleep(2)                    # 让「在想」看得见
        hit = re.search(r"^\[(.+?)\] 组B", prompt, re.M) or re.search(r"^\[(.+?)\]", prompt, re.M)
        body = {"group": hit.group(1) if hit else "?", "why": "和丙、丁是同一类东西"}
        out = json.dumps({"model": "fake-review", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]}).encode()
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


ROW = """(id) => { const li = document.querySelector(`aside.inbox [data-inbox="${id}"]`);
  return li ? li.textContent.replace(/\\s+/g, '') : '' }"""


async def row(page, nid: str, ok=bool, timeout: float = 8.0) -> str:
    return await E.poll(page, f"({ROW})({json.dumps(nid)})", ok, timeout=timeout) or ""


async def case_reasons(page, ck, vault: Path) -> None:
    (vault / "nodes/组A/戊.md").write_text(ISLAND.format(n="戊", rels="\n## 关系\n- 部件:: [[甲]]\n")
                                         .replace("没这个框", "测试"), "utf-8")
    (vault / "nodes/新域").mkdir(parents=True, exist_ok=True)
    (vault / "nodes/新域/岛一.md").write_text(ISLAND.format(n="岛一", rels="\n## 关系\n- 对比:: [[岛二]]\n"), "utf-8")
    (vault / "nodes/新域/岛二.md").write_text(ISLAND.format(n="岛二", rels=""), "utf-8")
    await E.use_scope(page, "")
    await E.switch_mode(page, "全局图")
    await E.menu_click(page, "重新加载")
    await asyncio.sleep(1.0)
    if not await page.ev("!!document.querySelector('aside.inbox')"):
        await E.open_rail(page, "Inbox")
    got = await row(page, "戊")
    ck.add("有邻居在画布上的：有把握 + 谁投的票", "有把握" in got and "甲（结构）" in got, got[:120])
    isle = await row(page, "岛一")
    ck.add("整块都没邻居在画布上：判不出，给「问 AI 该放哪（整块 2 个）」",
           "判不出" in isle and "问AI该放哪（整块2个）" in isle, isle[:160])
    ck.add("有把握的不给问 AI 的按钮", not await page.ev(
        "!!document.querySelector('aside.inbox [data-inbox=\"戊\"] [data-act=\"curate\"]')"), "")


async def case_ask(page, ck, vault: Path) -> None:
    await page.ev("document.querySelector('aside.inbox [data-inbox=\"岛一\"] [data-act=\"curate\"]').click()")
    busy = await E.poll(page, "document.querySelector('aside.inbox [data-curating]')?.textContent.replace(/\\s+/g, '') || ''",
                        bool, timeout=4)
    ck.add("问的时候看得见：review 在想这 2 个、等了几秒", "review在想这2个" in (busy or "") and "秒" in (busy or ""), str(busy))
    ck.add("整块的每个条目都在转", await page.ev("document.querySelectorAll('aside.inbox [data-curating]').length") == 2, "")
    ai = await row(page, "岛二", lambda v: "AI建议放" in (v or ""), timeout=12)
    ck.add("结论挂到整块每个条目上：分组 + 理由 + 谁给的", "AI建议放「组B」" in ai and "和丙、丁是同一类东西" in ai
           and "fake-review" in ai, ai[:160])
    prompt = CALLS[-1] if CALLS else ""
    ck.add("给模型看了分组树（带代表成员）和这批点", "组B" in prompt and "丙" in prompt and "「岛一」" in prompt
           and "岛二 的摘要" in prompt, "")
    ck.add("结论存下来了", "curated" in (vault / ".knowrary/pending.json").read_text("utf-8"), "")

    await page.ev("document.querySelector('aside.inbox [data-inbox=\"岛一\"] [data-act=\"place-ai\"]').click()")
    await E.poll(page, "'x'", lambda _: {"岛一", "岛二"} <= set(ck.layout()["nodes"]), timeout=12)
    lay = ck.layout()
    want = lay["nodes"]["丙"]["group"]
    ck.add("一键把整块放进它说的分组", all(lay["nodes"].get(i, {}).get("group") == want for i in ("岛一", "岛二")),
           str({i: lay["nodes"].get(i, {}).get("group") for i in ("岛一", "岛二")}))
    left = await E.poll(page, "document.querySelectorAll('aside.inbox [data-inbox]').length", lambda v: v == 1, timeout=6)
    ck.add("放完不在 Inbox 里了", left == 1, str(left))


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
            await case_reasons(page, ck, vault)
            await case_ask(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-curate-") as tmpdir:
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
