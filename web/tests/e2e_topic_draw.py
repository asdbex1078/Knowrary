#!/usr/bin/env python3
"""专题画布的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_topic_draw.py

2026-10-13：围绕一个主题自己画总结图（照 draw.io）。顶栏「专题」→ 建一个 → 从左边图形栏把大框、矩形
拖进画布（松手点在大框里就挂上去）、改字和悬停说明、右键换形状、搜知识点拖进来、右键「从这里连线」、
删、撤销、刷新还在；全局图上不出现手画的东西。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_canvas as E  # noqa: E402
from e2e_chat_sessions import settle  # noqa: E402

TOPIC = "文件系统"


class FakeModel(BaseHTTPRequestHandler):
    """假的模型服务（openai 兼容）：审图 → 从提示词里认出真的框 id，交回两条意见；问该画什么 → 一段 Markdown。"""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        if "审稿人" in prompt:
            rect = re.search(r"\[(\w+)\] \w+「引导块」", prompt).group(1)
            frame = re.search(r"\[(\w+)\] frame", prompt).group(1)
            content = json.dumps({"summary": "少了超级块", "items": [
                {"kind": "fact", "target": rect, "say": "引导块是一个盘块", "fix": {"field": "tip", "value": "1 个盘块（1024 字节）"}},
                {"kind": "missing", "target": frame, "say": "缺超级块", "fix": {"shape": "rect", "text": "超级块", "tip": "文件系统的总账"}},
                {"kind": "fact", "target": "s不存在", "say": "指不到", "fix": {"field": "text", "value": "x"}}]}, ensure_ascii=False)
        else:
            content = "**中间**：引导块｜超级块｜逻辑块位图｜inode 位图｜inode｜数据块"
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
    cfg.write_text(json.dumps({"providers": {"fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                                      "model": "fake-m", "api_key": "x"}},
                               "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg
PALETTE = "!!document.querySelector('[data-palette]')"


def topic_layout(api: str) -> dict:
    return E.get(f"{api}/api/layout?layout={urllib.parse.quote('topic:' + TOPIC)}")["layout"]


def shapes_by_text(doc: dict) -> dict:
    return {s.get("text") or f"<{s['kind']}>": (sid, s) for sid, s in (doc.get("shapes") or {}).items()}


def has_cell(cell: str) -> str:
    return f"!!document.querySelector('[data-cell-id=\"{cell}\"]')"


BODY = "el.querySelector('rect, ellipse, polygon, path') || el"


async def tap(page, cell: str) -> None:
    """X6 的 click 是 mousedown + mouseup 合成的。派发到这个 cell 自己的元素上，别被叠在上面的框抢走。"""
    out = await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="{cell}"]');
      if (!el) return 'missing';
      const body = {BODY};
      const r = body.getBoundingClientRect();
      const x = Math.round(r.x + 12), y = Math.round(r.y + r.height - 8);
      for (const t of ['mousedown', 'mouseup']) body.dispatchEvent(new MouseEvent(t, {{ bubbles: true, cancelable: true,
        clientX: x, clientY: y, view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }}));
      return 'ok';
    }})()""")
    assert out == "ok", f"点不到 {cell}：{out}"
    await asyncio.sleep(0.3)


async def drop(page, attr: str, value: str, dx: int = 0, dy: int = 0) -> None:
    """从左边图形栏拖到画布：真的 dragstart（组件自己往 dataTransfer 里写）→ dragover → drop。
    落点 = 画布可见区中心偏 (dx, dy)。"""
    out = await page.ev(f"""(() => {{
      const src = document.querySelector('[{attr}="{value}"]');
      const canvas = document.querySelector('.canvas');
      if (!src || !canvas) return 'missing';
      const dt = new DataTransfer();
      src.dispatchEvent(new DragEvent('dragstart', {{ bubbles: true, dataTransfer: dt }}));
      const r = canvas.getBoundingClientRect();
      const x = r.x + r.width / 2 + {dx}, y = r.y + r.height / 2 + {dy};
      for (const t of ['dragenter', 'dragover', 'drop']) canvas.dispatchEvent(new DragEvent(t, {{ bubbles: true,
        cancelable: true, clientX: x, clientY: y, dataTransfer: dt }}));
      return 'ok';
    }})()""")
    assert out == "ok", f"拖不了 {attr}={value}：{out}"
    await asyncio.sleep(0.4)


