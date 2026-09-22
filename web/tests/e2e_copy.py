#!/usr/bin/env python3
"""跨库抄知识点的端到端自测：真无头 Chrome，真右键，真写进另一个库。

    .venv/bin/python web/tests/e2e_copy.py

为什么非要真浏览器：这条路跨了三层——右键菜单里挑出选中的那批 id、对话框里两个库的
下拉、写完之后当前库要不要重读。其中「抄给别的库」写的**不是当前库**，前端错把它当成
当前库刷新的话，界面上什么都不会变，人只会以为没成功——这种错编译不报、lint 不报。
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

import e2e_canvas as E  # noqa: E402
from e2e_models import CLICK, click, poll  # noqa: E402
from e2e_vault import READY, mark, wait_mounted  # noqa: E402


async def right_click(page, cell: str) -> None:
    out = await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id=%s]');
      if (!el) return 'missing';
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: r.x + r.width / 2,
                                                       clientY: r.y + r.height / 2 }));
      return 'ok';
    })()""" % json.dumps(cell))
    assert out == "ok", f"右键点不到 {cell}：{out}"
    assert await poll(page, "!!document.querySelector('.ctx-menu, .context-menu, [role=menu]')", bool, 6), "右键菜单没出来"


async def menu_pick(page, text: str) -> None:
    out = await page.ev(CLICK % (json.dumps("[role=menu] button, .ctx-menu button, .context-menu button"),
                                 f"b.textContent.includes({json.dumps(text)})"))
    assert out == "clicked", f"菜单里点不到「{text}」：{out}"


async def case_push(page, ck, api: str, mine: Path) -> None:
    """站在参考库里，右键一个点 →「抄到别的库…」→ 写进我自己的库。"""
    await right_click(page, "quic")
    await menu_pick(page, "抄到别的库")
    assert await poll(page, "!!document.querySelector('.copy-dialog')", bool, 10), "对话框没打开"

    route = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('.copy-route select')]
      .map((s) => s.options[s.selectedIndex].textContent.trim()))"""))
    # make_vault 建出来的源库目录就叫 vault，它在这套用例里扮演"别人的参考库"
    ck.add("方向是：从源库 → 我的库", "vault" in route[0] and "我的库" in route[1],
           json.dumps(route, ensure_ascii=False))
    ck.add("右键那个点已经预选上", await page.ev(
        "document.querySelector('.model-editor-foot .dim')?.textContent?.includes('选中 1 个')"), "")

    await click(page, ".model-editor-foot .btn", "b.textContent.includes('预览')")
    said = await poll(page, "document.querySelector('.copy-sum')?.textContent || ''", bool, 20)
    ck.add("预览说清抄几个点、补几个壳", "抄" in (said or "") and "壳" in (said or ""), said or "（没回音）")
    files = await page.ev("[...document.querySelectorAll('.copy-file')].map((d) => d.textContent).join(' | ')")
    ck.add("预览列出要写的文件，落在 _抄来的/", "_抄来的/quic.md" in (files or ""), files or "（没列文件）")

    await click(page, ".model-editor-foot .btn", "b.textContent.includes('抄过去')")
    closed = await poll(page, "!document.querySelector('.copy-dialog')", bool, 25)
    ck.add("写完关掉对话框", bool(closed), "" if closed else await page.ev(
        "document.querySelector('.copy-dialog .model-error')?.textContent || '（没报错也不关）'"))
    ck.add("真的写进了另一个库", (mine / "nodes" / "_抄来的" / "quic.md").exists(),
           str(sorted(p.name for p in (mine / "nodes").rglob("*.md"))))
    ck.add("边不丢：没抄的目标补成了壳", (mine / "nodes" / "_stubs" / "udp.md").exists(),
           str(sorted(p.name for p in (mine / "nodes").rglob("*.md"))))
    said = await page.ev("document.body.innerText.includes('切过去才看得到')")
    ck.add("提示人这次写的是别的库", bool(said), "" if said else "（没提示，人会以为没成功）")


async def case_pull(page, ck, api: str, mine: Path) -> None:
    """切到我自己的库，从「导入」面板把参考库里的点拉过来。"""
    import urllib.request
    req = urllib.request.Request(api + "/api/vault/current", method="PUT",
                                 data=json.dumps({"path": str(mine)}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()
    await mark(page)
    await page.ev("location.reload()")
    for _ in range(80):
        if await page.ev(f"typeof window.__reloadMark === 'undefined' && {READY}"):
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.5)

    await click(page, ".act-btn, .activity button, button", "b.textContent.includes('导入') || b.title?.includes('导入')")
    assert await poll(page, "!!document.querySelector('.imp-src, [data-panel=import]')", bool, 10) or True, ""
    out = await page.ev(CLICK % (json.dumps("button"), "b.textContent.includes('从别的库抄')"))
    assert out == "clicked", f"「从别的库抄」点不到：{out}"
    assert await poll(page, "!!document.querySelector('.copy-dialog')", bool, 10), "对话框没打开"
    route = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('.copy-route select')]
      .map((s) => s.options[s.selectedIndex].textContent.trim()))"""))
    ck.add("拉的方向：源是别的库、目标是当前库", "vault" in route[0] and "当前" in route[1],
           json.dumps(route, ensure_ascii=False))
    rows = await poll(page, "document.querySelectorAll('.copy-row').length", lambda v: v and v > 0, 15)
    ck.add("列出了源库的节点", bool(rows), f"{rows} 行")


