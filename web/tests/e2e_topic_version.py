#!/usr/bin/env python3
"""专题版本演进（A 方案，2026-10-27）的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_topic_version.py

验这条链路：中心 frontmatter 写 `版本` → 专题画布底部出来一根版本条（小片），默认停最新版；
点某一版，画布上方块按 since/until 现染（区间不覆盖的淡成虚影 kg-ver-ghost、这版新增的描金晕
kg-ver-new），全程不重建 cell；刷新记得上次选的版；在检查器里给没打标的框设「起始版本」能落盘。

单测（web/tests/unit.mjs）已经锁死 versionStateOf 的判定；这里补的是单测覆盖不到的实时路径：
版本条渲染、DOM 染色、下拉打标走补丁落盘。
"""
from __future__ import annotations

import asyncio
import json
import os
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
from e2e_topic_draw import saved, tap  # noqa: E402

TOPIC = "调度"
# 中心节点带版本轴 + 两个部件；甲 / 乙 由 make_vault 已经铺好，绑定得上（不是 stub）
CENTER_MD = ("---\nname: 调度\nfield: 测试\ndesc: 进程调度\n"
             '专题: 调度\n版本: ["0.12", "2.6", "7.2"]\n---\n# 调度\n\n正文\n\n'
             "## 关系\n- 部件:: [[甲]]\n- 部件:: [[乙]]\n")
# s1 绑甲、不打标（全版本常驻）；s2 绑乙、since 2.6（0.12 看不到、2.6 起新增）
CANVAS = {
    "revision": 1,
    "shapes": {
        "s1": {"kind": "rect", "x": 0, "y": 0, "w": 120, "h": 60, "text": "", "node": "甲"},
        "s2": {"kind": "rect", "x": 220, "y": 0, "w": 120, "h": 60, "text": "", "node": "乙", "since": "2.6"},
    },
}


# 甲：正文按版本分小节（第 5 步细节抽屉的料），前言常驻 + 三版各一节 + `## 关系` 原样
PART_MD = ("---\nname: 甲\nfield: 测试\ndesc: 甲\n---\n# 甲\n\n轮转调度，随版本演进。\n\n"
           "## 0.12\n最简单的时间片轮转，就绪队列一条链。\n\n"
           "## 2.6\nO(1) 调度器，140 个优先级位图。\n\n"
           "## 7.2\nCFS，红黑树按 vruntime 排。\n\n## 关系\n- 部件:: [[乙]]\n")


def seed(vault: Path) -> None:
    (vault / "nodes/组A/调度.md").write_text(CENTER_MD, "utf-8")
    (vault / "nodes/组A/甲.md").write_text(PART_MD, "utf-8")      # 覆盖 make_vault 的默认甲，给它分版本正文
    canvas = vault / ".knowrary/topics" / TOPIC / "canvas.json"
    canvas.parent.mkdir(parents=True, exist_ok=True)
    canvas.write_text(json.dumps(CANVAS, ensure_ascii=False), "utf-8")


def topic_layout(api: str) -> dict:
    return E.get(f"{api}/api/layout?layout={urllib.parse.quote('topic:' + TOPIC)}")["layout"]


async def has_class(page, cell: str, cls: str):
    return await page.ev(f"""(() => {{ const el = document.querySelector('[data-cell-id="{cell}"]');
      return el ? el.classList.contains('{cls}') : null; }})()""")


async def open_topic(page, ck) -> None:
    await E.click_text(page, ".topbar .seg button", "专题")
    await E.poll(page, f"!!document.querySelector('[data-topic=\"{TOPIC}\"]')", bool, timeout=6)
    await page.ev(f"document.querySelector('[data-topic=\"{TOPIC}\"]').click()")
    await E.poll(page, "!!document.querySelector('[data-palette]')", bool, timeout=8)
    await E.poll(page, "!!document.querySelector('.ver-bar')", bool, timeout=6)
    chips = await page.ev("[...document.querySelectorAll('.ver-bar .seg button')].map((b) => b.textContent.trim())")
    cur = await page.ev("document.querySelector('.ver-bar .seg button.on')?.textContent?.trim() || ''")
    ck.add("中心写了 `版本` → 画布底部出来版本条，三档都在，默认停最新版",
           chips == ["0.12", "2.6", "7.2"] and cur == "7.2", f"chips={chips} cur={cur}")


async def cur_ver(page) -> str:
    return await page.ev("document.querySelector('.ver-bar .seg button.on')?.textContent?.trim() || ''")


async def case_paint(page, ck) -> None:
    # 默认 7.2：两个框都实显（s2 的 since 已经到了）
    ck.add("最新版 7.2：常驻框和已出现的框都不是虚影",
           not await has_class(page, "shape:s1", "kg-ver-ghost")
           and not await has_class(page, "shape:s2", "kg-ver-ghost"), "")

    await E.click_text(page, ".ver-bar .seg button", "0.12")
    await asyncio.sleep(0.3)
    ck.add("切到 0.12：since 2.6 的框淡成虚影，常驻框不受影响",
           await has_class(page, "shape:s2", "kg-ver-ghost") is True
           and await has_class(page, "shape:s1", "kg-ver-ghost") is False,
           f"s2.ghost={await has_class(page, 'shape:s2', 'kg-ver-ghost')} s1.ghost={await has_class(page, 'shape:s1', 'kg-ver-ghost')}")

    await E.click_text(page, ".ver-bar .seg button", "2.6")
    await asyncio.sleep(0.3)
    ck.add("切到 2.6：那一版正是 s2 新增，描金晕、不再是虚影",
           await has_class(page, "shape:s2", "kg-ver-new") is True
           and await has_class(page, "shape:s2", "kg-ver-ghost") is False,
           f"s2.new={await has_class(page, 'shape:s2', 'kg-ver-new')}")