async def right_click(page, cell: str, item: str) -> None:
    """在一个 cell 上右键，再点菜单里的某一项。"""
    out = await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="{cell}"]');
      if (!el) return 'missing';
      const body = {BODY};
      const r = body.getBoundingClientRect();
      body.dispatchEvent(new MouseEvent('contextmenu', {{ bubbles: true, cancelable: true, button: 2,
        clientX: Math.round(r.x + 12), clientY: Math.round(r.y + r.height - 8), view: window }}));
      return 'ok';
    }})()""")
    assert out == "ok", f"右键不到 {cell}：{out}"
    await E.poll(page, "!!document.querySelector('.ctx-menu')", bool, timeout=4)
    await E.click_text(page, ".ctx-menu .pop-item", item)
    await asyncio.sleep(0.4)


async def type_into(page, sel: str, text: str) -> None:
    out = await page.ev(f"""(() => {{ const el = document.querySelector({sel!r}); if (!el) return 'missing';
      el.value = {text!r}; el.dispatchEvent(new Event('input', {{ bubbles: true }})); return 'ok'; }})()""")
    assert out == "ok", f"找不到输入框 {sel}"


async def saved() -> None:
    await asyncio.sleep(1.6)                # 补丁 300ms 节流 + 打字 250ms 防抖 + 一次往返


async def case_create(page, ck, vault: Path) -> None:
    await E.click_text(page, ".topbar .seg button", "专题")
    await E.poll(page, "!!document.querySelector('[data-topic-name]')", bool, timeout=6)
    ck.add("还没有专题时点顶栏「专题」：先开目录让你建", True)
    await type_into(page, "[data-topic-name]", TOPIC)
    await page.ev("document.querySelector('[data-act=\"create-topic\"]').click()")
    await E.poll(page, PALETTE, bool, timeout=8)
    ck.add("建好就进那张画布，左边是图形栏", (vault / ".knowrary/topics" / TOPIC / "canvas.json").exists())


async def case_draw(page, ck, api: str) -> dict:
    await drop(page, "data-palette-shape", "frame")
    await drop(page, "data-palette-shape", "rect", dx=-150)      # 松手点落在大框里
    await type_into(page, "[data-shape-text]", "引导块")
    await type_into(page, "[data-shape-tip]", "512 字节，放引导程序")
    await saved()
    got = shapes_by_text(topic_layout(api))
    ck.add("从左边拖进来一个大框、一个矩形；刚放下的直接选中，改字和悬停说明落进画布",
           "<frame>" in got and "引导块" in got and got["引导块"][1]["tip"] == "512 字节，放引导程序", str(got)[:200])
    frame_id, rect_id = got["<frame>"][0], got["引导块"][0]
    ck.add("矩形松手点在大框里：直接挂到大框下面", got["引导块"][1].get("parent") == frame_id, str(got["引导块"][1]))

    await right_click(page, f"shape:{rect_id}", "改成圆柱")
    await saved()
    rect = topic_layout(api)["shapes"][rect_id]
    ck.add("右键「改成圆柱」：换了形状，字和说明不丢", rect["kind"] == "cylinder" and rect["text"] == "引导块", str(rect)[:120])

    before = rect
    await E.drag(page, f"shape:{frame_id}", dx=-120, dy=-60, grab="title")
    await saved()
    after = topic_layout(api)["shapes"][rect_id]
    ck.add("拖大框：里面的小框跟着走，坐标一起记", (after["x"], after["y"]) != (before["x"], before["y"]),
           f"{before['x']},{before['y']} → {after['x']},{after['y']}")
    return {"frame": frame_id, "rect": rect_id}


async def case_tip(page, ck, ids: dict) -> None:
    shown = await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="shape:{ids['rect']}"]');
      const body = {BODY};
      const r = body.getBoundingClientRect();
      for (const t of ['mouseover', 'mouseenter']) body.dispatchEvent(new MouseEvent(t, {{ bubbles: true,
        clientX: r.x + 10, clientY: r.y + 10, view: window }}));
      const tip = document.querySelector('.draw-tip');
      return tip && !tip.hidden ? tip.textContent : '';
    }})()""")
    ck.add("鼠标放上去：浮出悬停说明", shown == "512 字节，放引导程序", repr(shown))


