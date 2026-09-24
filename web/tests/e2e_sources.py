#!/usr/bin/env python3
"""检查器「来源」的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_sources.py

来源是一个列表（frontmatter `sources`，旧的 `source` 并在里面），混着三种东西：
库里的原文（点得开）、外部来源（灰字）、链到了但原文不在（标红）。它跨了三层——
索引只存原样字符串、节点详情才解析、检查器按解析结果渲染——任何一层对不上都只有点开才看得出来。
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

FM_SOURCES = ('sources:\n  - "[[articles/BPE全景#为什么是子词]]"\n  - 图灵《计算机器与智能》\n'
              '  - "[[articles/还没放进来]]"\n')

SOURCES = """JSON.stringify((() => {
  const dt = [...document.querySelectorAll('.insp dt')].find((d) => d.textContent.trim() === '来源');
  const dd = dt?.nextElementSibling;
  if (!dd) return null;
  return [...dd.querySelectorAll('.src-item')].map((s) => ({
    text: s.textContent.replace(/\\s+/g, ''),
    href: s.querySelector('a.src-obsidian')?.getAttribute('href') || '',
    inapp: !!s.querySelector('a:not(.src-obsidian)'),
    dead: !!s.querySelector('.src-dead'),
    muted: !!s.querySelector('.muted') || s.classList.contains('muted') }));
})())"""


def seed(vault: Path) -> None:
    md = vault / "nodes/组A/甲.md"
    md.write_text(md.read_text("utf-8").replace("desc: 甲\n", "desc: 甲\n" + FM_SOURCES), "utf-8")
    md = vault / "nodes/组A/乙.md"
    md.write_text(md.read_text("utf-8").replace("desc: 乙\n", "desc: 乙\nsource: 知识图谱.jpg\n"), "utf-8")
    (vault / "articles").mkdir()
    (vault / "articles" / "BPE全景.md").write_text("# BPE 全景\n\n## 为什么是子词\n\n原文\n", "utf-8")


async def pick(page, name: str) -> None:
    """用搜索框选中一个节点（和 e2e_canvas.case_search 同一个办法）。"""
    await page.ev(f"""(() => {{ const i = document.querySelector('.search input');
      i.value = {json.dumps(name)}; i.dispatchEvent(new Event('input', {{ bubbles: true }})); }})()""")
    await E.poll(page, "document.querySelectorAll('.search .hits li').length", lambda v: (v or 0) > 0)
    await page.ev("""(() => { const li = document.querySelector('.search .hits li');
      li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })); })()""")
    await E.poll(page, "document.querySelector('.insp .node-title')?.textContent || ''",
                 lambda v: name in (v or ""), timeout=8)
    await asyncio.sleep(0.8)          # 节点详情是异步拉的：解析过的来源要等它回来


async def scenarios(page, ck) -> None:
    await E.wait_render(page, 4)
    await pick(page, "甲")
    got = json.loads(await page.ev(SOURCES) or "null") or []
    ck.add("检查器里来源是一个列表（三项）", len(got) == 3, str(got))
    art, ext, dead = (got + [{}] * 3)[:3]
    ck.add("库里的原文点得开（名字在应用里读，旁边小图标去 Obsidian），带上小节",
           art.get("inapp") and art.get("href", "").startswith("obsidian://open?")
           and "file=articles/BPE%E5%85%A8%E6%99%AF.md" in art.get("href", "") and "§为什么是子词" in art.get("text", ""),
           str(art))
    ck.add("外部来源是灰字、不是链接", ext.get("muted") and not ext.get("href") and not ext.get("inapp")
           and ext.get("text") == "图灵《计算机器与智能》", str(ext))
    ck.add("链到了但原文不在：标出来、不给死链", dead.get("dead") and not dead.get("href") and not dead.get("inapp")
           and "原文不在" in dead.get("text", ""), str(dead))

    await pick(page, "乙")
    got = json.loads(await page.ev(SOURCES) or "null") or []
    ck.add("旧的单值 source 照样显示（一张图 → 外部来源）",
           len(got) == 1 and got[0]["muted"] and got[0]["text"] == "知识图谱.jpg", str(got))

    await pick(page, "丙")
    has = await page.ev("[...document.querySelectorAll('.insp dt')].some((d) => d.textContent.trim() === '来源')")
    ck.add("没有来源的节点不显示这一行", not has, str(has))


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
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-sources-") as tmpdir:
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
