#!/usr/bin/env python3
"""导入时保留原文的端到端自测：真无头 Chrome，从粘贴一路点到写入。

    .venv/bin/python web/tests/e2e_import_article.py

原文层第 3 步：导入面板「保留原文」默认开，原文原样存进原文目录，节点的 sources 链到它出自的那一节。
画布那条长流水（e2e_canvas.case_import_panel）刻意不点「拆成知识点」——那是一次真模型调用。
这里用一个本地的假 OpenAI 接口当 learn 角色，把「拆解 → 卡上看原文去向 → 写入 → 原文落盘」整条走完。
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
sys.path.insert(0, str(REPO / "tools" / "knowrary"))

import e2e_canvas as E  # noqa: E402  (壳子全在这儿：free_port / launch_chrome / Page / get)

import core  # noqa: E402  （读回写下去的 md，和服务端同一个解析器）

ARTICLE = "# BPE 全景\n\n开头一段。\n\n## 为什么是子词\n\n词表太大、字符太碎。\n\n## 合并规则\n\n高频对先合并。\n"
BODY = "## 描述\n\n" + "BPE 从字符出发，反复合并最高频的相邻符号对，得到介于词和字符之间的子词单元。" * 6
PLAN = {"shape": "single", "summary": "整篇讲 BPE", "nodes": [
    {"id": "BPE", "name": "BPE", "desc": "子词分词", "from_section": "为什么是子词", "body": BODY,
     "relations": [{"type": "依赖", "target": "甲", "confidence": 0.9}]}]}
CALLS: list = []                     # 假模型被问了几次：同名冲突要在调模型之前挡下
PROMPTS: list[str] = []              # 每次问了什么：粒度档要进提示词
# 第二篇（2026-10-13 粒度）：一篇拆成三个点，卡上勾两个合并
ELF = "# ELF\n\n## 动态链接\n\nGOT/PLT。\n\n## 数据库扩展\n\ndlopen 加载 .so。\n"
MULTI = {"shape": "multi", "summary": "ELF 三个点", "nodes": [
    {"id": "ELF", "name": "ELF", "desc": "格式", "body": "## 描述\n见 [[数据库扩展]]", "relations": [
        {"type": "依赖", "target": "甲", "confidence": 0.9}, {"type": "包含", "target": "动态链接", "confidence": 1}]},
    {"id": "动态链接", "name": "动态链接", "desc": "GOT/PLT", "body": "## 描述\nd", "relations": []},
    {"id": "数据库扩展", "name": "数据库扩展", "desc": "dlopen", "body": "## 描述\nx",
     "relations": [{"type": "依赖", "target": "动态链接", "confidence": 1}]}]}
MERGED = {"main": "动态链接", "summary": "数据库扩展当动态链接的例子", "nodes": [
    {"id": "动态链接", "name": "动态链接", "desc": "运行时重定位", "body": "## 描述\nd\n\n## 例子\ndlopen 加载 .so",
     "relations": []}]}


def answer(prompt: str) -> dict:
    if "合并成一个" in prompt:
        return MERGED
    return MULTI if "dlopen" in prompt else PLAN


class FakeLearn(BaseHTTPRequestHandler):
    """假的 OpenAI 兼容接口，当 learn 角色：导入那一次调用固定回 PLAN。"""

    def do_POST(self):  # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = "\n".join(str(m.get("content")) for m in req.get("messages") or [])
        CALLS.append(1)
        PROMPTS.append(prompt)
        out = json.dumps({"model": "fake-learn", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(answer(prompt), ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def fake_learn(tmp: Path) -> Path:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLearn)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = tmp / "llm.json"
    cfg.write_text(json.dumps({"providers": {
        "fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                 "model": "fake-learn", "api_key": "x"}},
        "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg


CARD = """JSON.stringify({
  shape: document.querySelector('aside .imp-article [data-shape]')?.textContent.trim() || '',
  article: document.querySelector('aside .imp-article [data-article]')?.textContent.trim() || '',
  error: document.querySelector('aside .imp-error')?.textContent.trim() || '',
  keep: document.querySelector('aside [data-act="keep-article"]')?.checked })"""


async def paste(page, text: str, name: str) -> None:
    await page.ev(f"""(() => {{
      const set = (el, v) => {{ el.value = v; el.dispatchEvent(new Event('input', {{ bubbles: true }})); }};
      set(document.querySelector('aside .imp-text'), {json.dumps(text)});
      const inputs = document.querySelectorAll('aside .imp-form input');
      set(inputs[0], {json.dumps(name)}); set(inputs[1], '测试');
    }})()""")
    await asyncio.sleep(0.2)


async def scenarios(page, ck, vault: Path) -> None:
    await E.wait_render(page, 4)
    await E.open_rail(page, "导入")
    await E.poll(page, "!!document.querySelector('aside .imp-src')", lambda v: v, timeout=6)
    st = json.loads(await page.ev(CARD))
    ck.add("「保留原文」默认开着", st["keep"] is True, str(st))

    await paste(page, ARTICLE, "BPE全景")
    await E.click_text(page, "aside .imp-run .btn", "拆成知识点")
    await E.poll(page, "!!document.querySelector('aside .imp-card')", lambda v: v, timeout=10)
    st = json.loads(await page.ev(CARD))
    ck.add("卡上说清模型的判断：整篇一个点，没拆", "整篇只讲一个点" in st["shape"], str(st))
    ck.add("卡上说清原文去哪、写入才放", "articles/BPE全景.md" in st["article"] and "写入那一下才放" in st["article"],
           str(st))
    ck.add("预览时原文还没落盘", not (vault / "articles" / "BPE全景.md").exists(), "")

    await E.click_text(page, "aside .imp-card .btn", "写入")
    saved = vault / "articles" / "BPE全景.md"
    for _ in range(40):
        if saved.exists() and (vault / "nodes/测试/BPE.md").exists():
            break
        await asyncio.sleep(0.25)
    fm, body = core.split_frontmatter(saved.read_text("utf-8")) if saved.exists() else ({}, "")
    ck.add("写入后原文进了原文目录，正文一字不改", body == ARTICLE and fm.get("origin") == "粘贴", str(fm))
    node = vault / "nodes/测试/BPE.md"
    srcs = core.split_frontmatter(node.read_text("utf-8"))[0].get("sources") if node.exists() else None
    ck.add("新节点的来源链到它出自的那一节", srcs == ["[[articles/BPE全景#为什么是子词]]"], str(srcs))

    # 再导一篇同名、内容不同的：调模型之前就挡下，让人改名
    await E.click_text(page, "aside .imp-card .btn", "再导一篇")
    await asyncio.sleep(0.3)
    calls = len(CALLS)
    await paste(page, "# 另一篇\n\n完全不同。\n", "BPE全景")
    await E.click_text(page, "aside .imp-run .btn", "拆成知识点")
    await E.poll(page, "document.querySelector('aside .imp-error')?.textContent || ''", lambda v: bool(v), timeout=8)
    st = json.loads(await page.ev(CARD))
    ck.add("同名不同文：当场拦下让人改文章名", "换个文章名" in st["error"], str(st))
    ck.add("拦下时没调模型", len(CALLS) == calls, f"calls {calls}->{len(CALLS)}")
    ck.add("已有的那篇原文没被覆盖", core.split_frontmatter(saved.read_text("utf-8"))[1] == ARTICLE, "")
    await grain_and_reshape(page, ck, vault)


NODES = "JSON.stringify([...document.querySelectorAll('aside .imp-node-head b')].map((b) => b.textContent.trim()))"


async def grain_and_reshape(page, ck, vault: Path) -> None:
    """2026-10-13：拆前选「粗」进提示词；卡上勾两个点合并、看得见谁改的、能撤回。"""
    await E.click_text(page, "aside [data-grain] button", "粗")
    await paste(page, ELF, "ELF")
    await E.click_text(page, "aside .imp-run .btn", "拆成知识点")
    await E.poll(page, NODES, lambda v: len(json.loads(v)) == 3, timeout=10)
    ck.add("选的粒度进了提示词", "只拆 2～4 个节点" in PROMPTS[-1], PROMPTS[-1][-300:])
    used = await page.ev("document.querySelector('aside [data-grain-used]')?.textContent.trim() || ''")
    ck.add("卡上标着这次用的粒度", used == "粒度：粗", used)

    await page.ev("document.querySelector('aside [data-pick=\"动态链接\"]').click()")
    await page.ev("document.querySelector('aside [data-pick=\"数据库扩展\"]').click()")
    await E.poll(page, "!!document.querySelector('aside [data-reshape]')", lambda v: v, timeout=4)
    await page.ev("document.querySelector('aside [data-act=\"merge\"]').click()")
    await E.poll(page, NODES, lambda v: json.loads(v) == ["ELF", "动态链接"], timeout=10)
    note = await page.ev("document.querySelector('aside [data-reshaped]')?.textContent || ''")
    ck.add("合并后卡上说清谁改的、怎么改的", "fake-learn" in note and "当动态链接的例子" in note, note)
    ck.add("合并只发了被勾的两个点的全文", '"id": "数据库扩展"' in PROMPTS[-1] and '"id": "ELF"' not in PROMPTS[-1],
           PROMPTS[-1][:300])
    await E.poll(page, "[...document.querySelectorAll('aside .cc-diff b')].map((b) => b.textContent).join('|')",
                 lambda v: "数据库扩展" not in v and "动态链接" in v, timeout=8)
    diff = await page.ev("[...document.querySelectorAll('aside .cc-diff')].map((p) => p.textContent).join('\\n')")
    ck.add("重算后的 diff 里，ELF 正文的链接改指合并后的点", "[[动态链接]]" in diff and "[[数据库扩展]]" not in diff,
           diff[:400])

    await page.ev("document.querySelector('aside [data-act=\"undo-reshape\"]').click()")
    await E.poll(page, NODES, lambda v: len(json.loads(v)) == 3, timeout=8)
    ck.add("撤回回到合并前三个点", True, "")
    ck.add("调整只改卡不写盘", not (vault / "nodes/测试/动态链接.md").exists(), "")


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
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-import-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "KNOWRARY_LLM_CONFIG": str(fake_learn(tmp)), "NO_PROXY": "127.0.0.1,localhost",
               "no_proxy": "127.0.0.1,localhost"}
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