async def case_link(page, ck, api: str, ids: dict) -> None:
    await type_into(page, "[data-palette-search]", "甲")
    await E.poll(page, "!!document.querySelector('[data-palette-node=\"甲\"]')", bool, timeout=4)
    await drop(page, "data-palette-node", "甲", dx=380)
    await E.poll(page, has_cell("甲"), bool, timeout=6)
    ck.add("搜到知识点，拖进画布", "甲" in topic_layout(api)["nodes"], "")

    await right_click(page, f"shape:{ids['rect']}", "从这里连线")
    ck.add("右键「从这里连线」：拉出一条跟着鼠标的虚线", bool(await page.ev(has_cell("link:__preview"))), "")
    await tap(page, "甲")
    await E.poll(page, "!!document.querySelector('[data-link-card]')", bool, timeout=6)
    await type_into(page, "[data-link-text]", "读")
    await saved()
    links = list((topic_layout(api).get("links") or {}).values())
    ck.add("点终点连上：框 → 知识点，线上写字，只存在画布里",
           len(links) == 1 and links[0]["source"] == {"kind": "shape", "id": ids["rect"]}
           and links[0]["target"] == {"kind": "node", "id": "甲"} and links[0]["text"] == "读"
           and not await page.ev(has_cell("link:__preview")), str(links))


