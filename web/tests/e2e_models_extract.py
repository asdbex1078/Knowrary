#!/usr/bin/env python3
"""抽成模型谱系的端到端自测：真无头 Chrome + 本地假 learn / review 接口。

    .venv/bin/python web/tests/e2e_models_extract.py

2026-09-30（模型谱系第 2 步）：原文面板里按子目录分组，「这 N 篇一起抽成模型谱系」→ 没指定模型目录先选目录
→ 一篇一篇抽（看得见第几篇、等了几秒）→ 预览（新增 / 补格 / 冲突 / 可疑）→ 可疑的格子 review 核对、
一键按原文改 → 勾掉不要的 → 写入（只补不覆盖、写前快照）。
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

import e2e_canvas as E  # noqa: E402

DS = "# DeepSeek 迭代史\n\n## V3\n2024年12月26日发布，671B 总参数、37B 激活，预训练 14.8T tokens，用了 MLA。\n"
QW = "# Qwen 迭代史\n\n## Qwen2.5\n2024年12月19日发布，最大 72B。\n"
EXTRACT = {
    "DeepSeek": {"families": [{"name": "DeepSeek", "open": True, "tagline": "极致 MoE", "models": [
        {"name": "DeepSeek-V3", "date": "2024-12-26", "params": "671B", "active": "37B",
         "tokens": "15T tokens", "tech": ["[[甲]]", "FP8"], "highlight": "训练成本很低"}]}]},
    "Qwen": {"families": [{"name": "Qwen", "models": [{"name": "Qwen2.5", "date": "2024-12-19", "params": "72B"}]}]},
}
VERIFY = {"checks": [{"i": 0, "verdict": "wrong", "why": "原文是 14.8T", "quote": "预训练 14.8T tokens",
                      "suggest": "14.8T tokens"}]}
CALLS: list = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][0]["content"]
        verify = "核对一份**模型目录**" in prompt
        CALLS.append("verify" if verify else "extract")
        time.sleep(1.5)                  # 让进度条看得见
        article = prompt.split("## 文章")[-1].split("## 输出")[0]
        body = VERIFY if verify else EXTRACT["DeepSeek" if "DeepSeek" in article else "Qwen"]
        out = json.dumps({"model": "fake-llm", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                          "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def fake_llm(tmp: Path) -> Path:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = tmp / "llm.json"
    cfg.write_text(json.dumps({"providers": {"fake": {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                                                      "model": "fake-llm", "api_key": "x"}},
                               "roles": {"learn": "fake", "review": "fake"}}), "utf-8")
    return cfg


TEXT = "(sel) => (document.querySelector(sel)?.textContent || '').replace(/\\s+/g, '')"


async def text(page, sel: str) -> str:
    return await page.ev(f"({TEXT})({json.dumps(sel)})") or ""


async def case_extract(page, ck, vault: Path) -> None:
    await E.open_rail(page, "原文")
    head = await E.poll(page, f"({TEXT})('aside [data-group=\"llm\"] .art-group-head')", bool, timeout=8)
    ck.add("原文按子目录分组，组上给「这 2 篇一起抽成模型谱系」", "llm/" in (head or "") and "这2篇一起抽成模型谱系" in (head or ""), str(head))
    await page.ev("document.querySelector('aside [data-group=\"llm\"] [data-act=\"extract-models-all\"]').click()")
    await E.poll(page, "!!document.querySelector('aside [data-models-dir]')", bool, timeout=6)
    ck.add("没指定模型目录：先让人选目录，带格式样例", "kind:family" in await text(page, "aside .models-sample pre"), "")
    ck.add("还没选目录就没调模型", CALLS == [], str(CALLS))
    await page.ev("document.querySelector('aside [data-act=\"models-dir-save\"]').click()")
    bar = await E.poll(page, f"({TEXT})('aside [data-models-run]')", lambda v: "第1/2篇" in (v or ""), timeout=6)
    ck.add("一篇一篇抽，看得见谁在抽、第几篇、等了几秒", "learn在抽《" in (bar or "") and "已等" in (bar or ""), str(bar))
    ver = await E.poll(page, f"({TEXT})('aside [data-models-run]')", lambda v: "review在核对" in (v or ""), timeout=12)
    ck.add("有可疑的格子才调 review，核对也看得见", "review在核对1格" in (ver or ""), str(ver))
    row = await E.poll(page, f"({TEXT})('aside [data-plan=\"DeepSeek-V3\"]')", lambda v: "写错了" in (v or ""), timeout=10)
    ck.add("预览：新增、摘要、可疑的格子挂着 review 的结论和原文引句",
           "新增" in row and "2024-12-26·671B/激活37B" in row and "原文里找不到15" in row and "预训练14.8Ttokens" in row, row[:200])
    ck.add("两篇抽两次、核对一次", CALLS == ["extract", "extract", "verify"], str(CALLS))


async def case_write(page, ck, vault: Path) -> None:
    await page.ev("document.querySelector('aside [data-plan=\"DeepSeek-V3\"] [data-act=\"fix\"]').click()")
    await asyncio.sleep(0.3)
    row = await text(page, "aside [data-plan=\"DeepSeek-V3\"]")
    ck.add("一键按原文改：计划里的值换了，改完按钮收起", "训练数据「14.8Ttokens」" in row and "改成" not in row, row[:200])
    await page.ev("document.querySelector('aside [data-plan=\"Qwen2.5\"] [data-act=\"pick\"]').click()")
    btn = await E.poll(page, f"({TEXT})('aside [data-act=\"write-models\"]')", lambda v: "写入1个型号" in (v or ""), timeout=4)
    ck.add("勾掉一个，写入按钮跟着变成 1 个", "写入1个型号" in (btn or ""), str(btn))
    await page.ev("document.querySelector('aside [data-act=\"write-models\"]').click()")
    done = await E.poll(page, f"({TEXT})('aside [data-models-done]')", bool, timeout=8)
    ck.add("写完说清写了几个、写进哪、快照在哪", "1个型号" in (done or "") and "models/DeepSeek.md" in (done or "")
           and ".knowrary/backup/models-" in (done or ""), str(done))
    md = (vault / "models/DeepSeek.md").read_text("utf-8")
    ck.add("家族文件：kind: family、型号一节、按原文改过的值、出处", "kind: family" in md and "## DeepSeek-V3" in md
           and "- 训练数据:: 14.8T tokens" in md and "[[articles/llm/deepseek/ds]]" in md, md[:300])
    ck.add("勾掉的家族不写", not (vault / "models/Qwen.md").exists(), "")
    got = E.get(ck.api + "/api/models")
    v3 = next((m for f in got["families"] for m in f["models"] if m["name"] == "DeepSeek-V3"), {})
    ck.add("写完就读得出来，技术点对上了图里的知识点", v3.get("tech", [{}])[0].get("node") == "甲", str(v3.get("tech")))


async def run(api: str, cdp: str, vault: Path, results: list) -> None:
    import websockets

    targets = E.get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        ck = E.Check(api)
        try:
            await E.wait_render(page, 4)
            await case_extract(page, ck, vault)
            await case_write(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-models-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        for rel, body in (("articles/llm/deepseek/ds.md", DS), ("articles/llm/qwen/qw.md", QW)):
            (vault / rel).parent.mkdir(parents=True, exist_ok=True)
            (vault / rel).write_text(body, "utf-8")
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "KNOWRARY_LLM_CONFIG": str(fake_llm(tmp)), "NO_PROXY": "127.0.0.1,localhost",
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
