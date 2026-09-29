#!/usr/bin/env python3
"""待审边审核的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_pending.py

2026-09-29：导入时模型没把握的边进了 `.knowrary/pending.json`，以前没有任何地方读它。
这里验两个入口都通：活动栏「欠账」（带角标，按文章分组、可整批）和检查器「关系」（按节点列）。
每一条能改类型、换方向再采纳；驳回留一笔、能撤销；已经连上的清掉、端点不在的只能驳回。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_canvas as E  # noqa: E402  (壳子全在这儿：free_port / launch_chrome / Page / get)

ORIGIN = {"source": "某文章", "imported_at": "2026-09-20", "article": "articles/某文章.md"}


def seed(vault: Path) -> None:
    (vault / "articles").mkdir()
    (vault / "articles" / "某文章.md").write_text("# 某文章\n\n正文\n", "utf-8")

    def edge(s, r, t, conf=0.5):
        return {"id": f"{s}->{t}#{r}", "source": s, "relation": r, "target": t, "year": None,
                "note": f"模型觉得{s}{r}{t}", "confidence": conf, "status": "pending", "origin": ORIGIN,
                "created_at": "2026-09-20T00:00:00Z"}
    doc = {"schema_version": 1, "updated_at": None, "homes": [], "rejected": [], "edges": [
        edge("乙", "依赖", "丙"),               # ok：改成「对比」、换方向再采纳
        edge("甲", "部件", "乙"),               # linked：md 里本来就有
        edge("丙", "相关", "不存在的点", 0.3),  # missing：只能驳回
        edge("丁", "相关", "甲", 0.7),          # ok：在检查器里驳回
    ]}
    (vault / ".knowrary" / "pending.json").write_text(json.dumps(doc, ensure_ascii=False), "utf-8")


# 只数待审的：「已驳回」那一栏的行也带 data-pending，混进来的话「等它回到待审」会被那一行直接满足
ROWS = """JSON.stringify([...document.querySelectorAll('aside [data-section="pending"] .pend-row:not(.rejected)')].map((r) => ({
  id: r.dataset.pending, text: r.textContent.replace(/\\s+/g, ''),
  acts: [...r.querySelectorAll('.pend-acts .btn')].map((b) => b.textContent.trim()) })))"""
BADGE = """[...document.querySelectorAll('.rail .rail-btn')].find((b) => (b.dataset.tip || '').includes('欠账'))
  ?.querySelector('.badge')?.textContent.trim() || ''"""


async def rows(page) -> dict:
    return {r["id"]: r for r in json.loads(await page.ev(ROWS) or "[]")}


async def row_eval(page, rid: str, js: str) -> str:
    return await page.ev(f"""(() => {{ const r = document.querySelector('aside [data-pending={json.dumps(rid)}]');
      if (!r) return 'missing'; {js}; return 'ok'; }})()""")


async def case_digest(page, ck, vault: Path) -> None:
    badge = await E.poll(page, BADGE, lambda v: v == "4", timeout=8)
    ck.add("活动栏「欠账」角标是待审边的条数", badge == "4", str(badge))
    await E.open_rail(page, "欠账")
    got = await E.poll(page, ROWS, lambda v: v and json.loads(v) and len(json.loads(v)) == 4, timeout=10)
    got = {r["id"]: r for r in json.loads(got or "[]")}
    head = await page.ev("document.querySelector('aside .pend-group-head')?.textContent.replace(/\\s+/g, '') || ''")
    ck.add("按出自哪篇分组", "《某文章》·4条" in head, head)
    ok, linked, gone = got.get("乙->丙#依赖", {}), got.get("甲->乙#部件", {}), got.get("丙->不存在的点#相关", {})
    ck.add("能采纳的给「采纳」和「驳回」", ok.get("acts") == ["采纳", "驳回"] and "50%" in ok.get("text", "")
           and "模型觉得乙依赖丙" in ok.get("text", ""), str(ok))
    ck.add("md 里已经有的标「已经连上了」、给「清掉」", "已经连上了" in linked.get("text", "")
           and linked.get("acts") == ["清掉", "驳回"], str(linked))
    ck.add("端点不在的标出来、只能驳回", "端点不在了" in gone.get("text", "") and gone.get("acts") == ["驳回"], str(gone))

    # 改类型 + 换方向再采纳
    await row_eval(page, "乙->丙#依赖", """const s = r.querySelector('select'); s.value = '对比';
      s.dispatchEvent(new Event('change', { bubbles: true })); r.querySelector('.pend-edge .icon-btn').click()""")
    await asyncio.sleep(0.3)
    txt = (await rows(page))["乙->丙#依赖"]["text"]
    ck.add("改了类型、换了方向，卡上看得出来", txt.startswith("丙") and "改过" in txt, txt)
    await row_eval(page, "乙->丙#依赖", "r.querySelector('[data-act=\"adopt\"]').click()")
    await E.poll(page, ROWS, lambda v: v and "乙->丙#依赖" not in v, timeout=10)
    c_md = (vault / "nodes/组B/丙.md").read_text("utf-8")
    ck.add("采纳写进了 md：换方向后写在丙上、类型是改过的，不带原来那句备注",
           "- 对比:: [[乙]]\n" in c_md and "模型觉得" not in c_md, c_md[-120:])
    ck.add("原来的方向没写", "依赖:: [[丙]]" not in (vault / "nodes/组A/乙.md").read_text("utf-8"), "")
    badge = await E.poll(page, BADGE, lambda v: v == "3", timeout=6)
    ck.add("角标跟着减", badge == "3", str(badge))

    await row_eval(page, "甲->乙#部件", "r.querySelector('[data-act=\"clear\"]').click()")
    await row_eval(page, "丙->不存在的点#相关", "r.querySelector('[data-act=\"reject\"]').click()")
    await E.poll(page, ROWS, lambda v: v and "甲->乙#部件" not in v and "不存在的点" not in v, timeout=10)
    await E.click_text(page, "aside .pend-toggle", "已驳回")
    await asyncio.sleep(0.3)
    rej = await page.ev("""JSON.stringify([...document.querySelectorAll('aside .pend-row.rejected')].map((r) => r.dataset.pending))""")
    ck.add("驳回的挪进「已驳回」，能看到", json.loads(rej or "[]") == ["丙->不存在的点#相关"], str(rej))
    doc = json.loads((vault / ".knowrary" / "pending.json").read_text("utf-8"))
    ck.add("pending.json：清掉的没了、驳回的留了一笔", [e["id"] for e in doc["edges"]] == ["丁->甲#相关"]
           and [e["id"] for e in doc["rejected"]] == ["丙->不存在的点#相关"], str(doc)[:200])

    await page.ev("""document.querySelector('aside .pend-row.rejected .pend-acts .btn')?.click()""")
    await E.poll(page, ROWS, lambda v: v and "不存在的点" in v, timeout=8)
    doc = json.loads((vault / ".knowrary" / "pending.json").read_text("utf-8"))
    ck.add("撤销驳回：回到待审", sorted(e["id"] for e in doc["edges"]) == ["丁->甲#相关", "丙->不存在的点#相关"]
           and doc["rejected"] == [], str(doc)[:200])

    await page.ev("""document.querySelector('aside .pend-meta a')?.click()""")
    title = await E.poll(page, "document.querySelector('aside .art-title')?.textContent || ''", lambda v: bool(v), timeout=8)
    ck.add("点《某文章》在应用里读原文", title == "某文章", str(title))
    await page.ev("""document.querySelector('aside .icon-btn[title*="收起"]')?.click()""")
    await asyncio.sleep(0.3)


async def case_inspector(page, ck, vault: Path) -> None:
    await page.ev("""(() => { const i = document.querySelector('.search input');
      i.value = '丁'; i.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    await E.poll(page, "document.querySelectorAll('.search .hits li').length", lambda v: (v or 0) > 0)
    await page.ev("""document.querySelector('.search .hits li').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))""")
    hint = await E.poll(page, "document.querySelector('.insp-pend-hint')?.textContent.trim() || ''", bool, timeout=8)
    ck.add("详情页提醒这个点有待审的边", "1 条待审" in (hint or ""), str(hint))
    await page.ev("document.querySelector('.insp-pend-hint').click()")
    got = await E.poll(page, """[...document.querySelectorAll('.insp [data-section="pending"] .pend-row')].map((r) => r.dataset.pending).join(',')""",
                       bool, timeout=6)
    ck.add("「关系」里列出和它有关的待审边", got == "丁->甲#相关", str(got))
    await page.ev("""document.querySelector('.insp [data-section="pending"] [data-act="reject"]').click()""")
    gone = await E.poll(page, "document.querySelectorAll('.insp [data-section=\"pending\"] .pend-row').length",
                        lambda v: v == 0, timeout=8)
    doc = json.loads((vault / ".knowrary" / "pending.json").read_text("utf-8"))
    ck.add("在检查器里驳回也生效", gone == 0 and "丁->甲#相关" in [e["id"] for e in doc["rejected"]], str(doc)[:160])


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
            await case_digest(page, ck, vault)
            await case_inspector(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-pending-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        seed(vault)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home")}
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
