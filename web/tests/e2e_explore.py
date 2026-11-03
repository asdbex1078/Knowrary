#!/usr/bin/env python3
"""探索台的端到端冒烟（2026-10-30）：真无头 Chrome。

    .venv/bin/python web/tests/e2e_explore.py

一个项目 = 一场远征。建一个带「甲 / 乙（已建）+ 幽灵点（没建）」的项目，开探索台，
验证掌握轴被正确呈现：进度条、待点亮（甲 / 乙，建好没考过）、新边疆（幽灵点），以及「模拟面试」按钮。
掌握轴全是服务端现算的，这里只验证前端这扇门读对、分对、画对。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_canvas as E  # noqa: E402
from e2e_chat_sessions import settle  # noqa: E402


class FakeModel(BaseHTTPRequestHandler):
    """假模型：起草大纲（propose）时回一份两点的清单——丙（库里已有）+ 泛型（还没建）。"""

    def do_POST(self):  # noqa: N802
        _ = self.rfile.read(int(self.headers["Content-Length"]))
        content = json.dumps({"field": "测试", "stages": [{"name": "阶段一", "points": [
            {"id": "丙", "name": "丙", "why": "库里已有"},
            {"id": "泛型", "name": "泛型", "why": "还没建"},
        ]}]}, ensure_ascii=False)
        out = json.dumps({"model": "fake-m", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def fake_model(tmp: Path) -> Path:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = tmp / "llm.json"
    cfg.write_text(json.dumps({"providers": {"fake": {"type": "openai",
                               "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "fake-m", "api_key": "x"}},
                               "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg

PROJECTS = {
    "schema_version": 2,
    "revision": 1,
    "projects": {
        "p1": {
            "name": "测试远征",
            "field": "测试",
            "level": "会用",
            "lists": [{
                "kind": "学习",
                "name": "主线",
                "stages": [{
                    "name": "阶段一",
                    "points": [
                        {"id": "甲", "name": "甲"},
                        {"id": "乙", "name": "乙"},
                        {"id": "幽灵点", "name": "还没建的点"},
                    ],
                }],
            }],
        },
    },
}


async def run(api: str, cdp: str, vault: Path, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await settle(page)
            # 选中项目（远征），并进对话模式——探索台在项目作用域才给
            await page.ev("""(() => { const s = document.querySelector('.proj-switch');
              s.value = 'p1'; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
            await asyncio.sleep(0.6)
            await E.click_text(page, ".topbar .seg button", "对话")
            await asyncio.sleep(0.4)

            has_rail = await E.poll(page, "!!document.querySelector('[data-rail=\"explore\"]')", bool, timeout=6)
            ck.add("项目作用域下，活动栏出现「探索」入口", bool(has_rail), "")

            await page.ev("document.querySelector('[data-rail=\"explore\"]').click()")
            await E.poll(page, "!!document.querySelector('.exp-progress')", bool, timeout=6)
            ck.add("开探索台：进度区出来了", bool(await page.ev("!!document.querySelector('.exp-progress')")), "")

            # 甲 / 乙 已建、没考过 → 待点亮；幽灵点没建 → 新边疆
            zones = await page.ev("""(() => {
              const pick = (sel) => [...document.querySelectorAll(sel)].map(b => b.textContent.trim());
              return JSON.stringify({
                untested: pick('.exp-zone.untested .exp-chip'),
                frontier: pick('.exp-zone.frontier .exp-chip'),
                interview: !!document.querySelector('.exp-interview'),
                interviewOff: document.querySelector('.exp-interview')?.disabled || false,
              });
            })()""")
            z = json.loads(zones)
            ck.add("待点亮区 = 已建没考过的甲、乙", sorted(z["untested"]) == ["乙", "甲"], str(z["untested"]))
            ck.add("新边疆区 = 还没建的幽灵点", z["frontier"] == ["还没建的点"], str(z["frontier"]))
            ck.add("模拟面试按钮在、且可点（有已建的点）", z["interview"] and not z["interviewOff"], str(z))

            # 今日探索卡 + 连续天数 + 达成仪式（新库没活动：streak 0；没点亮：不达成）
            feed = json.loads(await page.ev("""JSON.stringify({
              clues: [...document.querySelectorAll('.exp-clue .exp-clue-name')].map(x => x.textContent.trim()),
              streak: (document.querySelector('.exp-streak')||{}).textContent || '',
              done: !!document.querySelector('.exp-done'),
            })"""))
            ck.add("今日探索：端出线索卡（含新边疆「还没建的点」）",
                   len(feed["clues"]) >= 1 and "还没建的点" in feed["clues"], str(feed["clues"]))
            ck.add("连续天数显示，新库是 0 天", "连续" in feed["streak"] and "0" in feed["streak"], feed["streak"].strip())
            ck.add("没点亮 → 不显示达成仪式", feed["done"] is False, str(feed["done"]))

            # 远征大纲：模型起草 → 勾选 → 采纳成地图
            await page.ev("document.querySelector('.exp-expand').click()")   # 让模型补充大纲
            await E.poll(page, "!!document.querySelector('.exp-draft')", bool, timeout=8)
            draft_pts = await page.ev("""JSON.stringify([...document.querySelectorAll('.exp-draft .exp-chip')].map(b => b.textContent.replace('✓','').trim()))""")
            ck.add("模型起草的大纲出来了（丙、泛型）", sorted(json.loads(draft_pts)) == ["丙", "泛型"], draft_pts)

            await E.click_text(page, ".exp-draft-acts .btn", "采纳")
            await asyncio.sleep(1.8)                          # 存盘 + 现算进度回来
            after = await page.ev("""(() => {
              const pick = (sel) => [...document.querySelectorAll(sel)].map(b => b.textContent.trim());
              return JSON.stringify({ untested: pick('.exp-zone.untested .exp-chip'),
                                      frontier: pick('.exp-zone.frontier .exp-chip') });
            })()""")
            a = json.loads(after)
            ck.add("采纳后：丙进了待点亮（库里已有）", "丙" in a["untested"], str(a["untested"]))
            ck.add("采纳后：泛型进了新边疆（还没建）", "泛型" in a["frontier"], str(a["frontier"]))
            # 落盘了（projects.json 里有泛型）
            saved = json.loads((vault / ".knowrary" / "projects.json").read_text("utf-8"))
            all_ids = [p["id"] for ls in saved["projects"]["p1"]["lists"] for st in ls["stages"] for p in st["points"]]
            ck.add("大纲写进了 projects.json", "泛型" in all_ids and "丙" in all_ids, str(all_ids))

            # —— 阈值每个远征各自设：进度条上有达成线 + 可调控件，拖一下落进 project.done_at ——
            ck.add("进度条上画了达成线标记", bool(await page.ev("!!document.querySelector('.exp-bar-line')")), "")
            ck.add("阈值 range 控件在", bool(await page.ev("!!document.querySelector('.exp-thresh input[type=range]')")), "")
            await page.ev("""(() => { const r = document.querySelector('.exp-thresh input[type=range]');
              r.value = '60'; r.dispatchEvent(new Event('change', { bubbles: true })); })()""")
            await asyncio.sleep(1.2)                           # 存盘
            saved2 = json.loads((vault / ".knowrary" / "projects.json").read_text("utf-8"))
            ck.add("阈值落进这场远征的 done_at（0.6，每个远征各自定）",
                   abs(saved2["projects"]["p1"].get("done_at", 0.8) - 0.6) < 1e-6,
                   str(saved2["projects"]["p1"].get("done_at")))

            # —— 游戏皮肤总开关：关掉 → 探索台换平实措辞（结构不变，心魔→易忘、点亮→已掌握）——
            await page.ev("""fetch('/api/settings', { method:'PUT', headers:{'content-type':'application/json'},
              body: JSON.stringify({ game_skin: false }) })""")
            await asyncio.sleep(0.5)
            await page.call("Page.enable")
            await page.call("Page.reload")
            await asyncio.sleep(3)
            await page.call("Runtime.enable")
            await settle(page)
            await page.ev("""(() => { const s = document.querySelector('.proj-switch');
              s.value = 'p1'; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
            await asyncio.sleep(0.6)
            await E.click_text(page, ".topbar .seg button", "对话")
            await asyncio.sleep(0.4)
            await page.ev("document.querySelector('[data-rail=\"explore\"]').click()")
            await E.poll(page, "!!document.querySelector('.exp-progress')", bool, timeout=6)
            titles = await page.ev("""JSON.stringify([...document.querySelectorAll('.exp-zone-title')].map(x=>x.textContent.trim()))""")
            tt = json.loads(titles)
            joined = " ".join(tt)
            ck.add("平实皮肤：出现「待检验」，不再有游戏词「待点亮/心魔」",
                   "待检验" in joined and "待点亮" not in joined and "心魔" not in joined, str(tt))
        finally:
            results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-explore-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        (vault / ".knowrary" / "projects.json").write_text(json.dumps(PROJECTS, ensure_ascii=False), "utf-8")
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "KNOWRARY_LLM_CONFIG": str(fake_model(tmp)),
               "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
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
