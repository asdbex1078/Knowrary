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
REVISES: list = []                   # 假写手被问了几次（「按意见修改」）
FIXED = "已按意见改成2017"


class FakeReview(BaseHTTPRequestHandler):
    """假的 OpenAI 兼容接口，review 和 learn 两个角色都是它。等 2 秒再答——「审核中 · 已等 N 秒」要看得见。
    审核：正文里带 WARNME 的给一条意见，其余放行。改稿（提示词里有「写稿人」）：交回去掉 WARNME 的丙。"""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][0]["content"]
        time.sleep(2)
        if "写稿人" in prompt and "VAGUEME" in prompt:
            # 改坏了：交回一个新建已存在节点的改法，写回校验必然拒——看卡上怎么报
            REVISES.append(1)
            return self._answer({"changes": [{"type": "create_node", "source": "甲", "path": "nodes/组A/甲.md",
                                              "fields": {"name": "甲", "field": "测试", "desc": "d"}}],
                                 "summary": "瞎改", "skipped": []})
        if "写稿人" in prompt:           # 「按意见修改」的提示词：交回改好的丙
            REVISES.append(1)
            return self._answer({"changes": [{"type": "update_body", "source": "丙", "body": LONG + FIXED}],
                                 "summary": "年份改成 2017", "skipped": []})
        AUDITS.append(1)
        if "VAGUEME" in prompt:        # 一条引得到原句的、一条空泛的（2026-09-24 qwen-max 那种）
            return self._answer({"verdict": "warn", "summary": "有两处", "issues": [
                {"path": "nodes/组A/乙.md", "severity": "warn", "quote": "VAGUEME 这一句写错了年份",
                 "what": "年份错了", "why": "原文是 2017", "fix": "这一句写成 2017 年"},
                {"path": "nodes/组A/乙.md", "severity": "warn", "what": "关系描述不够清晰",
                 "why": "还有其他重要差异", "fix": "建议更详细地解释"}]})
        warn = "WARNME" in prompt
        verdict = {"verdict": "warn" if warn else "pass", "summary": "年份再核一下" if warn else "没问题",
                   "issues": [{"path": "nodes/组B/丙.md", "severity": "warn", "quote": "这是一段足够长的正文",
                               "what": "年份存疑",
                               "why": "原文是 2017", "fix": "改成 2017"}] if warn else []}
        self._answer(verdict)

    def _answer(self, data: dict):
        out = json.dumps({"model": "fake-review", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}).encode()
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
        "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
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


S3 = "2026-09-22T10:00:00+08:00"      # 比 s2 还晚：刷新后默认接着这一段


