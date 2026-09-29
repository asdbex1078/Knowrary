#!/usr/bin/env python3
"""写入前拦重复的端到端自测：真无头 Chrome。

    .venv/bin/python web/tests/e2e_relink.py

2026-09-29（项目化重构方案 §5A）：要新建的点图里可能已经有了——变更卡和导入面板上都标出来，
给一个「并进」：不新建，边改指已有节点，正文作为补充追加到它末尾，名字进它的别名。
这里验两条路都通；变更卡那条还验刷新回来还是并过的、能改回来。导入那条用本地的假 learn 接口。
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

import e2e_canvas as E  # noqa: E402  (壳子全在这儿：free_port / launch_chrome / Page / get)
from e2e_chat_sessions import settle  # noqa: E402

TS = "2026-09-28T10:00:00+08:00"
PLAN = {"shape": "multi", "summary": "s", "nodes": [
    {"id": "BPE分词", "name": "BPE分词", "desc": "d", "body": "从文章里拆出来的 BPE 笔记",
     "relations": [{"type": "依赖", "target": "甲", "confidence": 0.9}]}]}


class FakeLearn(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        out = json.dumps({"model": "fake", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(PLAN, ensure_ascii=False)}}]}).encode()
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
    cfg.write_text(json.dumps({"providers": {"fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                                      "model": "fake", "api_key": "x"}},
                               "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg


def seed(vault: Path) -> None:
    for nid in ("MHA机制", "子词分词-BPE"):
        (vault / f"nodes/组A/{nid}.md").write_text(
            f"---\nname: {nid}\nfield: 测试\ndesc: 已有的{nid}\n---\n# {nid}\n\n老正文\n", "utf-8")
    card = {"card_id": "c-dup", "into": None, "files": [], "changes": [
        {"type": "create_node", "source": "MHA", "path": "nodes/组A/MHA.md",
         "fields": {"name": "MHA", "field": "测试", "desc": "多头注意力"}, "body": "对话里讲的 MHA"},
        {"type": "add_edge", "source": "MHA", "relation": "依赖", "target": "乙"}]}
    d = vault / ".knowrary/chat/_scratch"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": TS, "role": "user", "text": "存一下", "session": "s1"},
            {"ts": TS, "role": "assistant", "text": "卡给你了", "session": "s1", "cards": [{"type": "card", "card": card}]}]
    (d / "2026-09.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


CARD = """JSON.stringify((() => { const c = document.querySelector('.change-card'); if (!c) return null;
  return { near: [...c.querySelectorAll('[data-near]')].map((x) => x.textContent.replace(/\\s+/g, '')),
           relinked: c.querySelector('[data-relinked]')?.textContent.replace(/\\s+/g, '') || '',
           diff: c.querySelector('.cc-file')?.textContent || '' } })())"""


async def card(page) -> dict:
    return json.loads(await page.ev(CARD) or "null") or {}


async def case_chat(page, ck, vault: Path) -> None:
    await settle(page)
    await E.poll(page, "!!document.querySelector('.change-card [data-near]')", bool, timeout=10)
    st = await card(page)
    ck.add("变更卡上标出「图里可能已经有 MHA 了」，候选是 MHA机制", len(st["near"]) == 1
           and "MHA机制" in st["near"][0] and "只差一个「机制」" in st["near"][0], str(st))
    await page.ev("document.querySelector('.change-card [data-act=\"relink\"]').click()")
    await E.poll(page, "!!document.querySelector('.change-card [data-relinked]')", bool, timeout=8)
    st = await card(page)
    ck.add("点「并进」：卡上写清并进了哪、做了什么", "并进了已有的「MHA机制」" in st["relinked"]
           and "正文追加到" in st["relinked"] and "进了「MHA机制」的别名" in st["relinked"] and not st["near"], str(st))
    ck.add("diff 换成了改 MHA机制，不再新建 MHA", "MHA机制.md" in st["diff"] and "nodes/组A/MHA.md" not in st["diff"],
           st["diff"][:160])
    ck.add("只改卡不写盘", not (vault / "nodes/组A/MHA.md").exists()
           and "对话里讲的 MHA" not in (vault / "nodes/组A/MHA机制.md").read_text("utf-8"), "")

    await page.call("Page.reload")
    await asyncio.sleep(2)
    await settle(page)
    await E.poll(page, "!!document.querySelector('.change-card [data-relinked]')", bool, timeout=10)
    st = await card(page)
    ck.add("刷新回来还是并过的", "MHA机制" in st["relinked"] and not st["near"], str(st))
    await page.ev("document.querySelector('.change-card [data-act=\"undo-relink\"]').click()")
    await E.poll(page, "!!document.querySelector('.change-card [data-near]')", bool, timeout=8)
    st = await card(page)
    ck.add("改回来：候选又回来了，diff 回到新建", bool(st["near"]) and not st["relinked"]
           and "nodes/组A/MHA.md" in st["diff"], str(st)[:200])

    await page.ev("document.querySelector('.change-card [data-act=\"relink\"]').click()")
    await E.poll(page, "!!document.querySelector('.change-card [data-relinked]')", bool, timeout=8)
    await page.ev("document.querySelector('.change-card .cc-acts .btn.primary').click()")
    await E.poll(page, "!!document.querySelector('.change-card.folded')", bool, timeout=10)
    md = (vault / "nodes/组A/MHA机制.md").read_text("utf-8")
    ck.add("写入后：边、补充、别名都落在 MHA机制 上，没新建 MHA",
           "- 依赖:: [[乙]]" in md and "对话里讲的 MHA" in md and "- MHA" in md
           and not (vault / "nodes/组A/MHA.md").exists(), md[-200:])


async def case_import(page, ck, vault: Path) -> None:
    await E.switch_mode(page, "全局图")
    await asyncio.sleep(0.8)
    await E.open_rail(page, "导入")
    await E.poll(page, "!!document.querySelector('aside .imp-src')", bool, timeout=6)
    await page.ev("""(() => { const set = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); };
      set(document.querySelector('aside .imp-text'), '# 分词\\n\\n一篇讲分词的文章');
      const i = document.querySelectorAll('aside .imp-form input'); set(i[0], '分词'); set(i[1], '测试');
      document.querySelector('aside [data-act="keep-article"]').click(); })()""")
    await asyncio.sleep(0.3)
    await E.click_text(page, "aside .imp-run .btn", "拆成知识点")
    note = await E.poll(page, "document.querySelector('aside [data-near]')?.textContent.replace(/\\s+/g, '') || ''",
                        bool, timeout=10)
    ck.add("导入面板上也标出「图里可能已经有它了」", "子词分词-BPE" in (note or "") and "用字几乎一样" in (note or ""), str(note))
    await page.ev("document.querySelector('aside [data-near] [data-act=\"relink\"]').click()")
    got = await E.poll(page, "document.querySelector('aside [data-relinked]')?.textContent.replace(/\\s+/g, '') || ''",
                       bool, timeout=10)
    ck.add("点「并进它」：标成不新建、并进已有的", "并进已有的「子词分词-BPE」" in (got or ""), str(got))
    await E.poll(page, "!!document.querySelector('aside .imp-card') && !document.querySelector('aside .imp-card .dim')?.textContent.includes('diff 是旧的')",
                 bool, timeout=8)
    await asyncio.sleep(0.5)
    await E.click_text(page, "aside .imp-card .btn", "写入")
    md = vault / "nodes/组A/子词分词-BPE.md"
    for _ in range(40):
        if "从文章里拆出来的 BPE 笔记" in md.read_text("utf-8"):
            break
        await asyncio.sleep(0.25)
    text = md.read_text("utf-8")
    ck.add("导入写入后：正文补进子词分词-BPE、边挂过去、名字进别名，没新建 BPE分词",
           "从文章里拆出来的 BPE 笔记" in text and "- 依赖:: [[甲]]" in text and "- BPE分词" in text
           and not list(vault.rglob("BPE分词.md")), text[-220:])


async def run(api: str, cdp: str, vault: Path, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await case_chat(page, ck, vault)
            await case_import(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-relink-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        seed(vault)
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