async def case_persist(page, ck) -> None:
    await page.call("Page.reload")
    await asyncio.sleep(2.5)
    await E.poll(page, "!!document.querySelector('.ver-bar')", bool, timeout=10)
    ck.add("刷新：记得上次选的版（2.6），不是又跳回最新", await cur_ver(page) == "2.6", await cur_ver(page))


async def dbl(page, cell: str) -> None:
    """在一个 cell 上双击（X6 的 node:dblclick 听原生 dblclick）。照 case_inline_edit 的做法，裸派一个 dblclick。"""
    out = await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="{cell}"]');
      if (!el) return 'missing';
      const body = el.querySelector('rect, ellipse, polygon, path') || el;
      const r = body.getBoundingClientRect();
      body.dispatchEvent(new MouseEvent('dblclick', {{ bubbles: true, cancelable: true, view: window,
        clientX: Math.round(r.x + 12), clientY: Math.round(r.y + 12) }}));
      return 'ok';
    }})()""")
    assert out == "ok", f"双击不到 {cell}：{out}"
    await asyncio.sleep(0.4)


async def ver_sections(page):
    return await page.ev("[...document.querySelectorAll('.topic-detail .ver-sec')].map((s) => s.dataset.ver)")


async def on_section(page):
    return await page.ev("document.querySelector('.topic-detail .ver-sec.on')?.dataset?.ver || ''")


async def case_detail(page, ck) -> None:
    # 双击绑了 md 的方块 s1（甲）→ 右侧细节抽屉展开它的正文
    await dbl(page, "shape:s1")
    await E.poll(page, "!!document.querySelector('.topic-detail')", bool, timeout=6)
    title = await page.ev("document.querySelector('.topic-detail .td-title')?.textContent?.trim() || ''")
    secs = await ver_sections(page)
    ck.add("双击绑定方块 → 右侧开细节抽屉，标题是节点名，正文切出三版小节",
           title == "甲" and secs == ["0.12", "2.6", "7.2"], f"title={title} secs={secs}")
    # 这会 topicVer 停在 2.6（上一步刷新后记得的），当前版小节描金高亮
    ck.add("细节抽屉：当前滑块那一版（2.6）的小节高亮（.on）", await on_section(page) == "2.6", await on_section(page))
    intro = await page.ev("document.querySelector('.topic-detail .td-intro')?.textContent?.includes('轮转调度') || false")
    ck.add("细节抽屉：第一个版本标题之前的前言常驻显示", intro is True, "")

    # 滑块联动：切到 7.2，高亮跟着挪到 7.2 小节（不重开抽屉）
    await E.click_text(page, ".ver-bar .seg button", "7.2")
    await asyncio.sleep(0.4)
    ck.add("滑块动 → 细节抽屉高亮跟着挪到 7.2 小节", await on_section(page) == "7.2", await on_section(page))

    # 「看细节」第二条路：选中方块后编辑卡上那个按钮也能开（先 Esc 收起抽屉）
    await page.ev("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    await asyncio.sleep(0.3)
    gone = await page.ev("!document.querySelector('.topic-detail')")
    await tap(page, "shape:s1")
    await E.poll(page, "!!document.querySelector('[data-act=\"open-detail\"]')", bool, timeout=6)
    await page.ev("document.querySelector('[data-act=\"open-detail\"]').click()")
    await E.poll(page, "!!document.querySelector('.topic-detail')", bool, timeout=6)
    ck.add("Esc 收起抽屉；编辑卡「看细节」按钮也能把它开回来",
           gone is True and await page.ev("!!document.querySelector('.topic-detail')") is True, f"esc收起={gone}")


async def case_tag(page, ck, api: str) -> None:
    # 选中没打标的 s1，检查器里出现版本行，设「起始版本 2.6」→ 落盘
    await tap(page, "shape:s1")
    await E.poll(page, "!!document.querySelector('[data-shape-ver]')", bool, timeout=6)
    ck.add("选中框：检查器里出现「版本」行（这个专题开了版本轴）", True)
    await page.ev("""(() => { const s = document.querySelector('[data-shape-since]');
      s.value = '2.6'; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    await saved()
    s1 = topic_layout(api)["shapes"]["s1"]
    ck.add("设「起始版本 2.6」：写进画布的 since（走补丁落盘）", s1.get("since") == "2.6", str(s1)[:120])
    # 当前停在 2.6，s1 这下正是新增版
    ck.add("打标后立即重染：s1 在 2.6 成了新增框", await has_class(page, "shape:s1", "kg-ver-new") is True, "")


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
            await open_topic(page, ck)
            await case_paint(page, ck)
            await case_persist(page, ck)
            await case_tag(page, ck, api)
            await case_detail(page, ck)
        except Exception as exc:
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-ver-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        seed(vault)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
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