def seed_stale_session(vault: Path) -> None:
    """pa 线上第三段：一张会被审出一条空泛意见的卡（乙），一张页面加载后在「别处」被写入的卡（丁）。"""
    def card(cid, change, path):
        return {"type": "card", "card": {"card_id": cid, "into": None, "changes": [change],
                                         "files": [{"path": path, "notes": [], "diff": ""}]}}
    cards = [card("c-vague", {"type": "update_body", "source": "乙", "body": LONG + "VAGUEME 这一句写错了年份"},
                  "nodes/组A/乙.md"),
             card("c-stale", {"type": "append_body", "source": "丁", "body": "补一句"}, "nodes/组B/丁.md")]
    rows = [{"ts": S3, "role": "user", "text": "再改两处", "session": "s3"},
            {"ts": S3, "role": "assistant", "text": "两张卡", "session": "s3", "cards": cards}]
    with (vault / ".knowrary/chat/pa/2026-09.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


async def stale_scenarios(page, ck, vault: Path) -> None:
    """2026-09-24 乙那次：页面上是旧状态的卡、空泛的意见、改坏了的修改——三件事各看一眼。"""
    seed_stale_session(vault)
    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "document.querySelectorAll('.change-card').length", lambda v: v == 2, timeout=8)

    # 页面加载之后，丁那张在「别的标签页」写进去了：这边的卡还显示着按钮
    with (vault / ".knowrary/cards.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": S3, "id": "c-stale", "event": "applied"}) + "\n")
    before = (vault / "nodes/组B/丁.md").read_text("utf-8")
    calls = len(AUDITS)
    assert await click_btn(page, "丁", "审核") == "ok"
    await E.poll(page, "[...document.querySelectorAll('.change-card')].find((c) => c.textContent.includes('丁'))"
                 "?.classList.contains('folded')", lambda v: v, timeout=6)
    st = await card_state(page, "丁")
    ck.add("旧状态的卡一点就对齐成已写入、折起来", st.get("folded") and "已写入" in st.get("head", ""), str(st))
    ck.add("对齐时没去问模型、也没重复写", len(AUDITS) == calls
           and (vault / "nodes/组B/丁.md").read_text("utf-8") == before, f"calls {calls}->{len(AUDITS)}")

    # 乙：审出一条引得到原句的、一条空泛的
    assert await click_btn(page, "乙", "审核") == "ok"
    await E.poll(page, "!!document.querySelector('.change-card [data-vague]')", lambda v: v, timeout=10)
    picks = await page.ev("""JSON.stringify([...document.querySelectorAll('.change-card .cc-audit-list li')].map((li) =>
      [li.textContent.includes('没指明在哪'), li.querySelector('.cc-audit-pick')?.checked, li.textContent.includes('原句')]))""")
    picks = json.loads(picks or "[]")
    ck.add("空泛的意见标「没指明在哪」、默认不勾；引得到的带原句、默认勾上",
           picks == [[False, True, True], [True, False, False]], str(picks))
    st = await card_state(page, "乙")
    ck.add("按意见修改只算勾上的那 1 条", "按意见修改（1 条）" in st.get("btns", []), str(st))

    body = (vault / "nodes/组A/乙.md").read_text("utf-8")
    assert await click_btn(page, "乙", "按意见修改（1 条）") == "ok"
    await E.poll(page, "!!document.querySelector('.change-card [data-revise-error]')", lambda v: v, timeout=10)
    err = await page.ev("document.querySelector('.change-card [data-revise-error]').textContent.replace(/\\s+/g, '')")
    ck.add("改坏了的原因挂在卡上：谁改的、多久、卡在哪一步", "按意见修改没成" in err and "fake-review" in err
           and "已经存在" in err, err)
    await asyncio.sleep(6)             # toast 早就没了，卡上的还在
    still = await page.ev("!!document.querySelector('.change-card [data-revise-error]')")
    ck.add("失败原因不会过几秒就消失", bool(still), str(still))
    ck.add("改坏了卡和文件都没动", (vault / "nodes/组A/乙.md").read_text("utf-8") == body, "")


async def card_state(page, head: str) -> dict:
    return json.loads(await page.ev(f"""JSON.stringify((() => {{
      const c = [...document.querySelectorAll('.change-card')]
        .find((x) => x.querySelector('.cc-head').textContent.includes({head!r}));
      if (!c) return {{}};
      const txt = (sel) => c.querySelector(sel)?.textContent.replace(/\\s+/g, '') || '';
      const revise = [...c.querySelectorAll('.cc-acts .btn')].find((b) => b.textContent.includes('按意见修改'));
      const busy = [...c.querySelectorAll('.cc-audit.busy')].map((x) => x.textContent.replace(/\\s+/g, '')).join('|');
      return {{ head: c.querySelector('.cc-head').textContent.replace(/\\s+/g, ''),
               panel: c.querySelector('.cc-audit')?.textContent.replace(/\\s+/g, '') || '',
               revised: txt('.cc-revised') + busy, delta: txt('.cc-revised-delta'), diff: txt('.cc-file'),
               primary: c.querySelector('.cc-acts .btn.primary')?.textContent.trim() || '',
               reviseDisabled: !!revise?.disabled,
               btns: [...c.querySelectorAll('.cc-acts .btn')].map((b) => b.textContent.trim()),
               folded: c.classList.contains('folded') }};
    }})())""") or "{}")


