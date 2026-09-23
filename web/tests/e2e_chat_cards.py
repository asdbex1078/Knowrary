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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


LONG = "这是一段足够长的正文，用来绕开「正文太薄」那条确定性检查。" * 8
S2 = "2026-09-21T10:00:00+08:00"      # 比 s1 晚：刷新后默认接着的就是这一段
AUDITS: list = []                    # 假审校被问了几次：「写入时沿用结论、不再问第二遍」靠它验


class FakeReview(BaseHTTPRequestHandler):
    """假的 OpenAI 兼容接口，当 review 角色。等 2 秒再答——「审核中 · 已等 N 秒」要看得见。
    正文里带 WARNME 的给一条意见，其余放行。"""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        AUDITS.append(1)
        time.sleep(2)
        warn = "WARNME" in body["messages"][0]["content"]
        verdict = {"verdict": "warn" if warn else "pass", "summary": "年份再核一下" if warn else "没问题",
                   "issues": [{"path": "nodes/组B/丙.md", "severity": "warn", "what": "年份存疑",
                               "why": "原文是 2017", "fix": "改成 2017"}] if warn else []}
        out = json.dumps({"model": "fake-review", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(verdict, ensure_ascii=False)}}]}).encode()
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
    cfg.write_text(json.dumps({"providers": {
        "cli": {"type": "claude-cli"},
        "fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                 "model": "fake-review", "api_key": "x"}},
        "roles": {"learn": "cli", "review": "fake"}}), "utf-8")
    return cfg


def seed_audit_session(vault: Path) -> None:
    """pa 线上第二段：两张改正文的卡（审核只审正文改动），一张会被提意见、一张直接过。"""
    cards = [{"type": "card", "card": {"card_id": f"c-{nid}", "into": None,
                                       "files": [{"path": f"nodes/组B/{nid}.md", "notes": [], "diff": ""}],
                                       "changes": [{"type": "update_body", "source": nid, "body": body}]}}
             for nid, body in (("丙", LONG + "WARNME"), ("丁", LONG))]
    rows = [{"ts": S2, "role": "user", "text": "改两段正文", "session": "s2"},
            {"ts": S2, "role": "assistant", "text": "两张卡", "session": "s2", "cards": cards}]
    with (vault / ".knowrary/chat/pa/2026-09.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


async def card_state(page, head: str) -> dict:
    return json.loads(await page.ev(f"""JSON.stringify((() => {{
      const c = [...document.querySelectorAll('.change-card')]
        .find((x) => x.querySelector('.cc-head').textContent.includes({head!r}));
      if (!c) return {{}};
      return {{ head: c.querySelector('.cc-head').textContent.replace(/\\s+/g, ''),
               panel: c.querySelector('.cc-audit')?.textContent.replace(/\\s+/g, '') || '',
               btns: [...c.querySelectorAll('.cc-acts .btn')].map((b) => b.textContent.trim()),
               folded: c.classList.contains('folded') }};
    }})())""") or "{}")


async def click_btn(page, head: str, label: str) -> str:
    return await page.ev(f"""(() => {{
      const c = [...document.querySelectorAll('.change-card')]
        .find((x) => x.querySelector('.cc-head').textContent.includes({head!r}));
      const b = [...(c?.querySelectorAll('.cc-acts .btn') || [])].find((x) => x.textContent.trim() === {label!r});
      if (!b) return 'missing'; b.click(); return 'ok';
    }})()""")


async def audit_scenarios(page, ck, vault: Path) -> None:
    """审核开着：先审再写、结论挂在卡上、写入时不再问第二遍、刷新后结论还在。"""
    seed_audit_session(vault)
    E.send(page.api + "/api/settings", {"audit_enabled": True}, method="PUT")
    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "document.querySelectorAll('.change-card').length", lambda v: v == 2, timeout=8)
    before = {n: (vault / p).read_text("utf-8") for n, p in (("丙", "nodes/组B/丙.md"), ("丁", "nodes/组B/丁.md"))}
    st = await card_state(page, "丁")
    ck.add("审核开着时卡上有「审核」和「审核并写入」", st.get("btns", [])[:2] == ["审核", "审核并写入"], str(st))

    assert await click_btn(page, "丁", "审核") == "ok"
    await asyncio.sleep(1.2)
    st = await card_state(page, "丁")
    ck.add("审核中看得见：谁在审、等了几秒", "审核中" in st.get("head", "") and "已等" in st.get("panel", ""), str(st))
    await E.poll(page, "document.querySelector('.change-card .cc-audit:not(.busy)') !== null", lambda v: v, timeout=8)
    st = await card_state(page, "丁")
    ck.add("审完结论挂在卡上（结论 + 模型 + 耗时）",
           "审核通过" in st.get("head", "") and "fake-review" in st.get("panel", "") and "没问题" in st.get("panel", ""),
           str(st))
    ck.add("只审不写", (vault / "nodes/组B/丁.md").read_text("utf-8") == before["丁"], "")
    calls = len(AUDITS)
    t0 = time.time()
    assert await click_btn(page, "丁", "写入") == "ok"
    await E.poll(page, "[...document.querySelectorAll('.change-card')].filter((c) => c.classList.contains('folded')).length",
                 lambda v: v == 1, timeout=6)
    ck.add("审过再写：不再问模型，秒回", len(AUDITS) == calls and time.time() - t0 < 1.8,
           f"calls {calls}->{len(AUDITS)} {time.time() - t0:.1f}s")
    ck.add("丁真写进去了", "WARNME" not in (vault / "nodes/组B/丁.md").read_text("utf-8")
           and (vault / "nodes/组B/丁.md").read_text("utf-8") != before["丁"], "")

    # 没审就点「审核并写入」：先审，有意见就停下给人看
    assert await click_btn(page, "丙", "审核并写入") == "ok"
    await E.poll(page, "[...document.querySelectorAll('.change-card .cc-acts .btn')].some((b) => b.textContent.trim() === '照写')",
                 lambda v: v, timeout=8)
    st = await card_state(page, "丙")
    ck.add("有意见就停在卡上：依据和改法都在", "年份存疑" in st.get("panel", "") and "改成2017" in st.get("panel", ""), str(st))
    ck.add("停下时没写盘", (vault / "nodes/组B/丙.md").read_text("utf-8") == before["丙"], "")
    calls = len(AUDITS)
    assert await click_btn(page, "丙", "照写") == "ok"
    await E.poll(page, "document.querySelectorAll('.change-card.folded').length", lambda v: v == 2, timeout=6)
    ck.add("点「照写」落盘、不再审第二遍", len(AUDITS) == calls and "WARNME" in (vault / "nodes/组B/丙.md").read_text("utf-8"),
           f"calls {calls}->{len(AUDITS)}")

    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "document.querySelectorAll('.change-card.folded').length", lambda v: v == 2, timeout=8)
    st = await card_state(page, "丙")
    ck.add("刷新后折叠条上还有审核结论", "条意见" in st.get("head", "") and st.get("folded"), str(st))
    await E.click_text(page, ".change-card.folded .cc-head", "丙")
    await asyncio.sleep(0.3)
    st = await card_state(page, "丙")
    ck.add("展开能看到当时的审核意见", "年份存疑" in st.get("panel", ""), str(st))


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
    await audit_scenarios(page, ck, vault)


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
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "KNOWRARY_LLM_CONFIG": str(fake_review(tmp)), "NO_PROXY": "127.0.0.1,localhost",
               "no_proxy": "127.0.0.1,localhost"}
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
