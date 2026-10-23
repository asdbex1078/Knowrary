#!/usr/bin/env python3
"""成品层的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_works.py

成品层（2026-10-22）：节点 sources 链 `[[works/….html#锚点]]` → 画布右上角亮「▶」徽标、
检查器里的来源是新标签页链接；原文面板「成品」页签列出成品、标出覆盖的点和「待导入」；
点「拆成知识点」带着 html 进导入面板，显示「成品即原文」。不调模型、不真打开新标签页。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_canvas as E  # noqa: E402  (壳子全在这儿：free_port / launch_chrome / Page / get)

WORK = ("<!doctype html><html><head><meta charset='utf-8'><title>ConcurrentHashMap 桶锁</title></head>"
        "<body><h1>桶锁</h1><section id='sec-bin'><h2>桶与扩容</h2><p>一个 bin 一把锁。</p></section>"
        "</body></html>")
FRESH = ("<!doctype html><html><head><meta charset='utf-8'><title>未拆的实验台</title></head>"
         "<body><h1>实验台</h1><p>还没导入。</p></body></html>")
NODE_D = ("---\nname: D\nfield: 测试\ndesc: 链着成品的点\n"
          "sources: [\"[[works/桶锁.html#sec-bin]]\"]\n---\n# D\n\n正文\n")


def make_vault(tmp: Path) -> Path:
    vault = E.make_vault(tmp)
    for rel, text in {"works/桶锁.html": WORK, "works/未拆.html": FRESH, "nodes/组A/d.md": NODE_D}.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return vault


async def scenarios(page: E.Page, ck: E.Check) -> None:
    await E.wait_render(page, 4)

    # ① 画布徽标：d 链着成品 → 右上角「▶」
    marks = await E.poll(page, """[...document.querySelectorAll('[data-cell-id="d"] text')]
      .map((t) => t.textContent).join('')""", lambda v: "▶" in (v or ""))
    ck.add("链着成品的节点亮出 ▶ 徽标", "▶" in (marks or ""), f"texts = {marks!r}")
    plain = await page.ev("""[...document.querySelectorAll('[data-cell-id="a"] text')]
      .map((t) => t.textContent).join('')""")
    ck.add("没链成品的节点不画徽标", "▶" not in (plain or ""), f"texts = {plain!r}")

    # ② 检查器：点开 d，成品来源是指向 /api/work 的新标签页链接（带锚点）
    await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="d"]');
      const r = el.getBoundingClientRect();
      for (const type of ['mousedown', 'mouseup', 'click'])
        el.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: r.x + r.width / 2,
                                                clientY: r.y + r.height / 2 }));
    })()""")
    href = await E.poll(page, """document.querySelector('.src-item a[href*="/api/work"]')?.getAttribute('href') || ''""",
                        lambda v: v, timeout=12)
    ok = "works%2F%E6%A1%B6%E9%94%81.html" in (href or "") and href.endswith("#sec-bin")
    ck.add("检查器来源链 /api/work 且带锚点", ok, f"href = {href!r}")

    # ③ 原文面板「成品」页签：列两个成品，一个标覆盖的点、一个标待导入
    await E.open_rail(page, "原文")
    await E.poll(page, """document.querySelector('[data-tab="成品"]') ? 'ok' : ''""", lambda v: v)
    await page.ev("""document.querySelector('[data-tab="成品"]').click()""")
    covered = await E.poll(page, """(() => {
      const row = document.querySelector('[data-work="works/桶锁.html"]');
      return row ? row.textContent.replace(/\\s+/g, ' ') : '';
    })()""", lambda v: v, timeout=12)
    ck.add("成品页签列出已覆盖的成品", "ConcurrentHashMap 桶锁" in (covered or "") and "1 个点" in (covered or ""),
           (covered or "")[:110])
    fresh = await page.ev("""(document.querySelector('[data-work="works/未拆.html"]')?.textContent || '')
      .replace(/\\s+/g, ' ')""")
    ck.add("没节点链着的成品标「待导入」", "待导入" in (fresh or ""), (fresh or "")[:110])
    chips = await page.ev("""[...document.querySelectorAll('.art-work-refs .art-chip')]
      .map((b) => b.textContent.trim()).join('、')""")
    ck.add("成品行下挂着拆出来的点", "D" in (chips or ""), f"chips = {chips!r}")

    # ④ 点「拆成知识点」：带着 html 进导入面板，「保留原文」换成「成品即原文」的说明
    out = await page.ev("""(() => {
      const row = document.querySelector('[data-work="works/未拆.html"]');
      const btn = row && row.querySelector('[data-act="import"]');
      if (!btn) return 'missing';
      btn.click(); return 'ok';
    })()""")
    ck.add("成品行上有「拆成知识点」", out == "ok", f"click = {out}")
    note = await E.poll(page, """(() => {
      const el = document.querySelector('.imp-keep');
      return el ? el.textContent.replace(/\\s+/g, ' ') : '';
    })()""", lambda v: v and "成品" in v, timeout=12)
    ck.add("导入面板按成品处理（成品即原文）", "成品即原文" in (note or ""), (note or "")[:110])
    name = await page.ev("""document.querySelector('.imp-form input')?.value || ''""")
    ck.add("文章名剥掉 .html 后缀", name == "未拆", f"source = {name!r}")


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
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-works-") as tmpdir:
        tmp = Path(tmpdir)
        vault = make_vault(tmp)
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