async def click_btn(page, head: str, label: str, scope: str = ".cc-acts .btn") -> str:
    return await page.ev(f"""(() => {{
      const c = [...document.querySelectorAll('.change-card')]
        .find((x) => x.querySelector('.cc-head').textContent.includes({head!r}));
      const b = [...(c?.querySelectorAll({scope!r}) || [])].find((x) => x.textContent.trim() === {label!r});
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
    await E.poll(page, "[...document.querySelectorAll('.change-card .cc-acts .btn')].some((b) => b.textContent.trim() === '忽略意见，原样写入')",
                 lambda v: v, timeout=8)
    st = await card_state(page, "丙")
    ck.add("有意见就停在卡上：依据和改法都在", "年份存疑" in st.get("panel", "") and "改成2017" in st.get("panel", ""), str(st))
    ck.add("停下时没写盘", (vault / "nodes/组B/丙.md").read_text("utf-8") == before["丙"], "")
    ck.add("有意见时两条路：按意见修改（主）/ 原样写入；没有多余的「重新审核」",
           st.get("btns") == ["按意见修改（1 条）", "忽略意见，原样写入"] and st.get("primary") == "按意见修改（1 条）", str(st))

    # 意见可以逐条勾：全取消就不给改
    await toggle_pick(page, "丙")
    st = await card_state(page, "丙")
    ck.add("取消勾选后按钮变 0 条且不能点", "按意见修改（0 条）" in st.get("btns", []) and st.get("reviseDisabled"), str(st))
    await toggle_pick(page, "丙")

    await revise_and_wait(page, ck, "丙", first=True)
    st = await card_state(page, "丙")
    ck.add("改完挂在卡上：谁改的、改了什么、改前改后", "AI按意见改过" in st.get("revised", "")
           and "fake-review" in st.get("revised", "") and "年份改成2017" in st.get("revised", "")
           and FIXED in st.get("delta", ""), str(st))
    ck.add("按意见修改不写盘", (vault / "nodes/组B/丙.md").read_text("utf-8") == before["丙"], "")
    ck.add("改完旧结论作废：给「重新审核」和「审核并写入」",
           st.get("btns") == ["重新审核", "审核并写入"] and "改之前的审核意见" in st.get("panel", ""), str(st))
    ck.add("卡上的 diff 换成改后的", FIXED in st.get("diff", ""), str(st))

    # 不刷新、当场撤回：审核结论要认回来
    assert await click_btn(page, "丙", "撤回修改", scope=".cc-revised .btn") == "ok"
    await E.poll(page, "document.querySelectorAll('.change-card .cc-revised').length", lambda v: v == 0, timeout=6)
    await asyncio.sleep(0.8)
    st = await card_state(page, "丙")
    ck.add("当场撤回：回到原样、结论又算数", st.get("btns") == ["按意见修改（1 条）", "忽略意见，原样写入"]
           and FIXED not in st.get("diff", ""), str(st))
    await revise_and_wait(page, ck, "丙")

    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "document.querySelectorAll('.change-card .cc-revised').length", lambda v: v == 1, timeout=8)
    st = await card_state(page, "丙")
    ck.add("刷新后 AI 改的那版还在", FIXED in st.get("delta", "") and FIXED in st.get("diff", ""), str(st))

    assert await click_btn(page, "丙", "撤回修改", scope=".cc-revised .btn") == "ok"
    await E.poll(page, "document.querySelectorAll('.change-card .cc-revised').length", lambda v: v == 0, timeout=6)
    await asyncio.sleep(0.8)
    st = await card_state(page, "丙")
    ck.add("撤回后回到原样：审核结论又算数", st.get("btns") == ["按意见修改（1 条）", "忽略意见，原样写入"]
           and FIXED not in st.get("diff", ""), str(st))

    await revise_and_wait(page, ck, "丙")
    calls = len(AUDITS)
    assert await click_btn(page, "丙", "审核并写入") == "ok"
    await E.poll(page, "document.querySelectorAll('.change-card.folded').length", lambda v: v == 2, timeout=10)
    text = (vault / "nodes/组B/丙.md").read_text("utf-8")
    ck.add("改后写入前重审了一遍，写下去的是改后的版本",
           len(AUDITS) == calls + 1 and FIXED in text and "WARNME" not in text, f"calls {calls}->{len(AUDITS)}")

    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "document.querySelectorAll('.change-card.folded').length", lambda v: v == 2, timeout=8)
    st = await card_state(page, "丙")
    ck.add("刷新后折叠条上是重审的结论", "审核通过" in st.get("head", "") and st.get("folded"), str(st))
    await E.click_text(page, ".change-card.folded .cc-head", "丙")
    await asyncio.sleep(0.3)
    st = await card_state(page, "丙")
    ck.add("展开能看到 AI 改过的记录", "AI按意见改过" in st.get("revised", ""), str(st))


async def toggle_pick(page, head: str) -> None:
    await page.ev(f"""(() => {{
      const c = [...document.querySelectorAll('.change-card')]
        .find((x) => x.querySelector('.cc-head').textContent.includes({head!r}));
      c.querySelector('.cc-audit-pick').click();
    }})()""")
    await asyncio.sleep(0.2)


async def revise_and_wait(page, ck, head: str, first: bool = False) -> None:
    n = len(REVISES)
    assert await click_btn(page, head, "按意见修改（1 条）") == "ok"
    if first:
        await asyncio.sleep(1.2)
        st = await card_state(page, head)
        ck.add("改稿中看得见：谁在改、等了几秒", "learn角色在按1条意见改" in st.get("revised", "")
               and "已等" in st.get("revised", ""), str(st))
    await E.poll(page, "document.querySelectorAll('.change-card .cc-revised').length", lambda v: v == 1, timeout=8)
    assert len(REVISES) == n + 1


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
    await stale_scenarios(page, ck, vault)


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
