#!/usr/bin/env python3
"""对话会话列表的端到端自测：真无头 Chrome，点开列表 → 归档 → 放回 → 删除。

    .venv/bin/python web/tests/e2e_chat_sessions.py

复用 e2e_canvas.py 的那套壳，单开一个文件：要往 vault 里预先塞几段对话留档，
混进画布那条长流水会打乱它靠顺序维系的前提（它断言全局线是空的）。

为什么非要真浏览器：会话条从原生 <select> 换成了 Popover 列表，行里套着按钮——
`@click.stop` 漏一个，点「归档」就会顺带切会话、把弹层关掉；这类错只有点一下才知道。
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

SESSIONS = (("sa", "第一段 Redis 持久化"), ("sb", "第二段 MVCC 追问"), ("sc", "第三段 事务隔离"))

ROWS = """JSON.stringify([...document.querySelectorAll('.pop-panel .sess-row .sess-title')]
  .map((e) => e.textContent.trim()))"""


def seed_chat(vault: Path) -> None:
    """全局线和 demo 项目线各塞三段，前端落在哪条线都有东西可点。"""
    for slot in ("_scratch", "demo"):
        d = vault / ".knowrary" / "chat" / slot
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        for i, (sid, q) in enumerate(SESSIONS):
            ts = f"2026-09-0{i + 1}T10:00:00+08:00"
            lines.append({"ts": ts, "role": "user", "text": q, "session": sid})
            lines.append({"ts": ts, "role": "assistant", "text": f"回答 {q}", "session": sid})
        (d / "2026-09.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines), "utf-8")


async def rows(page) -> list:
    return json.loads(await page.ev(ROWS) or "[]")


async def open_list(page) -> None:
    if not await page.ev("!!document.querySelector('.pop-panel .sess-list')"):
        await E.open_popover(page, ".chat-bar .sess-trigger")


async def row_btn(page, title: str, idx: int) -> str:
    """点某一行里的第 idx 个图标按钮（0 = 归档/放回，1 = 删除）。"""
    return await page.ev(f"""(() => {{
      const r = [...document.querySelectorAll('.pop-panel .sess-row')]
        .find((x) => x.querySelector('.sess-title').textContent.includes({title!r}));
      if (!r) return 'missing';
      r.querySelectorAll('.icon-btn')[{idx}].click(); return 'ok';
    }})()""")


async def settle(page) -> None:
    """等首屏：顶栏出来、晨间简报（全屏遮罩）关掉，再切到对话。"""
    await E.poll(page, "!!document.querySelector('.topbar .seg button')", lambda v: v, timeout=20)
    if await E.poll(page, "!!document.querySelector('.brief')", lambda v: v, timeout=6):
        await page.ev("""(() => {
          const b = [...document.querySelectorAll('.brief .btn')].find((x) => x.textContent.includes('待会儿'))
            || document.querySelector('.brief .icon-btn');
          b?.click(); return 'ok';
        })()""")
        await E.poll(page, "!document.querySelector('.brief')", lambda v: v, timeout=8)
    await E.switch_mode(page, "对话")
    await E.poll(page, "!!document.querySelector('.chat-bar .sess-trigger')", lambda v: v, timeout=8)
    await asyncio.sleep(0.8)          # 会话列表是异步拉的


async def scenarios(page, ck, vault: Path) -> None:
    await settle(page)
    ck.add("会话条是弹出列表不是原生下拉",
           await page.ev("!!document.querySelector('.chat-bar .sess-trigger')"), "")
    await open_list(page)
    got = await rows(page)
    ck.add("首屏就在对话时列表也拉得到（不用先切一次模式）", len(got) == 3, str(got))
    log_of = {s: vault / ".knowrary/chat" / s / "2026-09.jsonl" for s in ("_scratch", "demo")}

    # 归档：弹层不关、这一行消失、底下出现「已归档 1」
    assert await row_btn(page, "第二段", 0) == "ok"
    await asyncio.sleep(0.8)
    got = await rows(page)
    still = await page.ev("!!document.querySelector('.pop-panel')")
    shelf = await page.ev("""[...document.querySelectorAll('.pop-panel .pop-item')]
      .map((b) => b.textContent.replace(/\\s+/g, '')).join('|')""")
    ck.add("点归档不会顺带切会话、关弹层", bool(still), f"panel={still}")
    ck.add("归档后那一段离开列表", "第二段 MVCC 追问" not in got and len(got) == 2, str(got))
    ck.add("底下出现「已归档 1」", "已归档1" in (shelf or ""), str(shelf))
    arch = [s for s, p in log_of.items() if (p.parent / "archived.json").exists()]
    ck.add("归档落成贴纸、留档不动", len(arch) == 1 and "sb" in (log_of[arch[0]].parent / "archived.json").read_text("utf-8")
           and '"session": "sb"' in log_of[arch[0]].read_text("utf-8"), str(arch))
    slot = arch[0] if arch else "_scratch"

    # 展开已归档 → 放回
    await E.click_text(page, ".pop-panel .pop-item", "已归档")
    await asyncio.sleep(0.4)
    got = await rows(page)
    ck.add("展开后看得到归档的那段", "第二段 MVCC 追问" in got, str(got))
    assert await row_btn(page, "第二段", 0) == "ok"
    await asyncio.sleep(0.8)
    got = await rows(page)
    shelf_left = await page.ev("""[...document.querySelectorAll('.pop-panel .pop-item')]
      .some((b) => b.textContent.includes('归档'))""")
    ck.add("放回之后三段都在、已归档那栏消失", len(got) == 3 and not shelf_left, f"{got} shelf={shelf_left}")

    # 删除：第一下只出确认条，第二下才真删
    assert await row_btn(page, "第三段", 1) == "ok"
    await asyncio.sleep(0.4)
    confirm = await page.ev("document.querySelector('.pop-panel .sess-confirm')?.textContent || ''")
    ck.add("点垃圾桶先出确认条", "删除" in (confirm or "") and "第三段" in confirm, confirm)
    ck.add("确认之前留档一行没动", '"session": "sc"' in log_of[slot].read_text("utf-8"), "")
    await E.click_text(page, ".pop-panel .sess-confirm .btn", "删除")
    await asyncio.sleep(0.9)
    got = await rows(page)
    text = log_of[slot].read_text("utf-8")
    ck.add("删除后列表剩两段", len(got) == 2 and "第三段 事务隔离" not in got, str(got))
    ck.add("留档里那段的行真没了、别的还在", '"session": "sc"' not in text and '"session": "sa"' in text, "")

    # 刷新一下：删掉的不回来
    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await open_list(page)
    got = await rows(page)
    ck.add("刷新后删掉的那段不回来", len(got) == 2 and "第三段 事务隔离" not in got, str(got))


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
    if not Path(E.CHROME).exists():
        raise SystemExit(f"找不到 Chrome：{E.CHROME}")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-chat-") as tmpdir:
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