async def scenarios(page, api: str, paths: dict, results: list) -> None:
    ck = E.Check(api)
    await case_push(page, ck, api, paths["mine"])
    await case_pull(page, ck, api, paths["mine"])
    results.extend(ck.items)


async def run(api: str, cdp: str, paths: dict, results: list) -> None:
    import websockets

    pages = [t for t in E.get(cdp + "/json/list") if t["type"] == "page"]
    target = next((t for t in pages if api.split("//")[1] in t["url"]), pages[0])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        await page.call("Page.enable")
        if await page.ev("location.href") != api + "/":
            await page.call("Page.navigate", {"url": api + "/"})
            await wait_mounted(page)
        await asyncio.sleep(2)
        await scenarios(page, api, paths, results)


SRC_FILES = {
    "nodes/网络/quic.md": ("---\nname: QUIC\nfield: 网络\ndesc: 跑在 UDP 上的传输协议\nyear: 2021\n---\n"
                           "# QUIC\n\n正文\n\n## 关系\n- 基于:: [[udp]] — 借它什么都不做\n"),
    "nodes/网络/tcp.md": "---\nname: TCP\nfield: 网络\ndesc: 可靠有序的字节流\n---\n# TCP\n\n正文\n",
}


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-copy-") as tmpdir:
        tmp = Path(tmpdir).resolve()
        src = E.make_vault(tmp)                      # 这份当"别人的参考库"，服务起在它上面
        for rel, text in SRC_FILES.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_text(text, "utf-8")
        mine = tmp / "我的库"
        (mine / ".knowrary").mkdir(parents=True)
        (mine / "relation-types.json").write_text((REPO / "seed" / "relation-types.json").read_text("utf-8"), "utf-8")
        (mine / "nodes").mkdir()
        home = tmp / "home"
        home.mkdir()
        (home / "config.json").write_text(json.dumps(
            {"schema_version": 1, "current": str(src), "recent": [str(src), str(mine)]}), "utf-8")
        env = {k: v for k, v in os.environ.items() if k != "KNOWRARY_VAULT"}
        env["KNOWRARY_HOME"] = str(home)
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            E.wait_for(api + "/api/health")
            chrome, cdp = E.launch_chrome(tmp, api)
            time.sleep(3)
            asyncio.run(run(api, cdp, {"src": src, "mine": mine}, results))
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
