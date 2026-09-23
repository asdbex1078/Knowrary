#!/usr/bin/env python3
"""设置 →「知识库」这一页的端到端自测：真无头 Chrome，真点按钮，真切库。

    .venv/bin/python web/tests/e2e_vault.py

复用 e2e_canvas.py 的壳（临时 vault + 独立端口的服务 + Chrome/CDP），但单开一个文件：
这里要把**用户级配置**（`~/.knowrary/config.json`）指到临时目录，还要在切库之后等整页重载，
混进画布那条长流水会打乱它靠顺序维系的前提。

为什么非要真浏览器：这一页的每个动作都跨了三层——props 传下去、接口写用户级配置、
成功后 `location.reload()`。props 名字 camelize 对不上（2026-09-22 在「模型」页栽过一次）、
reload 之后读回的还是旧库，这两类错编译不报、lint 不报，只有点一下才知道。
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
from e2e_models import CLICK, click, poll  # noqa: E402  (点按钮的那两个小工具，不重写第二遍)


async def open_vault_tab(page) -> None:
    await click(page, "button.icon-btn", "b.title.startsWith('设置')")
    assert await poll(page, "!!document.querySelector('.set-dialog')", bool, 8), "设置弹窗没打开"
    await click(page, ".set-tab", "b.textContent.includes('知识库')")
    assert await poll(page, "!!document.querySelector('.vault-now')", bool, 8), "「知识库」面板没渲染"


async def enter(page, name: str) -> None:
    """在目录选择器里点开某个子目录。"""
    out = await page.ev(CLICK % (json.dumps(".vault-row"),
                                 f"b.querySelector('.vault-name')?.textContent.trim() === {json.dumps(name)}"))
    assert out == "clicked", f"目录选择器里点不到 `{name}`：{out}"
    assert await poll(page, "document.querySelector('.model-editor-head .dim')?.textContent || ''",
                      lambda v: v and v.endswith(name), 8), f"没进到 {name} 这一层"


READY = "document.readyState === 'complete' && !!document.querySelector('#app > *')"


async def mark(page) -> None:
    """在当前文档上盖个戳。切库会 `location.reload()`，新文档上这个戳不存在——
    只靠 readyState 是等不到的：**刚点完的那一刻，旧文档的 readyState 还是 complete**，
    于是"等重载"立刻返回，后面的操作全打在一个正在被拆掉的页面上（时好时坏地挂）。"""
    await page.ev("window.__reloadMark = 1")


async def wait_mounted(page) -> None:
    for _ in range(60):
        if await page.ev(READY):
            return
        await asyncio.sleep(0.5)
    raise AssertionError("页面没能挂载")


async def wait_reload(page, api: str) -> None:
    """等旧文档被换掉、新文档挂好。配合 mark() 用。"""
    for _ in range(80):
        if await page.ev(f"typeof window.__reloadMark === 'undefined' && {READY}"):
            await asyncio.sleep(0.6)      # 让 onMounted 那几个 fetch 跑完
            return
        await asyncio.sleep(0.5)
    raise AssertionError("切库之后页面没能重新加载")


async def case_current(page, ck, api: str, vault: Path) -> None:
    """进来先认人：当前库要显示成绝对路径，而且和服务端说的是同一个。"""
    shown = await page.ev("document.querySelector('.vault-now b')?.textContent?.trim() || ''")
    ck.add("面板上显示当前库的绝对路径", shown == str(vault), shown or "（一个字都没显示）")
    ck.add("没被环境变量钉住时不报 pinned",
           not await page.ev("!!document.querySelector('.set-body .field-hint')"), shown)


async def type_articles(page, text: str) -> None:
    await page.ev(f"""(() => {{ const i = document.querySelector('.art-input');
      i.value = {json.dumps(text)}; i.dispatchEvent(new Event('input', {{ bubbles: true }})); }})()""")
    await asyncio.sleep(0.8)          # 防抖 300ms + 一次 dry_run


ART = """JSON.stringify({ input: document.querySelector('.art-input')?.value,
  bad: document.querySelector('.art-input')?.classList.contains('bad'),
  err: document.querySelector('.set-body .model-error')?.textContent.trim() || '',
  move: document.querySelector('.art-move')?.textContent.replace(/\\s+/g, '') || '',
  count: document.querySelector('.art-count')?.textContent.trim() || '',
  done: document.querySelector('.art-done')?.textContent.trim() || '',
  save: document.querySelector('.art-row .btn')?.disabled,
  options: [...document.querySelectorAll('#art-dir-options option')].map((o) => o.value) })"""


async def case_articles(page, ck, vault: Path) -> None:
    """原文目录：边打边校验（规则在服务端）、旧目录有文章要问一句搬不搬、搬完真在新目录里。"""
    assert await poll(page, "!!document.querySelector('.art-input')", bool, 8), "原文目录那一栏没渲染"
    st = json.loads(await page.ev(ART))
    ck.add("显示当前原文目录和篇数", st["input"] == "articles" and "2 篇" in st["count"], str(st))
    ck.add("候选里有库里现有的目录、没有 nodes", "长文区" in st["options"] and "nodes" not in st["options"],
           str(st["options"]))

    await type_articles(page, "nodes/长文")
    st = json.loads(await page.ev(ART))
    ck.add("填进节点层当场标红、说清为什么", st["bad"] and "节点" in st["err"] and st["save"], str(st))
    await type_articles(page, "/Users/x/文章")
    st = json.loads(await page.ev(ART))
    ck.add("绝对路径也当场拒", st["bad"] and "相对路径" in st["err"], str(st))

    await type_articles(page, "长文区/2026")
    st = json.loads(await page.ev(ART))
    ck.add("旧目录有文章：先问一句要不要一起搬", "2篇原文" in st["move"] and "一起搬过去" in st["move"], str(st))
    ck.add("还没点就一篇没动", len(list((vault / "articles").rglob("*.md"))) == 2, "")
    await click(page, ".art-move .btn", "b.textContent.includes('一起搬过去')")
    assert await poll(page, "document.querySelector('.art-done')?.textContent || ''", bool, 8), "搬完没提示"
    st = json.loads(await page.ev(ART))
    moved = sorted(p.relative_to(vault).as_posix() for p in (vault / "长文区" / "2026").rglob("*.md"))
    ck.add("点了一起搬：文章真到了新目录", moved == ["长文区/2026/BPE全景.md", "长文区/2026/旧/注意力.md"], str(moved))
    ck.add("提示里写了搬了几篇、快照在哪", "2 篇" in st["done"] and ".knowrary/backup/articles-move-" in st["done"],
           st["done"])
    cfg = json.loads((vault / ".knowrary" / "vault.json").read_text("utf-8"))
    ck.add("配置写进了库里的 vault.json", cfg.get("articles_dir") == "长文区/2026", str(cfg))


async def case_init(page, ck, api: str, blank: Path) -> None:
    """选一个空目录 → 初始化 → 自动切过去。这是新用户的第一步，错一步就进不了门。"""
    await click(page, ".model-head .btn", "b.textContent.includes('选择目录')")
    assert await poll(page, "!!document.querySelector('.vault-picker')", bool, 8), "目录选择器没打开"
    here = await page.ev("document.querySelector('.model-editor-head .dim')?.textContent || ''")
    # 开在当前库旁边，不是家目录——库在别的分支上时，从家目录根本走不过去
    ck.add("选择器默认停在当前库旁边", here == str(blank.parent), here)

    flag = await page.ev("""(() => {
      const row = [...document.querySelectorAll('.vault-row')].find(
        (r) => r.querySelector('.vault-name')?.textContent.trim() === %s);
      return row ? row.querySelector('.vault-flag')?.textContent.trim() : 'missing';
    })()""" % json.dumps(blank.name))
    ck.add("空目录标成「空」", flag == "空", flag)

    await enter(page, blank.name)
    label = await page.ev("document.querySelector('.vault-foot .btn')?.textContent?.trim() || ''")
    ck.add("空目录上按钮是「选定并初始化」", "选定并初始化" in label, label)
    # 示例开关只在空目录上出现（已经是库的目录铺示例没有意义），默认不勾
    box = json.loads(await page.ev("""JSON.stringify((() => {
      const el = document.querySelector('.vault-sample input');
      return { there: !!el, checked: el ? el.checked : null };
    })())"""))
    ck.add("空目录上给出「放一份示例内容」，默认不勾", box["there"] and box["checked"] is False,
           json.dumps(box))
    await mark(page)
    await click(page, ".vault-foot .btn", "true")
    await wait_reload(page, api)

    now = E.get(api + "/api/vault")
    ck.add("初始化之后当前库换成了新目录", now["current"]["path"] == str(blank), str(now["current"]))
    ck.add("骨架建出来了", all((blank / rel).is_dir() for rel in
                            ("nodes", "fields", "assets", ".knowrary/layouts")),
           str(sorted(p.name for p in blank.iterdir())))
    ck.add("种子铺下去了", (blank / "relation-types.json").is_file() and (blank / ".gitignore").is_file(),
           str(sorted(p.name for p in blank.iterdir())))
    ck.add("新库是空的（没把别人的节点带过来）",
           E.get(api + "/api/index")["stats"]["nodes"] == 0,
           str(E.get(api + "/api/index")["stats"]))


async def case_switch_back(page, ck, api: str, vault: Path) -> None:
    """从「最近使用」切回原来的库：图要跟着换回来，不是还停在新库上。"""
    await open_vault_tab(page)
    rows = await poll(page, "document.querySelectorAll('.set-body .model-list-row').length", bool, 8)
    ck.add("最近使用里出现了上一个库", bool(rows),
           f"{rows} 行" + (await page.ev("document.querySelector('.set-body')?.innerText || ''") if not rows else ""))
    await mark(page)
    out = await page.ev(CLICK % (json.dumps(".set-body .model-list-row .btn"),
                                 "b.textContent.includes('切换')"))
    assert out == "clicked", f"点不到「切换」：{out}"
    await wait_reload(page, api)
    now = E.get(api + "/api/vault")
    ck.add("切回了原来的库", now["current"]["path"] == str(vault), str(now["current"]))
    ck.add("图跟着换回来了", E.get(api + "/api/index")["stats"]["nodes"] > 0,
           str(E.get(api + "/api/index")["stats"]))


async def case_sample(page, ck, api: str, sample: Path) -> None:
    """勾上示例再初始化：打开就该有图可点，而不是一片空白。"""
    await open_vault_tab(page)
    await click(page, ".model-head .btn", "b.textContent.includes('选择目录')")
    assert await poll(page, "!!document.querySelector('.vault-picker')", bool, 8), "目录选择器没打开"
    await enter(page, sample.name)
    out = await page.ev("""(() => {
      const el = document.querySelector('.vault-sample input');
      if (!el) return 'missing';
      el.click(); return el.checked ? 'on' : 'off';
    })()""")
    ck.add("示例开关点得动", out == "on", out)
    await mark(page)
    await click(page, ".vault-foot .btn", "true")
    await wait_reload(page, api)
    stats = E.get(api + "/api/index")["stats"]
    ck.add("勾了示例就真有图", stats["nodes"] == 9 and stats["stubs"] == 1, str(stats))
    ck.add("5 个关系族都有", set(stats["by_family"]) == {"结构", "依赖", "演化", "对照", "弱关联"},
           str(stats["by_family"]))
    cells = await poll(page, "document.querySelectorAll('[data-cell-id]').length", lambda v: v and v > 5, 15)
    ck.add("画布上真的画出来了", bool(cells and cells > 5), f"{cells} 个图元")


async def case_occupied(page, ck, busy: Path) -> None:
    """非空又不像库的目录：按钮必须是灰的——往别人的工程里撒文件是不可逆的。"""
    await open_vault_tab(page)
    await click(page, ".model-head .btn", "b.textContent.includes('选择目录')")
    assert await poll(page, "!!document.querySelector('.vault-picker')", bool, 8), "目录选择器没打开"
    await enter(page, busy.name)
    state = json.loads(await page.ev("""JSON.stringify({
      disabled: document.querySelector('.vault-foot .btn')?.disabled,
      why: document.querySelector('.vault-foot .dim')?.textContent?.trim() || '',
    })"""))
    ck.add("非空目录上「选定」是灰的", state["disabled"] is True, json.dumps(state, ensure_ascii=False))
    ck.add("并且说清为什么", "换一个空目录" in state["why"], state["why"])


async def scenarios(page, api: str, paths: dict, results: list) -> None:
    ck = E.Check(api)
    await open_vault_tab(page)
    await case_current(page, ck, api, paths["vault"])
    await case_articles(page, ck, paths["vault"])
    await case_init(page, ck, api, paths["blank"])
    await case_switch_back(page, ck, api, paths["vault"])
    await case_sample(page, ck, api, paths["sample"])
    await case_occupied(page, ck, paths["busy"])
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


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    if not Path(E.CHROME).exists():
        raise SystemExit(f"找不到 Chrome：{E.CHROME}")

    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-vault-") as tmpdir:
        tmp = Path(tmpdir).resolve()        # macOS 的 /var 是指向 /private/var 的符号链接
        vault = E.make_vault(tmp)
        (vault / "articles" / "旧").mkdir(parents=True)
        (vault / "articles" / "BPE全景.md").write_text("# BPE 全景\n", "utf-8")
        (vault / "articles" / "旧" / "注意力.md").write_text("# 注意力\n", "utf-8")
        (vault / "长文区").mkdir()
        blank = tmp / "新库"
        blank.mkdir()
        sample = tmp / "带示例的库"
        sample.mkdir()
        busy = tmp / "别人的工程"
        busy.mkdir()
        (busy / "pom.xml").write_text("<project/>", "utf-8")
        # 用户级配置指到临时目录：既不碰真实的 ~/.knowrary，也让 tmp 落进"可浏览"的名单
        # （放行的是已选库的上级目录，见 vaults._roots）
        home = tmp / "home"
        home.mkdir()
        (home / "config.json").write_text(json.dumps(
            {"schema_version": 1, "current": str(vault), "recent": [str(vault)]}), "utf-8")
        env = {k: v for k, v in os.environ.items() if k != "KNOWRARY_VAULT"}
        env["KNOWRARY_HOME"] = str(home)    # KNOWRARY_VAULT 必须不设：设了就 pinned，切库会被钉住
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            E.wait_for(api + "/api/health")
            chrome, cdp = E.launch_chrome(tmp, api)
            time.sleep(3)
            asyncio.run(run(api, cdp, {"vault": vault, "blank": blank, "sample": sample, "busy": busy}, results))
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
