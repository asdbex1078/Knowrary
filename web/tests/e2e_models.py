#!/usr/bin/env python3
"""设置 →「模型」这一页的端到端自测：真无头 Chrome，真点按钮。

    .venv/bin/python web/tests/e2e_models.py

复用 e2e_canvas.py 的那套壳（起临时 vault + 独立端口的服务 + Chrome/CDP），
但**单开一个文件**：这里要往 vault 里塞 llm.local.json、还要起一个假的 OpenAI 端点，
混进画布那条长流水会打乱它靠顺序维系的那些前提。

为什么非要真浏览器：2026-09-22 这一页上四个按钮全哑了，而源码逐行读下来处处成立——
1. props 声明成 `saveLLM` / `testLLM`，模板上写的是 `:save-llm`，Vue camelize 出来是
   `saveLlm` / `testLlm`，**永远对不上**。拿到的是 default null，于是「测试」一声不吭地
   return、「保存配置」报「保存接口不可用」。这种错编译不报、lint 不报，只有点一下才知道。
2. App 那侧的 saveLLMConfig 没 return，编辑器把 undefined 当成"没存住"——弹窗不关、
   也不报错，可配置其实已经落盘了。
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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_canvas as E  # noqa: E402  (壳子全在这儿：free_port / launch_chrome / Page / get)


class FakeOpenAI(BaseHTTPRequestHandler):
    """假的 /chat/completions：连得上、回一句 OK。真要打 dashscope，这套测试就没法在本机跑。"""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps({"choices": [{"message": {"content": "OK"}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


CLICK = """(() => {
  const el = [...document.querySelectorAll(%s)].find((b) => %s);
  if (!el) return 'missing';
  el.click();
  return 'clicked';
})()"""

SET_FIELD = """(() => {
  const set = (el, v) => {
    if (!el) return false;
    const d = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');
    d.set.call(el, v);                       // v-model 认的是原生 setter + input 事件
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  };
  const labs = [...document.querySelectorAll('.model-editor-body > label')];
  const by = (t) => labs.find((l) => l.textContent.includes(t))?.querySelector('input');
  const miss = Object.entries(%s).filter(([k, v]) => !set(by(k), v)).map(([k]) => k);
  return miss.length ? 'missing:' + miss.join(',') : 'ok';
})()"""


async def poll(page, expr, ok, timeout: float = 20.0):
    deadline = time.time() + timeout
    val = None
    while time.time() < deadline:
        val = await page.ev(expr)
        if ok(val):
            return val
        await asyncio.sleep(0.3)
    return val


async def click(page, sel: str, pred: str) -> None:
    out = await page.ev(CLICK % (json.dumps(sel), pred))
    assert out == "clicked", f"点不到 {sel} 里满足 {pred} 的那个：{out}"


async def row_click(page, name: str, title: str) -> None:
    """点某一行（按 provider 名字认行）上的某个图标按钮。"""
    out = await page.ev("""(() => {
      const row = [...document.querySelectorAll('.model-list-row')].find(
        (r) => r.querySelector('.model-list-name').textContent.includes(%s));
      if (!row) return 'missing-row';
      const b = [...row.querySelectorAll('.icon-btn')].find((x) => x.title.includes(%s));
      if (!b) return 'missing-btn';
      b.click(); return 'clicked';
    })()""" % (json.dumps(name), json.dumps(title)))
    assert out == "clicked", f"点不到 `{name}` 那一行的「{title}」：{out}"


async def fill(page, fields: dict) -> None:
    out = await page.ev(SET_FIELD % json.dumps(fields, ensure_ascii=False))
    assert out == "ok", f"编辑器里没找到这些字段：{out}"


def config_of(api: str) -> dict:
    cfg = E.get(api + "/api/llm/config")
    return {"names": [p["name"] for p in cfg["providers"]], "roles": cfg["roles"],
            "providers": {p["name"]: p for p in cfg["providers"]}}


async def open_models_tab(page) -> None:
    await click(page, "button.icon-btn", "b.title.startsWith('设置')")
    assert await poll(page, "!!document.querySelector('.set-dialog')", bool, 8), "设置弹窗没打开"
    await click(page, ".set-tab", "b.textContent.includes('模型')")
    assert await poll(page, "document.querySelectorAll('.model-list-row').length",
                      lambda v: v and v >= 2, 8), "模型列表没渲染出已有的 provider"


async def case_list_test(page, ck) -> None:
    """列表行上那个 ▶：点了必须给个说法。props 名字对不上时它是一声不吭地 return。"""
    await click(page, ".model-list-row .icon-btn", "b.title.includes('测试')")
    said = await poll(page, "document.querySelector('.set-body .model-success, "
                            ".set-body .model-error')?.textContent || ''", bool, 25)
    ck.add("列表里点 ▶ 测试，屏幕上要有回音", "连接成功" in (said or ""), said or "（点了什么都没发生）")


async def case_add(page, ck, api: str, stub: str) -> None:
    """添加模型 → 测试连接 → 保存：三步都要有反馈，最后要真落盘。"""
    await click(page, ".model-head .btn", "b.textContent.includes('添加模型')")
    assert await poll(page, "!!document.querySelector('.model-editor')", bool, 8), "编辑器没打开"

    geo = json.loads(await page.ev("""JSON.stringify((() => {
      const f = document.querySelector('.model-editor-foot'), d = document.querySelector('.model-editor');
      const fr = f.getBoundingClientRect(), dr = d.getBoundingClientRect();
      return { inside: fr.bottom <= dr.bottom + 1, onScreen: fr.bottom <= window.innerHeight + 1,
               h: Math.round(fr.height) };
    })())"""))
    # max-height + overflow:hidden 而不摆成竖向 flex 的话，内容一长这一排按钮就被裁掉，
    # 屏幕上根本没有「测试连接」和「保存」可点。
    ck.add("编辑器底栏那排按钮没被裁掉", geo["inside"] and geo["onScreen"] and geo["h"] > 0, str(geo))

    await fill(page, {"Provider 名称": "e2e-new", "模型名称": "stub-model",
                      "Base URL": stub, "API Key": "sk-e2e"})
    await asyncio.sleep(0.4)
    await click(page, ".model-editor-foot .btn", "b.textContent.includes('测试连接')")
    said = await poll(page, "document.querySelector('.model-editor .model-success, "
                            ".model-editor .model-error')?.textContent || ''", bool, 25)
    ck.add("编辑器里点「测试连接」，屏幕上要有回音", "连接成功" in (said or ""), said or "（点了什么都没发生）")

    await click(page, ".model-editor-foot .btn", "b.textContent.trim() === '保存'")
    closed = await poll(page, "!document.querySelector('.model-editor')", bool, 25)
    # 存住了却不关弹窗，人只会再点一次；这一条专门钉 saveLLMConfig 的返回值
    ck.add("保存成功后编辑器要自己关掉", bool(closed), "" if closed else await page.ev(
        "document.querySelector('.model-editor .model-error')?.textContent || '（没报错，也不关）'"))
    cfg = config_of(api)
    ck.add("新增的 provider 真落盘了", "e2e-new" in cfg["names"], str(cfg["names"]))
    ck.add("别人的密钥没被空串洗掉", cfg["providers"].get("bailian", {}).get("api_key_set") is True,
           str(cfg["providers"].get("bailian")))


async def case_rename(page, ck, api: str) -> None:
    """改名：roles 还指着旧名字的话服务端会 422，前端得把 roles 一起迁过去。"""
    before = config_of(api)
    assert set(before["roles"].values()) == {"bailian"}, f'前提变了：roles 不再指着 bailian（{before["roles"]}）'
    await row_click(page, "qwen-plus", "编辑")       # 列表上显示的是 model，bailian 那一行
    assert await poll(page, "!!document.querySelector('.model-editor')", bool, 8), "编辑器没打开"
    head = await page.ev("document.querySelector('.model-editor-head b')?.textContent")
    assert head == "编辑模型", f"点「编辑」开出来的却是 {head}"
    await fill(page, {"Provider 名称": "bailian-renamed"})
    await asyncio.sleep(0.4)
    await click(page, ".model-editor-foot .btn", "b.textContent.trim() === '保存'")
    closed = await poll(page, "!document.querySelector('.model-editor')", bool, 25)
    err = await page.ev("document.querySelector('.model-error')?.textContent || ''")
    ck.add("改名能存下去", bool(closed), err or ("" if closed else "（弹窗没关，也没报错）"))
    cfg = config_of(api)
    ck.add("改名之后 roles 跟着指过去",
           "bailian-renamed" in cfg["names"] and set(cfg["roles"].values()) == {"bailian-renamed"},
           f'{cfg["names"]} / {cfg["roles"]}')


async def case_bottom_save(page, ck, api: str) -> None:
    """底部「保存配置」：`@click="saveModels"` 会把 MouseEvent 当成第一个参数灌进去。"""
    await row_click(page, "claude-cli", "删除")
    await asyncio.sleep(0.3)
    await click(page, ".model-actions .btn", "b.textContent.includes('保存配置')")
    await asyncio.sleep(1.5)
    err = await page.ev("document.querySelector('.set-body > .model-error')?.textContent || ''")
    ck.add("底部「保存配置」不该自己报错", not err, err)
    names = config_of(api)["names"]
    ck.add("删掉的那个真的没了", names == ["bailian-renamed", "e2e-new"], str(names))


async def scenarios(page, api: str, stub: str, results: list) -> None:
    ck = E.Check(api)
    await open_models_tab(page)
    await case_list_test(page, ck)
    await case_add(page, ck, api, stub)
    await case_rename(page, ck, api)
    await case_bottom_save(page, ck, api)
    results.extend(ck.items)


async def run(api: str, cdp: str, stub: str, results: list) -> None:
    import websockets

    pages = [t for t in E.get(cdp + "/json/list") if t["type"] == "page"]
    target = next((t for t in pages if api.split("//")[1] in t["url"]), pages[0])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = E.Page(ws, api)
        await page.call("Runtime.enable")
        await page.call("Page.enable")
        # 冷启动时 Chrome 偶尔停在 about:blank，自己导一次，别让用例去猜是哪里坏了
        if await page.ev("location.href") != api + "/":
            await page.call("Page.navigate", {"url": api + "/"})
            for _ in range(60):
                if await page.ev("document.readyState === 'complete' && !!document.querySelector('#app > *')"):
                    break
                await asyncio.sleep(0.5)
        await asyncio.sleep(2)
        await scenarios(page, api, stub, results)


def write_config(vault: Path, stub: str) -> None:
    (vault / ".knowrary" / "llm.local.json").write_text(json.dumps({
        "_说明": "注释行：读写都要原样留着",
        "providers": {
            "claude-cli": {"_说明": "兜底", "type": "claude-cli"},
            "bailian": {"type": "openai", "model": "qwen-plus", "base_url": stub, "api_key": "sk-old"},
        },
        "roles": {"learn": "bailian", "review": "bailian"},
    }, ensure_ascii=False), "utf-8")


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    if not Path(E.CHROME).exists():
        raise SystemExit(f"找不到 Chrome：{E.CHROME}")

    fake = HTTPServer(("127.0.0.1", 0), FakeOpenAI)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    stub = f"http://127.0.0.1:{fake.server_port}/v1"

    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-models-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        write_config(vault, stub)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault)}
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            E.wait_for(api + "/api/health")
            chrome, cdp = E.launch_chrome(tmp, api)
            time.sleep(3)
            asyncio.run(run(api, cdp, stub, results))
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