async def case_inline_edit(page, ck, api: str, ids: dict) -> None:
    """双击图形原地改字（照 draw.io）：盖一个文本框，Enter 确定。"""
    await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="shape:{ids['frame']}"]');
      const body = {BODY};
      const r = body.getBoundingClientRect();
      body.dispatchEvent(new MouseEvent('dblclick', {{ bubbles: true, cancelable: true,
        clientX: Math.round(r.x + 20), clientY: Math.round(r.y + 14), view: window }}));
    }})()""")
    shown = await E.poll(page, "!!document.querySelector('.draw-inline')", bool, timeout=4)
    ck.add("双击大框：在框上原地出文本框", bool(shown), "")
    await page.ev("""(() => { const a = document.querySelector('.draw-inline');
      a.value = 'Linux 0.12 文件系统';
      a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    await saved()
    text = topic_layout(api)["shapes"][ids["frame"]]["text"]
    ck.add("Enter 确定：字写进大框，文本框收起", text == "Linux 0.12 文件系统"
           and not await page.ev("!!document.querySelector('.draw-inline')"), repr(text))


async def case_upgrade(page, ck, api: str, vault: Path, ids: dict) -> None:
    """小框绑定已有点；大框升级成新知识点：专题中心 + 框里的小框补成「部件」。"""
    await right_click(page, f"shape:{ids['rect']}", "绑定已有知识点")
    await E.poll(page, "!!document.querySelector('[data-bind-dialog]')", bool, timeout=4)
    await type_into(page, "[data-bind-search]", "乙")
    await E.poll(page, "!!document.querySelector('[data-bind-hit=\"乙\"]')", bool, timeout=4)
    await page.ev("document.querySelector('[data-bind-hit=\"乙\"]').click()")
    await saved()
    ck.add("小框绑定已有知识点「乙」（大框还没升级，不写 md）", topic_layout(api)["shapes"][ids["rect"]].get("node") == "乙", "")

    await right_click(page, f"shape:{ids['frame']}", "升级为知识点")
    await E.poll(page, "!!document.querySelector('.node-dialog')", bool, timeout=4)
    name = await page.ev("document.querySelector('.node-dialog input').value")
    ck.add("新建对话框的名字从框上的字预填，空格去掉（名字就是文件名）", name == "Linux0.12文件系统", repr(name))
    await page.ev("""(() => { const i = document.querySelectorAll('.node-dialog input[type="text"], .node-dialog input:not([type])');
      i[1].value = 'Linux 0.12 的磁盘布局'; i[1].dispatchEvent(new Event('input', { bubbles: true })); })()""")
    await asyncio.sleep(0.3)
    await E.click_text(page, ".node-dialog .btn", "创建并升级这个框")
    md = None
    for _ in range(40):
        hits = list(vault.glob("nodes/**/Linux0.12文件系统.md"))
        if hits:
            md = hits[0].read_text("utf-8")
            break
        await asyncio.sleep(0.3)
    ck.add("大框升级：新 md 是这个专题的中心，框里已绑定的「乙」写成部件",
           bool(md) and "专题: 文件系统" in md and "- 部件:: [[乙]]" in md, (md or "没写出来")[-200:])
    await saved()
    ck.add("大框绑定到新点，专题目录认出中心",
           topic_layout(api)["shapes"][ids["frame"]].get("node") == "Linux0.12文件系统"
           and E.get(api + "/api/topics")["topics"][0]["center"] == "Linux0.12文件系统", "")


async def right_click_link(page, item: str) -> None:
    """在画布上唯一那条手画连线上右键，点菜单里某一项。"""
    out = await page.ev("""(() => {
      const el = [...document.querySelectorAll('[data-cell-id^="link:"]')].find((x) => x.getAttribute('data-cell-id') !== 'link:__preview');
      if (!el) return 'missing';
      const path = el.querySelector('path');
      const len = path.getTotalLength(), mid = path.getPointAtLength(len / 2);
      const m = path.getScreenCTM(), x = mid.x * m.a + m.e, y = mid.y * m.d + m.f;
      path.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2, clientX: x, clientY: y, view: window }));
      return 'ok';
    })()""")
    assert out == "ok", f"右键不到连线：{out}"
    await E.poll(page, "!!document.querySelector('.ctx-menu')", bool, timeout=4)
    await E.click_text(page, ".ctx-menu .pop-item", item)
    await asyncio.sleep(0.5)


async def case_relate(page, ck, api: str, vault: Path) -> None:
    """连线写成关系：框（已绑定「乙」）→ 知识点「甲」，选「依赖」写进 乙 的 md；再从 md 删掉。"""
    await right_click_link(page, "写成关系")
    await E.poll(page, "!!document.querySelector('[data-relate-locked]')", bool, timeout=4)
    ck.add("右键「写成关系」：两头由线定，对话框只选类型", True)
    await E.click_text(page, ".rel-dialog .type-chip", "依赖")
    await asyncio.sleep(0.3)
    await page.ev("document.querySelector('[data-act=\"relate-confirm\"]').click()")
    md = vault / "nodes/组A/乙.md"
    for _ in range(40):
        if "依赖:: [[甲]]" in md.read_text("utf-8"):
            break
        await asyncio.sleep(0.3)
    await saved()
    links = list((topic_layout(api).get("links") or {}).values())
    ck.add("写进 乙 的 md；线留着并记住它写成了哪条关系", "- 依赖:: [[甲]]" in md.read_text("utf-8")
           and len(links) == 1 and links[0].get("edge") == "乙->甲#依赖", str(links))

    await right_click_link(page, "从 md 删掉")
    for _ in range(40):
        if "依赖:: [[甲]]" not in md.read_text("utf-8"):
            break
        await asyncio.sleep(0.3)
    await saved()
    ck.add("右键「从 md 删掉这条关系」：md 里那行没了，线也删了",
           "依赖:: [[甲]]" not in md.read_text("utf-8") and not topic_layout(api).get("links"), md.read_text("utf-8")[-80:])


async def case_draft(page, ck, api: str, ids: dict) -> None:
    """侧栏按关系列出还没上图的点（丁 依赖 乙，乙 是中心的部件 → 丁 在「上」）；一键初稿把它摆到中心大框上方。"""
    got = await E.poll(page, "document.querySelector('[data-related=\"up\"][data-palette-node=\"丁\"]') ? 'ok' : ''",
                       bool, timeout=8)
    ck.add("侧栏「上 · 谁在用它」列出 丁（经部件 乙 一跳）", got == "ok", "")
    await page.ev("document.querySelector('[data-act=\"topic-draft\"]').click()")
    await saved()
    doc = topic_layout(api)
    frame = doc["shapes"][ids["frame"]]
    ck.add("一键初稿：丁 摆到中心大框上方，已经摆好的框不动",
           "丁" in doc["nodes"] and doc["nodes"]["丁"]["y"] < frame["y"], str(doc["nodes"].get("丁")))
    left = await page.ev("!!document.querySelector('[data-related][data-palette-node=\"丁\"]')")
    ck.add("摆上之后侧栏里就没有它了", not left, "")


async def case_ai(page, ck, api: str, ids: dict) -> None:
    """审这张图：意见挂在框上（角标），采纳改字 / 补框；问该画什么只给文字。"""
    await page.ev("document.querySelector('[data-act=\"open-topic-ai\"]').click()")
    await E.poll(page, "!!document.querySelector('[data-topic-ai]')", bool, timeout=4)
    await page.ev("document.querySelector('[data-act=\"topic-review\"]').click()")
    n = await E.poll(page, "document.querySelectorAll('[data-review-item]').length", lambda v: (v or 0) >= 2, timeout=15)
    ck.add("审这张图：两条意见摆出来，指不到画布的那条没摆", n == 2, f"{n} 条")
    badge = await page.ev(f"document.querySelector('[data-cell-id=\"shape:{ids['rect']}\"]').textContent")
    ck.add("意见挂在框上：那个框角上有角标", "1" in (badge or ""), repr(badge))
    meta = await page.ev("document.querySelector('[data-topic-ai]').textContent")
    ck.add("面板写明是哪个模型审的", "fake-m" in meta, "")

    await page.ev("document.querySelector('[data-review-item=\"r1\"] [data-act=\"review-adopt\"]').click()")
    await saved()
    rect = topic_layout(api)["shapes"][ids["rect"]]
    ck.add("采纳事实错误：悬停说明改成意见里的写法", rect["tip"] == "1 个盘块（1024 字节）", rect["tip"])
    await page.ev("document.querySelector('[data-review-item=\"r2\"] [data-act=\"review-adopt\"]').click()")
    await saved()
    got = shapes_by_text(topic_layout(api))
    ck.add("采纳「缺了什么」：超级块放进大框", "超级块" in got and got["超级块"][1].get("parent") == ids["frame"], str(got.get("超级块")))
    stored = E.get(f"{api}/api/topics/{urllib.parse.quote(TOPIC)}/review")["review"]["items"]
    ck.add("两条都记成已采纳（刷新还在）", [i["status"] for i in stored] == ["adopted", "adopted"], str([i["status"] for i in stored]))

    await type_into(page, "[data-advice-question]", "中间怎么分块？")
    await page.ev("document.querySelector('[data-act=\"topic-advice\"]').click()")
    ans = await E.poll(page, "document.querySelector('[data-advice=\"0\"]')?.textContent || ''", bool, timeout=15)
    ck.add("问该画什么：只给文字建议", "逻辑块位图" in (ans or "") and "中间怎么分块" in (ans or ""), (ans or "")[:80])
    # 删掉刚补的超级块，后面的用例还按原来两个框算
    await right_click(page, f"shape:{got['超级块'][0]}", "删除")
    await saved()


async def case_delete_undo(page, ck, api: str, ids: dict) -> None:
    await right_click(page, f"shape:{ids['rect']}", "删除")
    await saved()
    doc = topic_layout(api)
    ck.add("右键「删除」：图形删了", ids["rect"] not in doc["shapes"], str(list(doc["shapes"])))
    await E.press(page, "z")
    await asyncio.sleep(2.0)
    doc = topic_layout(api)
    ck.add("⌘Z 撤销：图形回来", ids["rect"] in doc["shapes"],
           f"{list(doc['shapes'])} / {list(doc.get('links') or {})}")


async def case_folders(page, ck, api: str, vault: Path) -> None:
    """专题文件夹（2026-10-14）：建文件夹 → 把专题拖进去 → 右键挪回顶层 → 空文件夹右键删掉。md 不动。"""
    if not await page.ev("!!document.querySelector('[data-act=\"new-folder\"]')"):   # 面板可能还开着：再点一下会关上
        await E.open_rail(page, "专题")
    await E.poll(page, "!!document.querySelector('[data-act=\"new-folder\"]')", bool, timeout=4)
    await page.ev("window.prompt = () => 'AI'; document.querySelector('[data-act=\"new-folder\"]').click()")
    await E.poll(page, "!!document.querySelector('[data-folder=\"AI\"]')", bool, timeout=6)
    ck.add("新建文件夹：专题树里多了「AI」", (vault / ".knowrary/topics/AI/.keep").exists(), "")
    await page.ev(f"""(() => {{
      const dt = new DataTransfer();
      document.querySelector('[data-topic="{TOPIC}"]').dispatchEvent(new DragEvent('dragstart', {{ bubbles: true, dataTransfer: dt }}));
      const f = document.querySelector('[data-folder="AI"]');
      for (const t of ['dragover', 'drop']) f.dispatchEvent(new DragEvent(t, {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
    }})()""")
    await asyncio.sleep(1.0)
    ck.add("把专题拖到文件夹上：挪进去了，画布还读得到", (vault / ".knowrary/topics/AI" / TOPIC / "canvas.json").exists()
           and len(topic_layout(api)["shapes"]) >= 2, "")
    await page.ev(f"""document.querySelector('[data-topic="{TOPIC}"]').dispatchEvent(new MouseEvent('contextmenu',
      {{ bubbles: true, cancelable: true, clientX: 200, clientY: 300 }}))""")
    await E.poll(page, "!!document.querySelector('.ctx-menu')", bool, timeout=4)
    await E.click_text(page, ".ctx-menu .pop-item", "移到：顶层")
    await asyncio.sleep(1.0)
    ck.add("右键「移到：顶层」：挪回来了", (vault / ".knowrary/topics" / TOPIC / "canvas.json").exists(), "")
    await page.ev("""document.querySelector('[data-folder="AI"]').dispatchEvent(new MouseEvent('contextmenu',
      { bubbles: true, cancelable: true, clientX: 200, clientY: 300 }))""")
    await E.poll(page, "!!document.querySelector('.ctx-menu')", bool, timeout=4)
    await E.click_text(page, ".ctx-menu .pop-item", "删除文件夹")
    await asyncio.sleep(1.0)
    ck.add("空文件夹右键删掉", not (vault / ".knowrary/topics/AI").exists(), "")


async def case_reload_and_global(page, ck) -> None:
    await page.call("Page.reload")
    await asyncio.sleep(2.5)
    n = await E.poll(page, "document.querySelectorAll('[data-shape^=\"kg-draw-\"]').length", lambda v: (v or 0) >= 2, timeout=10)
    ck.add("刷新：回到这张专题画布，画的东西都在", (n or 0) >= 2 and bool(await page.ev(PALETTE)), f"{n} 个图形")
    await E.switch_mode(page, "全局图")
    await asyncio.sleep(1.0)
    n = await page.ev("document.querySelectorAll('[data-shape^=\"kg-draw-\"]').length")
    ck.add("全局图上没有手画的东西，也没有图形栏", n == 0 and not await page.ev(PALETTE), f"{n} 个图形")


async def run(api: str, cdp: str, vault: Path, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await settle(page)                       # 首屏：顶栏出来、晨间简报关掉
            await E.switch_mode(page, "全局图")
            await E.wait_render(page, 1)
            await case_create(page, ck, vault)
            ids = await case_draw(page, ck, api)
            await case_tip(page, ck, ids)
            await case_link(page, ck, api, ids)
            await case_inline_edit(page, ck, api, ids)
            await case_upgrade(page, ck, api, vault, ids)
            await case_relate(page, ck, api, vault)
            await case_draft(page, ck, api, ids)
            await case_ai(page, ck, api, ids)
            await case_delete_undo(page, ck, api, ids)
            await case_folders(page, ck, api, vault)
            await case_reload_and_global(page, ck)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-topic-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
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
