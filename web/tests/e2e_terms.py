#!/usr/bin/env python3
"""术语面板（名词解释，2026-10-27）的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_terms.py

验这条链路：`术语/*.md` 合并进索引 `glossary` → 左侧活动栏「术语」开面板 → 按语境分组（全局 /
各专题，`语境::` 能逐条覆盖）→ 搜词 / 别名 / 解释 → 点「详见」跳到知识点。

core 单测已锁死 parse/merge 的口径；这里补的是界面实时路径：面板渲染、分组、搜索、跳转。
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

import e2e_canvas as E  # noqa: E402
from e2e_chat_sessions import settle  # noqa: E402

GLOBAL_MD = ("## GDT\n全局描述符表 —— 存段描述符的表。\n- 同:: 全局描述符表, 描述符表\n- 详见:: [[甲]]\n\n"
             "## CPU\n中央处理器。\n")
TOPIC_MD = ("## 时间片\n进程一次最多连续跑多久。\n\n"
            "## CR3\n页目录基址寄存器。\n- 语境:: 硬件\n")   # 逐条覆盖文件默认语境


def seed(vault: Path) -> None:
    for rel, text in {"术语/_全局.md": GLOBAL_MD, "术语/调度.md": TOPIC_MD}.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")


async def groups(page):
    return await page.ev("[...document.querySelectorAll('.term-group .scope-tag')].map((s) => s.textContent.trim())")


async def terms_in(page, scope: str):
    return await page.ev(f"""[...document.querySelectorAll('.term-group')]
      .filter((g) => g.dataset.scope === '{scope}')
      .flatMap((g) => [...g.querySelectorAll('.term-row')].map((r) => r.dataset.term))""")


async def open_panel(page, ck) -> None:
    await E.poll(page, "!!document.querySelector('[data-rail=\"terms\"]')", bool, timeout=8)
    await page.ev("document.querySelector('[data-rail=\"terms\"]').click()")
    await E.poll(page, "!!document.querySelector('.term-group')", bool, timeout=6)
    gs = await groups(page)
    ck.add("活动栏「术语」开面板 → 按语境分组，全局在最前，`语境::` 覆盖成「硬件」独立一组",
           gs == ["全局", "硬件", "调度"] or gs == ["全局", "调度", "硬件"], f"groups={gs}")
    glob = await terms_in(page, "全局")
    ck.add("全局组：GDT、CPU 都在", sorted(glob) == ["CPU", "GDT"], f"全局={glob}")
    aka = await page.ev("document.querySelector('.term-row[data-term=\"GDT\"] .term-aka')?.textContent?.trim() || ''")
    ck.add("GDT 显示别名（同::）", "全局描述符表" in aka and "描述符表" in aka, f"aka={aka}")
    cr3 = await terms_in(page, "硬件")
    ck.add("CR3 走 `语境:: 硬件` → 落在硬件组、不在调度组", cr3 == ["CR3"], f"硬件={cr3}")


async def case_search(page, ck) -> None:
    await page.ev("""(() => { const s = document.querySelector('[data-term-search]');
      s.value = '时间片'; s.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    await asyncio.sleep(0.3)
    rows = await page.ev("[...document.querySelectorAll('.term-row')].map((r) => r.dataset.term)")
    ck.add("搜「时间片」：只剩它一条", rows == ["时间片"], f"rows={rows}")
    # 搜别名也认：描述符表 是 GDT 的别名
    await page.ev("""(() => { const s = document.querySelector('[data-term-search]');
      s.value = '描述符表'; s.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    await asyncio.sleep(0.3)
    rows = await page.ev("[...document.querySelectorAll('.term-row')].map((r) => r.dataset.term)")
    ck.add("搜别名「描述符表」→ 命中 GDT", rows == ["GDT"], f"rows={rows}")
    await page.ev("""(() => { const s = document.querySelector('[data-term-search]');
      s.value = ''; s.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    await asyncio.sleep(0.2)


async def case_add(page, ck, vault: Path) -> None:
    # 面板顶部「+」展开加词表单，填 TLB + 别名，提交 → 落盘 + 索引刷新 + 列表出现
    await page.ev("document.querySelector('[data-act=\"toggle-add\"]').click()")
    await E.poll(page, "!!document.querySelector('[data-term-add]')", bool, timeout=4)
    await page.ev("""(() => {
      const set = (sel, v) => { const e = document.querySelector(sel); e.value = v;
        e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); };
      set('[data-add-term]', 'TLB'); set('[data-add-gloss]', '页表缓存。'); set('[data-add-aka]', '快表, 旁路缓冲');
      set('[data-add-scope]', '全局');
    })()""")
    await page.ev("document.querySelector('[data-act=\"submit-add\"]').click()")
    ok = await E.poll(page, "!!document.querySelector('.term-row[data-term=\"TLB\"]')", bool, timeout=8)
    ck.add("面板加词：填表提交 → 落盘 + 索引刷新 + TLB 进列表", bool(ok), "")
    md = (vault / "术语" / "_全局.md").read_text("utf-8")
    ck.add("面板加词：写进 术语/_全局.md（含别名）", "## TLB" in md and "快表" in md and "旁路缓冲" in md, md[-120:])


async def select_in_md(page, text: str) -> None:
    """往正文里塞一段 `.md`，选中其中 text 这段，派 mouseup（触发选字查词）。"""
    await page.ev(f"""(() => {{
      let d = document.getElementById('__md_test');
      if (!d) {{ d = document.createElement('div'); d.id = '__md_test'; d.className = 'md';
        d.style.position = 'fixed'; d.style.top = '220px'; d.style.left = '420px'; document.body.appendChild(d); }}
      d.innerHTML = '<p>{text}</p>';
      const t = d.querySelector('p').firstChild;
      const r = document.createRange(); r.setStart(t, 0); r.setEnd(t, {len(text)});
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
      document.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true }}));
    }})()""")
    await asyncio.sleep(0.25)


async def case_capture(page, ck) -> None:
    # 选中一个**不是术语**的生词 → 冒「记为名词」→ 点它 → 术语面板加词表单打开、词条预填
    await select_in_md(page, "生僻词")
    shown = await E.poll(page, "!!document.querySelector('[data-term-capture]')", bool, timeout=4)
    cap = await page.ev("document.querySelector('[data-term-capture]')?.textContent?.trim() || ''")
    ck.add("正文选中生词（不在术语表）→ 冒「记为名词」", shown is True and "记为名词" in cap, cap)
    await page.ev("document.querySelector('[data-term-capture]').click()")
    await E.poll(page, "!!document.querySelector('[data-term-add]')", bool, timeout=4)
    val = await page.ev("document.querySelector('[data-add-term]')?.value || ''")
    ck.add("点「记为名词」→ 加词表单打开、词条已预填", val == "生僻词", f"预填={val}")


async def case_lookup(page, ck) -> None:
    # 选中一个**是术语**的词（GDT）→ 弹它的解释卡（按需查），有「详见」能跳、有「也记一条」
    await select_in_md(page, "GDT")
    shown = await E.poll(page, "!!document.querySelector('[data-term-lookup] .tl-sense')", bool, timeout=4)
    gloss = await page.ev("document.querySelector('[data-term-lookup] .tl-gloss')?.textContent?.trim() || ''")
    has_cap = await page.ev("!!document.querySelector('[data-term-capture]')")
    ck.add("正文选中术语「GDT」→ 弹解释卡（不是记为名词按钮）",
           shown is True and "全局描述符表" in gloss and has_cap is False, f"gloss={gloss} capPill={has_cap}")
    # 卡上「详见」跳到知识点甲（GDT 种子里写了 详见:: [[甲]]）
    await page.ev("document.querySelector('[data-term-lookup] [data-act=\"goto\"]').click()")
    ok = await E.poll(page, "document.querySelector('.node-title')?.textContent?.trim() === '甲'", bool, timeout=6)
    ck.add("解释卡「详见」→ 跳到知识点甲", bool(ok),
           await page.ev("document.querySelector('.node-title')?.textContent?.trim() || ''"))
    await page.ev("const d = document.getElementById('__md_test'); if (d) d.remove()")


async def case_goto(page, ck) -> None:
    # 点 GDT 的「详见」→ 跳到知识点甲（详情抽屉里出现它）
    await page.ev("document.querySelector('.term-row[data-term=\"GDT\"] [data-act=\"goto\"]').click()")
    ok = await E.poll(page, "document.querySelector('.node-title')?.textContent?.trim() === '甲'", bool, timeout=6)
    ck.add("点 GDT「详见」→ 跳到知识点甲（检查器详情打开）", bool(ok),
           await page.ev("document.querySelector('.node-title')?.textContent?.trim() || ''"))


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
            await open_panel(page, ck)
            await case_search(page, ck)
            await case_add(page, ck, vault)
            await case_capture(page, ck)
            await case_goto(page, ck)
            await case_lookup(page, ck)
        except Exception as exc:
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-terms-") as tmpdir:
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
