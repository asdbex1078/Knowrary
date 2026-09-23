#!/usr/bin/env python3
"""对话卡片的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_chat_cards.py

2026-09-23 那次：在 AI 史项目里点了一张改 **Transformer** 清单的卡，页面跟着切去了 Transformer，
对话线整段被换掉；切回来，同一轮里另外三张没点的变更卡全没了——卡只活在前端内存里。
这里验四件事：卡跟着留档回来、点过的折成一行能展开、点别的项目的卡不跳走、换项目再回来卡还在。
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
from e2e_chat_sessions import settle  # noqa: E402

TS = "2026-09-20T10:00:00+08:00"
EDGE = {"type": "add_edge", "source": "甲", "relation": "相关", "target": "乙"}


def seed_projects(api: str) -> None:
    def one(name, points):
        return {"name": name, "lists": [{"kind": "学习", "name": "主线", "goal": name,
                                         "stages": [{"name": "一", "points": points}]}]}
    E.send(api + "/api/projects", {"base_revision": 0, "projects": {
        "pa": one("甲项目", [{"id": "甲", "name": "甲"}]),
        "pb": one("乙项目", [{"id": "乙", "name": "乙"}, {"id": "旧乙", "name": "旧乙"}]),
    }}, method="PUT")


def seed_chat(vault: Path) -> None:
    """pa 线上一段对话：一张没点的变更卡、一张没点的「改 pb 清单」卡、一张已经写过的变更卡。"""
    cards = [
        {"type": "card", "card": {"card_id": "c-edge", "changes": [EDGE], "into": None,
                                  "files": [{"path": "nodes/组A/甲.md", "notes": [], "diff": "+ 摆出来那一刻的旧 diff"}]}},
        {"type": "list_edit", "list_edit": {
            "card_id": "c-list", "project": "pb", "project_name": "乙项目", "list": 0, "list_name": "主线",
            "edits": [{"op": "drop", "id": "旧乙", "name": "旧乙"}], "left": 1, "empties": False}},
        {"type": "card", "card": {"card_id": "c-done", "changes": [], "into": None,
                                  "files": [{"path": "nodes/组B/丙.md", "notes": [], "diff": "+ 早就写过了"}]}},
    ]
    d = vault / ".knowrary" / "chat" / "pa"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": TS, "role": "user", "text": "帮我整理", "session": "s1"},
            {"ts": TS, "role": "assistant", "text": "三张卡", "session": "s1", "cards": cards}]
    (d / "2026-09.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    (vault / ".knowrary" / "cards.jsonl").write_text(
        json.dumps({"ts": TS, "id": "c-done", "event": "applied"}) + "\n", "utf-8")


CARDS = """JSON.stringify([...document.querySelectorAll('.chat .change-card, .change-card')].map((c) => ({
  head: c.querySelector('.cc-head').textContent.replace(/\\s+/g, ''),
  folded: c.classList.contains('folded'),
  diff: !!c.querySelector('.cc-file, ul'),
  act: !!c.querySelector('.cc-acts .btn.primary') })))"""


async def cards(page) -> list:
    return json.loads(await page.ev(CARDS) or "[]")


def pick(got: list, head: str) -> dict:
    """按标题认卡。渲染顺序是项目卡 → 拆点卡 → 清单卡 → 变更卡，不是留档里的顺序。"""
    return next((g for g in got if head in g["head"]), {})


async def click_card_btn(page, head: str) -> str:
    return await page.ev(f"""(() => {{
      const c = [...document.querySelectorAll('.change-card')]
        .find((x) => x.querySelector('.cc-head').textContent.includes({head!r}));
      const b = c?.querySelector('.cc-acts .btn.primary');
      if (!b) return 'missing'; b.click(); return 'ok';
    }})()""")


async def scenarios(page, ck, vault: Path) -> None:
    await settle(page)
    await E.use_scope(page, "pa")
    await E.poll(page, "document.querySelectorAll('.change-card').length", lambda v: v == 3, timeout=8)
    got = await cards(page)
    ck.add("刷新后三张卡都跟着留档回来", len(got) == 3, str(got))
    edge, lst, done = pick(got, "甲"), pick(got, "乙项目"), pick(got, "丙")
    fresh = await page.ev("[...document.querySelectorAll('.cc-diff')].some((d) => d.textContent.includes('相关:: [[乙]]'))")
    ck.add("没点的变更卡整张摆着，diff 按现在的文件重算", not edge.get("folded") and edge.get("act") and fresh,
           f"{edge} fresh={fresh}")
    ck.add("没点的清单卡整张摆着", not lst.get("folded") and lst.get("act"), str(lst))
    ck.add("点过的卡折成一行", done.get("folded") and not done.get("diff") and not done.get("act"), str(done))

    await E.click_text(page, ".change-card.folded .cc-head", "已写入")
    await asyncio.sleep(0.3)
    got = await cards(page)
    ck.add("点折叠条能展开回看", not pick(got, "丙").get("folded") and pick(got, "丙").get("diff"), str(got))

    # 点「改乙项目清单」：写的是 pb，但人在 pa 聊天——不许跳走
    assert await click_card_btn(page, "乙项目") == "ok"
    await E.poll(page, "document.querySelectorAll('.change-card.folded').length", lambda v: v >= 1, timeout=8)
    await asyncio.sleep(0.6)
    scope = await page.ev("document.querySelector('.proj-switch')?.value")
    got = await cards(page)
    ck.add("点别的项目的清单卡不跳项目", scope == "pa", f"scope={scope}")
    ck.add("点完那张折起来，对话和另外两张卡都在",
           len(got) == 3 and pick(got, "乙项目").get("folded") and pick(got, "甲").get("act"), str(got))
    pts = E.get(page.api + "/api/projects")["doc"]["projects"]["pb"]["lists"][0]["stages"][0]["points"]
    ck.add("清单真改了", [p["id"] for p in pts] == ["乙"], str(pts))

    # 换到 pb 再回 pa：卡照样在，刚点的那张是折叠的
    await E.use_scope(page, "pb")
    await asyncio.sleep(0.8)
    await E.use_scope(page, "pa")
    await E.poll(page, "document.querySelectorAll('.change-card').length", lambda v: v == 3, timeout=8)
    got = await cards(page)
    ck.add("换项目再切回来，没点的卡还在", not pick(got, "甲").get("folded") and pick(got, "甲").get("act"), str(got))
    ck.add("刚点过的那张回来是折叠的", pick(got, "乙项目").get("folded"), str(got))

    assert await click_card_btn(page, "提议写入") == "ok"
    await E.poll(page, "document.querySelectorAll('.change-card.folded').length", lambda v: v == 3, timeout=10)
    text = (vault / "nodes/组A/甲.md").read_text("utf-8")
    ck.add("恢复出来的变更卡能写进 md", "相关:: [[乙]]" in text, text[-80:])
    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "document.querySelectorAll('.change-card').length", lambda v: v == 3, timeout=8)
    got = await cards(page)
    ck.add("刷新后三张都是已点、都折叠", len(got) == 3 and all(g["folded"] for g in got), str(got))


async def run(api: str, cdp: str, vault: Path, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await scenarios(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-cards-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        seed_chat(vault)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home")}
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            E.wait_for(api + "/api/health")
            seed_projects(api)
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
