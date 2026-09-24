#!/usr/bin/env python3
"""原文阅读视图的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_articles.py

原文层第 4 步。验这几条链路都通：
活动栏「原文」→ 列表（拆出几个点 / 还没拆）→ 读一篇（每节标题旁挂出自这一节的点、画布上点亮它们）
→ 点一个点跳到图上；检查器「来源」→ 在应用里打开并滚到那一节；「还没拆」→ 带进导入面板。
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

FILLER = "\n".join(f"第 {i} 段：凑长度，让「为什么是子词」那一节不在第一屏里。" for i in range(40))
ARTICLE = ("---\nimported: 2026-09-23\norigin: 粘贴\n---\n# BPE 全景\n\n开头一段。\n\n"
           f"## 背景\n\n{FILLER}\n\n## 为什么是子词\n\n词表太大、字符太碎。\n\n## 合并规则\n\n高频对先合并。\n\n"
           f"## 后记\n\n{FILLER}\n")          # 后面也要够长：不然滚到底了，目标那节上不了顶


def seed(vault: Path) -> None:
    def add(name: str, line: str) -> None:
        md = vault / f"nodes/组A/{name}.md"
        md.write_text(md.read_text("utf-8").replace(f"desc: {name}\n", f"desc: {name}\n{line}"), "utf-8")
    add("甲", 'sources: ["[[articles/BPE全景#为什么是子词]]"]\n')
    add("乙", 'sources: ["[[articles/BPE全景]]"]\n')
    (vault / "articles").mkdir()
    (vault / "articles" / "BPE全景.md").write_text(ARTICLE, "utf-8")
    (vault / "articles" / "还没拆.md").write_text("# 还没拆的一篇\n\n正文还没导入。\n", "utf-8")


OPACITY = """JSON.stringify(Object.fromEntries(['甲', '乙', '丙'].map((id) => {
  const c = window.__kg.graph.getCellById(id); return [id, c ? c.attr('body/opacity') ?? 1 : null] })))"""


async def pick(page, name: str) -> None:
    await page.ev(f"""(() => {{ const i = document.querySelector('.search input');
      i.value = {json.dumps(name)}; i.dispatchEvent(new Event('input', {{ bubbles: true }})); }})()""")
    await E.poll(page, "document.querySelectorAll('.search .hits li').length", lambda v: (v or 0) > 0)
    await page.ev("""(() => { const li = document.querySelector('.search .hits li');
      li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })); })()""")
    await E.poll(page, "document.querySelector('.insp .node-title')?.textContent || ''",
                 lambda v: name in (v or ""), timeout=8)
    await asyncio.sleep(0.8)


async def case_list_and_read(page, ck) -> None:
    await E.open_rail(page, "原文")
    rows = await E.poll(page, """JSON.stringify([...document.querySelectorAll('aside .art-row')].map((r) =>
      r.textContent.replace(/\\s+/g, '')))""", lambda v: v and "BPE" in v, timeout=8)
    rows = json.loads(rows or "[]")
    ck.add("活动栏有「原文」，列表里两篇", len(rows) == 2, str(rows))
    ck.add("拆过的标拆出几个点", any("BPE全景2个点" in r for r in rows), str(rows))
    ck.add("没拆过的标「还没拆」并给按钮", any("还没拆的一篇还没拆拆成知识点" in r for r in rows), str(rows))

    await E.click_text(page, "aside .art-open", "BPE 全景")
    await E.poll(page, "!!document.querySelector('aside .art-read')", lambda v: v, timeout=8)
    head = await page.ev("document.querySelector('aside .art-head')?.textContent.replace(/\\s+/g, '') || ''")
    ck.add("页头：已拆成 2 个点、以节点为准、导入日期", "已拆成2个点" in head and "以节点为准" in head
           and "2026-09-23" in head, head[:120])
    sec = await page.ev("""JSON.stringify([...document.querySelectorAll('aside .art-h')].map((h) =>
      [h.childNodes[0].textContent.trim(), [...h.querySelectorAll('.art-chip')].map((c) => c.textContent.trim())]))""")
    sec = dict(json.loads(sec or "[]"))
    ck.add("出自那一节的点挂在那节标题旁", sec.get("为什么是子词") == ["甲"] and sec.get("合并规则") == [], str(sec))
    ck.add("链整篇的点只进顶上总表", "乙" in head and all("乙" not in v for v in sec.values()), head[:160])
    op = json.loads(await page.ev(OPACITY) or "{}")
    ck.add("画布上点亮这篇拆出的点、其余淡出", op.get("甲") == 1 and op.get("乙") == 1 and op.get("丙") == 0.35, str(op))

    await E.click_text(page, "aside .art-h .art-chip", "甲")
    title = await E.poll(page, "document.querySelector('.insp .node-title')?.textContent || ''",
                         lambda v: "甲" in (v or ""), timeout=6)
    ck.add("点标题旁的点 → 图上定位到它", "甲" in (title or ""), str(title))

    await page.ev("document.querySelector('aside [data-act=\"back\"]')?.click()")
    await asyncio.sleep(0.6)
    op = json.loads(await page.ev(OPACITY) or "{}")
    ck.add("回到列表，画布高亮收回", op.get("丙") == 1, str(op))


async def case_from_inspector(page, ck) -> None:
    await page.ev("""document.querySelector('aside .icon-btn[title*="收起"]')?.click()""")
    await asyncio.sleep(0.3)
    await pick(page, "甲")
    await page.ev("""[...document.querySelectorAll('.insp .src-item a')].find((a) => !a.classList.contains('src-obsidian'))?.click()""")
    await E.poll(page, "!!document.querySelector('aside .art-read')", lambda v: v, timeout=8)
    await asyncio.sleep(0.5)
    seen = await page.ev("""(() => {
      const box = document.querySelector('aside .art-read')?.closest('.drawer-body');   // 真正滚的是抽屉体
      const h = [...document.querySelectorAll('aside .art-h')].find((x) => x.textContent.includes('为什么是子词'));
      if (!box || !h) return null;
      const b = box.getBoundingClientRect(), r = h.getBoundingClientRect();
      return JSON.stringify({ top: Math.round(r.top - b.top), height: Math.round(b.height), scrolled: box.scrollTop });
    })()""")
    seen = json.loads(seen or "null") or {}
    ck.add("检查器点来源 → 应用里打开并滚到那一节",
           seen.get("scrolled", 0) > 0 and 0 <= seen.get("top", -1) < seen.get("height", 0) / 2, str(seen))
    obs = await page.ev("document.querySelector('.insp .src-obsidian')?.getAttribute('href') || ''")
    ck.add("旁边的小图标还是去 Obsidian", obs.startswith("obsidian://open?"), obs)


async def case_import_fresh(page, ck) -> None:
    await page.ev("document.querySelector('aside [data-act=\"back\"]')?.click()")
    await E.poll(page, "document.querySelectorAll('aside .art-row').length", lambda v: v == 2, timeout=6)
    await page.ev("""[...document.querySelectorAll('aside .art-row')].find((r) => r.textContent.includes('还没拆的一篇'))
      ?.querySelector('[data-act="import"]')?.click()""")
    got = await E.poll(page, """(() => {
      const t = document.querySelector('aside .imp-text'); const s = document.querySelector('aside .imp-form input');
      return t && s ? JSON.stringify([t.value, s.value]) : '';
    })()""", lambda v: v and "正文还没导入" in v, timeout=8)
    got = json.loads(got or '["", ""]')
    ck.add("「拆成知识点」→ 导入面板里带着这篇、文章名填好", "正文还没导入" in got[0] and got[1] == "还没拆", str(got))


async def scenarios(page, ck) -> None:
    await E.wait_render(page, 4)
    await case_list_and_read(page, ck)
    await case_from_inspector(page, ck)
    await case_import_fresh(page, ck)


async def run(api: str, cdp: str, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await scenarios(page, ck)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-articles-") as tmpdir:
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
            asyncio.run(run(api, cdp, results))
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
