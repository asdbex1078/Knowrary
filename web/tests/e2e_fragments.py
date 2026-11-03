#!/usr/bin/env python3
"""碎片记忆 + 竣工图的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_fragments.py

碎片（2026-10-23）：面板里随手记 → 积压统计 → 归拢（假 learn 从提示词里抠出碎片 id，
回「并进 a / 长成新点 / 继续躺」三簇）→ 点「并进」真写 md 并标去向 → 点「去导入面板拆」
整理稿带进导入面板。竣工图：项目挂专题（顶栏下拉），挂上就切到专题模式。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "tools" / "knowrary"))

import e2e_canvas as E  # noqa: E402

import core  # noqa: E402

RE_FRAG_ID = re.compile(r"^- \[([0-9: -]+ [0-9a-f]{8})\]", re.M)


def clusters_of(prompt: str) -> dict:
    """从归拢提示词里抠出真实的碎片 id：第一条并进 a、第二条长成新点、第三条继续躺。"""
    ids = RE_FRAG_ID.findall(prompt)
    out = []
    if len(ids) > 0:
        out.append({"title": "桶锁补充", "ids": [ids[0]], "action": "enrich", "node": "甲",
                    "summary": "补进甲", "text": "碎片里说的桶锁细节，整理成一段。"})
    if len(ids) > 1:
        out.append({"title": "惰性绑定", "ids": [ids[1]], "action": "new", "node": None,
                    "summary": "自成一个点", "text": "PLT 蹦床与惰性绑定的完整说明。"})
    if len(ids) > 2:
        out.append({"title": "太薄", "ids": ids[2:], "action": "keep", "node": None, "summary": "再攒攒", "text": ""})
    return {"clusters": out}


class FakeLLM(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = "\n".join(str(m.get("content")) for m in req.get("messages") or [])
        out = json.dumps({"model": "fake", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(clusters_of(prompt), ensure_ascii=False)}}],
                          }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def fake_llm(tmp: Path) -> Path:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = tmp / "llm.json"
    cfg.write_text(json.dumps({"providers": {
        "fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                 "model": "fake", "api_key": "x"}},
        "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg


SET = """(sel, v) => {
  const el = document.querySelector(sel)
  el.value = v
  el.dispatchEvent(new Event('input', { bubbles: true }))
}"""


async def add_fragment(page: E.Page, text: str, expect: int) -> None:
    await page.ev(f"""(() => {{ const set = {SET}; set('aside .frag-add input', {json.dumps(text)}) }})()""")
    await asyncio.sleep(0.2)                     # 等 Vue 把按钮的 disabled 刷掉
    out = await page.ev("""(() => { const btn = document.querySelector('aside .frag-add .btn')
      if (!btn) return 'no-btn'
      if (btn.disabled) return 'disabled'
      btn.click(); return 'ok' })()""")
    assert out == "ok", f"记不了：{out}"
    deadline = time.time() + 6
    while time.time() < deadline:
        rows = await page.ev("document.querySelectorAll('aside .frag-row').length")
        if rows == expect:
            return
        await asyncio.sleep(0.25)
    err = await page.ev("document.querySelector('aside .imp-error')?.textContent || ''")
    rows = await page.ev("document.querySelectorAll('aside .frag-row').length")
    raise AssertionError(f"记第 {expect} 条没出现（rows={rows}，error={err!r}）")


async def scenarios(page: E.Page, ck: E.Check, api: str) -> None:
    # 预建了项目的库开机可能停在没有画布的那一页：等顶栏出来、关掉晨报遮罩、切到全局图再等渲染
    await E.poll(page, """[...document.querySelectorAll('.topbar .seg button')]
      .some((el) => el.textContent.includes('全局图'))""", lambda v: v, timeout=20)
    await page.ev("""document.querySelector('.brief-mask .btn.subtle')?.click()""")
    await E.switch_mode(page, "全局图")
    await E.wait_render(page, 4)

    # ① 碎片面板：记三条 → 积压统计
    await E.open_rail(page, "碎片")
    await E.poll(page, "!!document.querySelector('aside .frag-add input')", lambda v: v, timeout=8)
    for i, text in enumerate(("CHM 的桶锁在 resize 时会扩大持有范围", "PLT 是惰性绑定的蹦床", "今天看了眼 io_uring")):
        await add_fragment(page, text, i + 1)
    stats = await E.poll(page, """document.querySelector('aside .frag-stats')?.textContent.replace(/\\s+/g, ' ') || ''""",
                         lambda v: "3" in (v or ""), timeout=8)
    ck.add("记三条后积压统计是 3", "3" in (stats or "") and "没归拢" in (stats or ""), stats)
    rows = await page.ev("document.querySelectorAll('aside .frag-row').length")
    ck.add("列表按天列出三条", rows == 3, f"rows = {rows}")

    # ② 归拢：假 learn 回三簇；卡上能看见模型名（模型调用必须看得见）
    await page.ev("""document.querySelector('aside [data-act="consolidate"]').click()""")
    meta = await E.poll(page, """document.querySelector('aside .frag-meta')?.textContent || ''""",
                        lambda v: "fake" in (v or ""), timeout=15)
    ck.add("归拢回三簇、显示模型与耗时", "fake" in (meta or ""), meta)
    n = await page.ev("document.querySelectorAll('aside .frag-cluster').length")
    ck.add("三簇都摆出来", n == 3, f"clusters = {n}")
    dump = await page.ev("""JSON.stringify([...document.querySelectorAll('aside .frag-cluster')]
      .map((el) => el.querySelector('.chip')?.textContent.trim()))""")
    issues = await page.ev("""document.querySelector('aside .frag-meta span')?.getAttribute('title') || ''""")
    ck.add("第一簇是并进", "并进" in (dump or ""), f"actions={dump} issues={issues!r}")

    # ③ 并进：真写 md（append_body + 审核照常）、碎片标去向、这张簇卡自己消失
    await page.ev("""document.querySelector('aside [data-act="enrich"]').click()""")
    await E.poll(page, "document.querySelectorAll('aside .frag-row.done').length", lambda v: v == 1, timeout=15)
    body = E.get(api + "/api/node/%E7%94%B2")["raw"]
    ck.add("并进后「甲」的正文带「补充自碎片」引言", "补充自碎片" in body and "桶锁细节" in body, body[-120:])
    left = await page.ev("document.querySelectorAll('aside .frag-cluster').length")
    ck.add("落完地的簇卡自己消失", left == 2, f"clusters = {left}")
    row = await page.ev("""document.querySelector('aside .frag-row.done')?.textContent.replace(/\\s+/g, ' ') || ''""")
    ck.add("碎片行标了去向（→ 甲）", "→ 甲" in (row or ""), row)

    # ④ 长成新节点：整理稿带进导入面板（粘贴 tab、文章名 = 簇名），碎片标「导入《…》」
    await page.ev("""document.querySelector('aside [data-act="to-import"]').click()""")
    filled = await E.poll(page, """(() => {
      const t = document.querySelector('aside .imp-text')
      const name = document.querySelector('aside .imp-form input')
      return t && name ? JSON.stringify({ text: t.value, name: name.value }) : ''
    })()""", lambda v: v and "蹦床" in v, timeout=10)
    got = json.loads(filled or "{}")
    ck.add("整理稿带进导入面板、文章名是簇名", got.get("name") == "惰性绑定", filled)

    # ⑤ 竣工图：顶栏下拉「挂一张」→ 存进项目、切到专题模式
    await page.ev("""(() => { const s = document.querySelector('.proj-switch');
      s.value = 'p'; s.dispatchEvent(new Event('change', { bubbles: true })) })()""")
    await asyncio.sleep(1.2)
    await page.ev("""document.querySelector('[data-act="finished-map"]').click()""")
    # 下拉是先开、专题目录异步到：等「文件系统」真出现在面板里再点
    await E.poll(page, """[...document.querySelectorAll('.pop-panel .pop-item')]
      .some((el) => el.textContent.includes('文件系统'))""", lambda v: v, timeout=8)
    await E.click_text(page, ".pop-panel .pop-item", "文件系统")
    on = await E.poll(page, """document.querySelector('[data-mode="topic"]')?.classList.contains('on')""",
                      lambda v: v, timeout=10)
    ck.add("挂上专题后直接切到专题模式", bool(on), f"topic on = {on}")
    await asyncio.sleep(1.0)
    saved = E.get(api + "/api/projects")["doc"]["projects"]["p"].get("topics") or []
    toast = await page.ev("document.querySelector('.toast')?.textContent.replace(/\\s+/g, ' ') || ''")
    ck.add("专题存进项目的 topics 字段", saved == ["文件系统"], f"saved={saved} toast={toast!r}")


async def run(api: str, cdp: str, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await scenarios(page, ck, api)
        except Exception as exc:
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-frag-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "KNOWRARY_LLM_CONFIG": str(fake_llm(tmp)), "NO_PROXY": "127.0.0.1,localhost",
               "no_proxy": "127.0.0.1,localhost"}
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            E.wait_for(api + "/api/health")
            # 项目和专题在开浏览器**之前**建好：App 启动时就能拉到，savePlans 的乐观锁不会撞
            E.send(api + "/api/projects", {"base_revision": 0, "projects": {
                "p": {"name": "P 项目", "lists": []}}}, method="PUT")
            E.send(api + "/api/topics", {"topic": "文件系统"})
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
