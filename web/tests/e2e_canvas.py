#!/usr/bin/env python3
"""画布端到端自测：真无头 Chrome + 真 DOM 鼠标事件 → 验证 X6 交互是否真的落盘。

    .venv/bin/python web/tests/e2e_canvas.py

自己起一个临时 vault + 独立端口的服务实例，**不碰仓库里的 layout.json**；
用完自动清理。需要本机装有 Google Chrome（只读用它的无头模式）与 `web/dist`（先 npm run build）。

为什么要有它：阶段 2 的两个 bug 只有真拖拽才暴露得出来——
1. X6 `embedding.frontOnly` 默认 true，落点被其他节点挡住时"拖进分组"会丢归属；
2. 用 requestAnimationFrame 放开写入守卫，在后台标签页 / 无头浏览器里永不触发，改动被静默丢弃。
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机直连，绕过代理
VAULT_HOLDER: list = []      # 当前用例跑在哪个临时 vault 上（写回用例要直接读文件）

FILES = {
    "nodes/组A/甲.md": "---\nname: 甲\nfield: 测试\ndesc: 甲\n---\n# 甲\n\n正文\n\n## 关系\n- 部件:: [[乙]]\n",
    "nodes/组A/乙.md": "---\nname: 乙\nfield: 测试\ndesc: 乙\n---\n# 乙\n\n正文\n",
    "nodes/组B/丙.md": "---\nname: 丙\nfield: 测试\ndesc: 丙\n---\n# 丙\n\n正文\n\n## 关系\n- 依赖:: [[甲]]\n",
    "nodes/组B/丁.md": "---\nname: 丁\nfield: 测试\ndesc: 丁\n---\n# 丁\n\n正文\n\n## 关系\n- 依赖:: [[乙]]\n",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str) -> dict:
    with OPENER.open(urllib.request.Request(url)) as r:
        return json.loads(r.read())


def wait_for(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get(url)
            return
        except Exception:
            time.sleep(0.3)
    raise SystemExit(f"等不到 {url}")


def arch_prefix() -> list[str]:
    """Apple Silicon 上强制走 arm64 切片。

    .venv 里的 python 是 x86_64（Rosetta），子进程默认继承这个偏好，Chrome 就会跑 x64 切片，
    然后有相当概率启动到一半放弃、不写 DevToolsActivePort——就是"偶发启动失败"的真正来源。
    """
    if sys.platform != "darwin":
        return []
    translated = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True)
    native_arm = platform.machine() == "arm64" or translated.stdout.strip() == "1"
    return ["/usr/bin/arch", "-arm64"] if native_arm and Path("/usr/bin/arch").exists() else []


def launch_chrome(tmp: Path, api: str, attempts: int = 3):
    """启动无头 Chrome。冷启动偶发不写 DevToolsActivePort，换个 profile 重试即可。"""
    last = None
    for i in range(attempts):
        profile = tmp / f"chrome{i}"
        log = (tmp / f"chrome{i}.log").open("w")
        proc = subprocess.Popen(arch_prefix()
                                + [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                   "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                                   "--window-size=1600,1000", "--remote-debugging-port=0",
                                   f"--user-data-dir={profile}", api + "/"], stdout=log, stderr=log)
        try:
            return proc, devtools_base(profile, timeout=25.0)
        except SystemExit as exc:
            last = exc
            proc.terminate()
            time.sleep(1.5)
    raise SystemExit(f"Chrome 连续 {attempts} 次启动失败：{last}")


def devtools_base(profile: Path, timeout: float = 60.0) -> str:
    """Chrome 用 --remote-debugging-port=0 时会把真实端口写进 DevToolsActivePort；
    回环地址它可能绑 IPv4 也可能绑 IPv6，两种都试。"""
    marker = profile / "DevToolsActivePort"
    deadline = time.time() + timeout
    port = None
    while time.time() < deadline:
        if marker.exists():
            head = marker.read_text().splitlines()
            if head and head[0].strip().isdigit():
                port = int(head[0].strip())
                break
        time.sleep(0.3)
    if port is None:
        log = profile.parent / f"{profile.name}.log"
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-6:]) if log.exists() else "（无日志）"
        raise SystemExit(f"Chrome 没有写出 DevToolsActivePort，可能启动失败。日志尾部：\n{tail}")
    for host in ("127.0.0.1", "[::1]"):
        base = f"http://{host}:{port}"
        try:
            get(base + "/json/list")
            return base
        except Exception:
            continue
    raise SystemExit(f"DevTools 端口 {port} 连不上")


def make_vault(tmp: Path) -> Path:
    vault = tmp / "vault"
    (vault / ".knowrary").mkdir(parents=True)
    (vault / "relation-types.json").write_text((REPO / "relation-types.json").read_text("utf-8"), "utf-8")
    for rel, text in FILES.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    VAULT_HOLDER.clear()
    VAULT_HOLDER.append(vault)
    return vault


def drag_script(cell: str, *, to_cell: str | None = None, dx: int = 0, dy: int = 0, grab: str = "center") -> str:
    """在页面里派发一串真实的 mousedown / mousemove / mouseup。"""
    origin = "{ x: r.x + 40, y: r.y + 10 }" if grab == "title" else "{ x: r.x + r.width / 2, y: r.y + r.height / 2 }"
    target = (f"""const b = document.querySelector('[data-cell-id="{to_cell}"]');
        if (!b) return 'missing-dst';
        const br = b.getBoundingClientRect(); to = {{ x: br.x + br.width / 2, y: br.y + br.height / 2 }};"""
              if to_cell else f"to = {{ x: from.x + {dx}, y: from.y + {dy} }};")
    return f"""(() => {{
      const el = document.querySelector('[data-cell-id="{cell}"]');
      if (!el) return 'missing-src';
      const r = el.getBoundingClientRect();
      const from = {origin};
      let to;
      {target}
      // 事件必须派发到真实元素上：X6 在 mouseup 里会对 target 调 hasAttribute，
      // 派发到 document 会抛异常、中断它的清理，之后整张图都不再响应鼠标。
      const at = (x, y) => document.elementFromPoint(x, y) || document.body;
      const fire = (t, x, y, node) => (node || at(x, y)).dispatchEvent(new MouseEvent(t, {{
        bubbles: true, cancelable: true, clientX: x, clientY: y, view: window,
        button: 0, buttons: t === 'mouseup' ? 0 : 1 }}));
      const hit = document.elementFromPoint(from.x, from.y);
      const before = el.getBoundingClientRect().x;
      fire('mousedown', from.x, from.y, el.querySelector('rect') || el);
      for (let i = 1; i <= 10; i++) {{
        fire('mousemove', from.x + (to.x - from.x) * i / 10, from.y + (to.y - from.y) * i / 10, null);
      }}
      const during = el.getBoundingClientRect().x;
      fire('mouseup', to.x, to.y, null);
      return JSON.stringify({{ ok: true, hitTag: hit?.tagName,
        hitCell: hit?.closest('[data-cell-id]')?.getAttribute('data-cell-id') || null,
        movedOnScreen: Math.round(during - before) }});
    }})()"""


class Page:
    """极简 CDP 客户端：够用来 evaluate 与 reload。"""

    def __init__(self, ws, api: str = ""):
        self.ws = ws
        self.api = api
        self.n = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result", {})

    async def ev(self, expr: str):
        res = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if res.get("exceptionDetails"):
            raise AssertionError("页面 JS 异常：" + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:300])
        return res.get("result", {}).get("value")


async def wait_render(page: Page, expect_nodes: int, timeout: float = 20.0) -> int:
    """等 X6 把节点画出来再动手（冷启动的无头 Chrome 首屏可能要好几秒）。"""
    deadline = time.time() + timeout
    seen = 0
    while time.time() < deadline:
        seen = await page.ev("document.querySelectorAll('[data-shape=\"kg-node\"]').length") or 0
        if seen >= expect_nodes:
            return seen
        await asyncio.sleep(0.5)
    detail = await page.ev("JSON.stringify({ banner: document.querySelector('.banner')?.textContent,"
                           " err: window.__lastError || null, body: document.body.innerText.slice(0, 200) })")
    raise AssertionError(f"画布迟迟没渲染出 {expect_nodes} 个节点（当前 {seen}）；页面状态：{detail}")


async def drag(page: Page, *args, **kwargs) -> dict:
    out = await page.ev(drag_script(*args, **kwargs))
    assert out and out.startswith("{"), f"拖拽脚本返回 {out!r}（元素没找到？）"
    return json.loads(out)


class Check:
    """收集用例结论，最后统一打印。"""

    def __init__(self, api: str):
        self.api = api
        self.items: list[tuple[str, bool, str]] = []

    def layout(self) -> dict:
        return get(self.api + "/api/layout")["layout"]

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.items.append((name, ok, detail))


async def case_initial(page: Page, ck: Check) -> None:
    await wait_render(page, 4)
    base = ck.layout()
    ck.add("首次打开自动生成布局", base["revision"] == 1 and len(base["nodes"]) == 4,
           f"revision {base['revision']}，节点 {len(base['nodes'])}")


async def poll(page: Page, expr: str, ok, timeout: float = 8.0):
    """X6 开了异步渲染，DOM 不会立刻更新；轮询到条件成立或超时。"""
    deadline = time.time() + timeout
    value = None
    while time.time() < deadline:
        value = await page.ev(expr)
        if ok(value):
            return value
        await asyncio.sleep(0.3)
    return value


async def edge_counts(page: Page) -> dict:
    return json.loads(await page.ev("""JSON.stringify({
      total: document.querySelectorAll('.x6-edge').length,
      agg: [...document.querySelectorAll('.x6-edge')].filter(
        (e) => e.getAttribute('data-cell-id')?.startsWith('agg:')).length })"""))


async def case_aggregate(page: Page, ck: Check) -> None:
    """跨分组边默认聚合成一束；点它展开明细，再点收起。"""
    start = await edge_counts(page)
    ck.add("跨分组边默认聚合成束", start["agg"] >= 1 and start["total"] == start["agg"],
           f"画布 {start['total']} 条，其中聚合 {start['agg']} 束")
    click = """(() => {
      const agg = [...document.querySelectorAll('.x6-edge')].find(
        (e) => e.getAttribute('data-cell-id')?.startsWith('agg:'));
      if (!agg) return 'no-agg';
      const path = agg.querySelector('path');
      const r = path.getBoundingClientRect();
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      for (const t of ['mousedown', 'mouseup', 'click']) {
        path.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true,
          clientX: x, clientY: y, view: window, button: 0 }));
      }
      return agg.getAttribute('data-cell-id');
    })()"""
    bundle = await page.ev(click)
    expect_detail = start["total"] - start["agg"] + 2   # 这束里有 2 条明细
    await poll(page, "document.querySelectorAll('.x6-edge').length", lambda v: (v or 0) >= expect_detail)
    opened = await edge_counts(page)
    ck.add("点聚合束展开明细", opened["agg"] == start["agg"] - 1 and opened["total"] > start["total"],
           f"{bundle} → 明细 {opened['total'] - opened['agg']} 条，剩 {opened['agg']} 束")
    await page.ev(click.replace("startsWith('agg:')", "startsWith('agg:')"))  # 束已展开，重复点无副作用
    await asyncio.sleep(0.5)

    # 悬停高亮：无关边淡出
    await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="乙"]');
      const r = el.getBoundingClientRect();
      for (const t of ['mouseover', 'mouseenter']) {
        el.dispatchEvent(new MouseEvent(t, { bubbles: t === 'mouseover', clientX: r.x + 5, clientY: r.y + 5, view: window }));
      }
      return 'sent';
    })()""")
    dim = await poll(page, """[...document.querySelectorAll('.x6-edge path')].filter(
      (p) => parseFloat(p.getAttribute('opacity') || '1') < 0.2).length""", lambda v: (v or 0) > 0)
    ck.add("悬停节点高亮相关边、淡出其余", (dim or 0) > 0, f"淡出 {dim} 条")


async def case_drag_node(page: Page, ck: Check) -> dict:
    """节点拖进另一个分组：归属与坐标都要落盘，且一次拖动只写一次。"""
    before = ck.layout()
    await drag(page, "甲", to_cell="g-测试--组B")
    await asyncio.sleep(1.6)
    now = ck.layout()
    ck.add("拖节点跨分组改归属并落盘",
           now["nodes"]["甲"]["group"] == "g-测试--组B" and now["revision"] == before["revision"] + 1,
           f"分组 {now['nodes']['甲']['group']}，revision {before['revision']} → {now['revision']}")
    return now


async def case_drag_group(page: Page, ck: Check, prev: dict) -> dict:
    """拖分组：X6 带着子节点一起走，服务端一次收齐。"""
    gid = "g-测试--组A"
    kids = [k for k, v in prev["nodes"].items() if v["group"] == gid]
    g0 = prev["groups"][gid]
    k0 = {k: (prev["nodes"][k]["x"], prev["nodes"][k]["y"]) for k in kids}
    info = await drag(page, gid, dx=120, dy=-60, grab="title")
    await asyncio.sleep(1.6)
    now = ck.layout()
    gd = (round(now["groups"][gid]["x"] - g0["x"]), round(now["groups"][gid]["y"] - g0["y"]))
    kd = {k: (round(now["nodes"][k]["x"] - k0[k][0]), round(now["nodes"][k]["y"] - k0[k][1])) for k in kids}
    ck.add("拖分组带子节点批量落盘",
           gd != (0, 0) and all(d == gd for d in kd.values()) and now["revision"] == prev["revision"] + 1,
           f"分组位移 {gd}，子节点 {kd}，revision {now['revision']}，"
           f"抓取点命中 {info['hitTag']}/{info['hitCell']}，拖动中屏幕位移 {info['movedOnScreen']}")
    return now


async def case_noop_and_reload(page: Page, ck: Check, prev: dict) -> None:
    """原地拖不写盘；刷新后位置与归属保持，且刷新本身不写盘。"""
    await drag(page, "丁", dx=0, dy=0)
    await asyncio.sleep(1.2)
    quiet = ck.layout()
    ck.add("无位移不产生空写", quiet["revision"] == prev["revision"], f"revision {quiet['revision']}")
    await page.call("Page.enable")
    await page.call("Page.reload", {"ignoreCache": True})
    await wait_render(page, 4)
    after = ck.layout()
    ck.add("刷新后位置保持且刷新不写盘",
           after["revision"] == quiet["revision"] and after["nodes"]["甲"]["group"] == "g-测试--组B",
           f"revision {after['revision']}，甲 分组 {after['nodes']['甲']['group']}")


async def press(page: Page, key: str, shift: bool = False) -> None:
    await page.ev(f"""window.dispatchEvent(new KeyboardEvent('keydown',
      {{ key: '{key}', metaKey: true, shiftKey: {str(shift).lower()}, bubbles: true }}))""")


async def case_undo_redo(page: Page, ck: Check) -> None:
    """拖动可撤销：撤销 = 再发一个把状态改回去的 PATCH，所以 revision 继续前进但坐标回退。"""
    start = ck.layout()
    origin = (round(start["nodes"]["丁"]["x"]), round(start["nodes"]["丁"]["y"]))
    await drag(page, "丁", dx=140, dy=90)
    await asyncio.sleep(1.6)
    moved = ck.layout()
    shifted = (round(moved["nodes"]["丁"]["x"]), round(moved["nodes"]["丁"]["y"]))
    ck.add("拖动后坐标变化（撤销前提）", shifted != origin, f"{origin} → {shifted}")

    await press(page, "z")
    await asyncio.sleep(2.0)
    undone = ck.layout()
    back = (round(undone["nodes"]["丁"]["x"]), round(undone["nodes"]["丁"]["y"]))
    ck.add("⌘Z 撤销拖动", back == origin and undone["revision"] == moved["revision"] + 1,
           f"回到 {back}，revision {moved['revision']} → {undone['revision']}")

    await press(page, "z", shift=True)
    await asyncio.sleep(2.0)
    redone = ck.layout()
    again = (round(redone["nodes"]["丁"]["x"]), round(redone["nodes"]["丁"]["y"]))
    ck.add("⇧⌘Z 重做", again == shifted, f"回到 {again}")


async def case_recover_from_crash(page: Page, ck: Check) -> None:
    """画布出错后必须能继续用。

    这里故意用"把 mousemove/mouseup 派发到 document"的序列——X6 在这种事件上会抛
    `e.hasAttribute is not a function`，异常会打断它的内部清理，历史上会导致整张图
    再也不响应鼠标。App 里的自愈逻辑应当重建画布，之后拖拽照常生效。
    """
    crash = """(() => {
      const el = document.querySelector('[data-cell-id="丙"]');
      const r = el.getBoundingClientRect();
      const fire = (t, x, y, node) => node.dispatchEvent(new MouseEvent(t, {
        bubbles: true, cancelable: true, clientX: x, clientY: y, view: window,
        button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      fire('mousedown', r.x + 20, r.y + 15, el.querySelector('rect'));
      for (let i = 1; i <= 6; i++) fire('mousemove', r.x + 20 + 24 * i, r.y + 15 + 12 * i, document);
      fire('mouseup', r.x + 160, r.y + 90, document);
      return 'crashed';
    })()"""
    await page.ev(crash)
    await asyncio.sleep(2.0)
    before = ck.layout()
    await drag(page, "丁", dx=90, dy=70)
    await asyncio.sleep(1.8)
    after = ck.layout()
    ck.add("画布崩溃后自动恢复、拖拽仍然落盘", after["revision"] > before["revision"],
           f"revision {before['revision']} → {after['revision']}")


async def case_edit_relation(page: Page, ck: Check, vault: Path) -> None:
    """阶段 3 主链路：点节点 → 改关系 → 预览（不写盘）→ 确认写回 md（自动备份）。"""
    md = vault / "nodes/组A/甲.md"
    before = md.read_text("utf-8")
    # 选中节点：点它
    await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="甲"]');
      const r = el.getBoundingClientRect();
      const at = (x, y) => document.elementFromPoint(x, y) || document.body;
      const fire = (t, x, y) => at(x, y).dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true,
        clientX: x, clientY: y, view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      fire('mousedown', x, y); fire('mouseup', x, y);
      return 'clicked';
    })()""")
    got_panel = await poll(page, "!!document.querySelector('aside pre.raw, aside .add-edge')", lambda v: v)
    ck.add("点节点出详情面板（含 md 原文入口）", bool(got_panel),
           await page.ev("document.querySelector('aside h3')?.textContent || '无面板'"))

    # 填新增关系表单 → 加入变更
    await page.ev("""(() => {
      const set = (el, v) => { el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true })); };
      const box = document.querySelector('.add-edge');
      set(box.querySelector('select'), '相关');
      set(box.querySelector('input'), '丙');
      return 'filled';
    })()""")
    # Vue 要一个 tick 才会把「加入变更」从 disabled 解除，不能在同一段脚本里立刻点
    await poll(page, "!document.querySelector('.add-edge button').disabled", lambda v: v)
    await page.ev("document.querySelector('.add-edge button').click()")
    await asyncio.sleep(0.6)
    queued = await page.ev("document.querySelectorAll('aside.changes ul.edges li').length")
    ck.add("变更进入待写回列表", queued == 1, f"{queued} 条")

    # 预览：必须不碰 md
    await page.ev("""[...document.querySelectorAll('aside.changes button')]
      .find((b) => b.textContent.includes('预览变更')).click()""")
    await poll(page, "!!document.querySelector('aside pre.diff')", lambda v: v)
    diff = await page.ev("document.querySelector('aside pre.diff')?.textContent || ''")
    ck.add("预览出 diff 且不写盘", "相关" in (diff or "") and md.read_text("utf-8") == before,
           f"diff {len(diff or '')} 字，md 未变 {md.read_text('utf-8') == before}")

    # 确认写入
    await page.ev("""[...document.querySelectorAll('aside.changes button')]
      .find((b) => b.textContent.includes('确认写入')).click()""")
    await poll(page, "document.querySelector('.banner')?.textContent || ''", lambda v: "已写回" in (v or ""))
    after = md.read_text("utf-8")
    backups = list((vault / ".knowrary" / "backup").glob("*/nodes/组A/甲.md"))
    ck.add("确认后写回 md 并备份原文",
           "- 相关:: [[丙]]" in after and before.split("## 关系")[0] == after.split("## 关系")[0] and backups,
           f"关系已写入 {'- 相关:: [[丙]]' in after}，正文未变 {before.split('## 关系')[0] == after.split('## 关系')[0]}，"
           f"备份 {len(backups)} 份")


async def case_search(page: Page, ck: Check) -> None:
    """搜索：输入关键字 → 点结果 → 该节点被选中并居中。"""
    await page.ev("""(() => {
      const input = document.querySelector('.search input');
      input.value = '丙';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return 'typed';
    })()""")
    hits = await poll(page, "document.querySelectorAll('.search ul.hits li').length", lambda v: (v or 0) > 0)
    await page.ev("document.querySelector('.search ul.hits li').click()")
    await asyncio.sleep(1.2)
    title = await page.ev("document.querySelector('aside h3')?.textContent || ''")
    ck.add("搜索命中并定位", (hits or 0) > 0 and "丙" in (title or ""), f"{hits} 条结果，面板标题「{title}」")


async def case_note_and_ref(page: Page, ck: Check, vault: Path) -> None:
    """便签与引用卡：只写 layout.json，不碰 md。"""
    md_before = sum(len(p.read_text("utf-8")) for p in sorted(vault.rglob("*.md")))
    await page.ev("window.prompt = () => '实验便签'")     # 避开原生弹窗
    await page.ev("""[...document.querySelectorAll('header button')]
      .find((b) => b.textContent.includes('便签')).click()""")
    await asyncio.sleep(1.6)
    layout = get(page.api + "/api/layout")["layout"] if hasattr(page, "api") else None
    ck.add("便签已落盘", bool(layout and layout["notes"] and layout["notes"][0]["text"] == "实验便签"),
           str(layout["notes"] if layout else "读不到"))
    ck.add("便签不碰 md", sum(len(p.read_text("utf-8")) for p in sorted(vault.rglob("*.md"))) == md_before,
           "md 总长度未变")

    # 引用卡：详情面板上的按钮（此时已选中某个节点）
    await page.ev("""(() => {
      const btn = [...document.querySelectorAll('aside button')].find((b) => b.textContent.includes('放引用卡'));
      if (!btn) return 'no-btn';
      btn.click(); return 'ok';
    })()""")
    await asyncio.sleep(1.6)
    layout2 = get(page.api + "/api/layout")["layout"]
    ck.add("引用卡已落盘且指向本体", bool(layout2["refs"]) and layout2["refs"][0]["target"],
           str(layout2["refs"]))
    rendered = await page.ev("document.querySelectorAll('[data-shape=\"kg-note\"],[data-shape=\"kg-ref\"]').length")
    ck.add("便签与引用卡画在画布上", (rendered or 0) >= 2, f"{rendered} 个")


async def case_focus_cluster(page: Page, ck: Check) -> None:
    """缩小出簇卡片 → 点一张放大进那个域 → Esc 回全景。"""
    await page.ev("__kg.graph.zoomTo(0.3)")
    clusters = await poll(page, "document.querySelectorAll('[data-shape=\"kg-cluster\"]').length",
                          lambda v: (v or 0) > 0)
    if not clusters:
        diag = await page.ev("""JSON.stringify({ zoom: +__kg.graph.zoom().toFixed(2),
          lastError: window.__lastError || null,
          banner: (document.querySelector('.banner')?.textContent || '').slice(0, 60),
          groups: Object.keys(__kg.layout.groups) })""")
        ck.add("缩小后折叠成簇卡片", False, f"一张都没有；现场：{diag}")
        return
    ck.add("缩小后折叠成簇卡片", True, f"{clusters} 张")
    await page.ev("""(() => {
      const el = document.querySelector('[data-shape="kg-cluster"]');
      const r = el.getBoundingClientRect();
      const at = (x, y) => document.elementFromPoint(x, y) || document.body;
      const fire = (t, x, y) => at(x, y).dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true,
        clientX: x, clientY: y, view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      fire('mousedown', x, y); fire('mouseup', x, y);
      return 'clicked';
    })()""")
    await asyncio.sleep(1.5)
    inside = await page.ev("""JSON.stringify({
      nodes: document.querySelectorAll('[data-shape="kg-node"]').length,
      clusters: document.querySelectorAll('[data-shape="kg-cluster"]').length,
      banner: (document.querySelector('.banner')?.textContent || '').slice(0, 20) })""")
    state = json.loads(inside)
    ck.add("点簇卡片放大进那个域", state["nodes"] > 0 and "已放大到" in state["banner"], inside)
    await page.ev("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    await asyncio.sleep(1.5)
    back = await page.ev("document.querySelectorAll('[data-shape=\"kg-cluster\"]').length")
    ck.add("Esc 回到全景（重新折叠）", (back or 0) >= (clusters or 0), f"{back} 张簇卡片")


async def case_render_complete(page: Page, ck: Check) -> None:
    """模型里的边必须条条落到 DOM：X6 异步渲染曾经在连续重排时丢 cell。"""
    counts = json.loads(await page.ev("""JSON.stringify({
      model: window.__kg ? __kg.graph.getEdges().length : -1,
      dom: document.querySelectorAll('.x6-edge').length })"""))
    ck.add("模型边数 = DOM 边数（渲染没丢 cell）", counts["model"] == counts["dom"],
           f"模型 {counts['model']}，DOM {counts['dom']}")


async def case_rendered(page: Page, ck: Check) -> None:
    # 前一个用例把画布留在"全景折叠"状态，先放大回来再数
    await page.ev("__kg.graph.zoomTo(1)")
    await wait_render(page, 4)
    counts = json.loads(await page.ev("""JSON.stringify({
      groups: document.querySelectorAll('[data-shape="kg-group"]').length,
      nodes: document.querySelectorAll('[data-shape="kg-node"]').length,
      edges: document.querySelectorAll('.x6-edge').length,
      status: document.querySelector('.status')?.textContent })"""))
    ck.add("画布渲染出分组/节点/边",
           counts["groups"] >= 3 and counts["nodes"] == 4 and counts["edges"] >= 1,
           f"分组 {counts['groups']}，节点 {counts['nodes']}，边 {counts['edges']}，状态 {counts['status']}")


# ---------------------------------------------------------------- 阶段 4：Inbox / 放置 / 欠账 / 复习

NEW_MD = ("---\nname: {n}\nfield: 测试\ndesc: {n} 的摘要\nlearned: 2020-01-01\n---\n"
          "# {n}\n\n正文\n\n## 关系\n- 部件:: [[{to}]]\n")


async def click_text(page: Page, sel: str, text: str) -> str:
    out = await page.ev(f"""(() => {{
      const b = [...document.querySelectorAll({sel!r})].find((x) => x.textContent.includes({text!r}));
      if (!b) return 'missing';
      b.click(); return 'ok';
    }})()""")
    assert out == "ok", f"点不到「{text}」（{sel}）：{out}"
    return out


async def md_size(vault: Path) -> int:
    return sum(len(p.read_text("utf-8")) for p in sorted(vault.rglob("*.md")))


async def case_inbox_place(page: Page, ck: Check, vault: Path) -> None:
    """新写的 md 进 Inbox → 「放进去」落成草稿；全程不碰 md。"""
    (vault / "nodes/组A/戊.md").write_text(NEW_MD.format(n="戊", to="甲"), "utf-8")
    before = await md_size(vault)
    await click_text(page, "header button", "重新加载")
    label = await poll(page, """[...document.querySelectorAll('header button')]
      .find((b) => b.textContent.trim().startsWith('Inbox'))?.textContent || ''""",
                       lambda v: "1" in (v or ""))
    ck.add("新写的节点进 Inbox", "1" in (label or ""), f"按钮上写着「{(label or '').strip()}」")

    await click_text(page, "header button", "Inbox")
    item = await poll(page, """(() => {
      const li = document.querySelector('aside.inbox ul.inbox-list li');
      return li ? li.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: bool(v))
    host = ck.layout()["nodes"]["甲"]["group"]                 # 前面的拖拽用例可能把「甲」挪过组
    host_name = ck.layout()["groups"][host]["name"]
    ck.add("Inbox 列出它并给出建议分组（跟着邻居走）",
           "戊" in (item or "") and host_name in (item or ""),
           f"{(item or '')[:70]}（「甲」在{host_name}）")

    await click_text(page, "aside.inbox ul.inbox-list li button", "放进去")
    await poll(page, "'x'", lambda _: "戊" in ck.layout()["nodes"], timeout=12)
    node = ck.layout()["nodes"].get("戊")
    same_group = bool(node) and node["group"] == host
    ck.add("放进建议分组并标成草稿", bool(node) and node["state"] == "draft" and same_group,
           f"{node}")
    ck.add("放置不碰 md", await md_size(vault) == before, "md 总长度未变")
    shown = await poll(page, "document.querySelectorAll('[data-cell-id=\"戊\"]').length", lambda v: (v or 0) > 0)
    ck.add("草稿画在画布上", (shown or 0) > 0, f"{shown} 个 cell")


async def case_due_badge(page: Page, ck: Check) -> None:
    """learned 是 2020 年 → 早该复习；节点右上角点亮金色圆点，面板上能记一次复习。"""
    fill = await poll(page, """document.querySelector('[data-cell-id="戊"] circle')?.getAttribute('fill') || ''""",
                      lambda v: v and v != "transparent")
    ck.add("到期节点亮出复习圆点", (fill or "").lower() == "#e0891f", f"circle fill = {fill}")

    await click_text(page, "header button", "欠账")
    text = await poll(page, """(() => {
      const a = document.querySelector('aside.digest');
      return a ? a.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "待复习" in v, timeout=12)
    ck.add("欠账清单列出草稿与待复习", "草稿" in (text or "") and "戊" in (text or ""), (text or "")[:110])

    await click_text(page, "aside.digest ul.edges li button", "✓")
    banner = await poll(page, "document.querySelector('.banner')?.textContent || ''",
                        lambda v: "复习" in (v or ""), timeout=12)
    ck.add("记一次复习后到期列表少一个", "第 1 次复习" in (banner or ""), (banner or "").strip()[:60])
    gone = await poll(page, """document.querySelector('[data-cell-id="戊"] circle')?.getAttribute('fill') || ''""",
                      lambda v: v == "transparent")
    ck.add("复习完圆点熄灭", gone == "transparent", f"circle fill = {gone}")


async def case_drag_from_inbox(page: Page, ck: Check, vault: Path) -> None:
    """从 Inbox 拖到画布：落在哪个分组框里就归哪个组，坐标就是松手的位置。"""
    (vault / "nodes/组B/己.md").write_text(NEW_MD.format(n="己", to="丙"), "utf-8")
    await click_text(page, "header button", "重新加载")
    await poll(page, "document.querySelectorAll('aside.inbox ul.inbox-list li').length", lambda v: (v or 0) >= 1)

    # 落点直接由分组框算：前面的用例会把节点拖来拖去，拿节点当参照物不可靠
    dropped = await page.ev("""(() => {
      const g = window.__kg.graph, lay = window.__kg.layout;
      const gid = Object.keys(lay.groups).find((k) => lay.groups[k].parent);   // 任一二级分组
      const box = lay.groups[gid];
      const p = g.localToClient(box.x + box.w / 2, box.y + box.h - 40);
      const dt = new DataTransfer();
      dt.setData('text/knowrary-node', '己');
      document.querySelector('.canvas').dispatchEvent(new DragEvent('drop', {
        dataTransfer: dt, clientX: Math.round(p.x), clientY: Math.round(p.y),
        bubbles: true, cancelable: true }));
      return JSON.stringify({ gid, x: Math.round(p.x), y: Math.round(p.y) });
    })()""")
    ck.add("拖放事件已派发", (dropped or "").startswith("{"), str(dropped))
    await poll(page, "'x'", lambda _: "己" in ck.layout()["nodes"], timeout=12)
    node = ck.layout()["nodes"].get("己")
    want = json.loads(dropped)["gid"] if (dropped or "").startswith("{") else None
    ck.add("拖到哪个分组框就归哪个组", bool(node) and node["group"] == want,
           f"落在 {node and node['group']}，期望 {want}")


async def case_finalize(page: Page, ck: Check) -> None:
    """草稿确认位置后定稿：金色虚线框变回正常卡片，只改 layout。"""
    dashed = await poll(page, """document.querySelector('[data-cell-id="己"] rect')
      ?.getAttribute('stroke-dasharray') || ''""", lambda v: bool(v))
    ck.add("草稿画成虚线框", bool(dashed), f"stroke-dasharray = {dashed}")
    await page.ev("""(() => {
      const cell = document.querySelector('[data-cell-id="己"]');
      const r = cell.getBoundingClientRect();
      cell.querySelector('rect').dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true,
        clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, view: window, button: 0, buttons: 1 }));
      cell.querySelector('rect').dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true,
        clientX: r.x + r.width / 2, clientY: r.y + r.height / 2, view: window, button: 0, buttons: 0 }));
      return 'clicked';
    })()""")
    await poll(page, "document.querySelector('aside h3')?.textContent || ''", lambda v: "己" in (v or ""))
    await click_text(page, "aside button", "定稿")
    state = await poll(page, "'x'", lambda _: ck.layout()["nodes"].get("己", {}).get("state") == "final", timeout=12)
    del state
    ck.add("定稿后不再是草稿", ck.layout()["nodes"]["己"]["state"] == "final", str(ck.layout()["nodes"]["己"]))


# ---------------------------------------------------------------- 阶段 5：贴图与手工拐点

PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                        "890000000a49444154789c6360000002000100fdff03fa0000000049454e44ae426082")


async def case_image(page: Page, ck: Check, vault: Path) -> None:
    """贴图：选 assets/ 里的图 → 画布上出现 → 位置进 layout.images，md 不动。"""
    (vault / "assets").mkdir(exist_ok=True)
    (vault / "assets" / "示意图.png").write_bytes(PNG_1PX)
    before = await md_size(vault)
    await click_text(page, "header button", "＋图片")
    thumbs = await poll(page, "document.querySelectorAll('aside.picker ul.thumbs li').length",
                        lambda v: (v or 0) > 0, timeout=12)
    ck.add("贴图面板列出 assets/ 里的图", (thumbs or 0) == 1, f"{thumbs} 张")
    await page.ev("document.querySelector('aside.picker ul.thumbs li').click()")
    await poll(page, "'x'", lambda _: bool(ck.layout()["images"]), timeout=12)
    img = ck.layout()["images"][0]
    ck.add("图片位置落进 layout.images", img["file"] == "示意图.png" and img["w"] == 320, str(img))
    ck.add("贴图不碰 md", await md_size(vault) == before, "md 总长度未变")
    drawn = await poll(page, "document.querySelectorAll('[data-shape=\"kg-image\"]').length",
                       lambda v: (v or 0) > 0)
    ck.add("图片画在画布上", (drawn or 0) > 0, f"{drawn} 个")
    href = await page.ev("""document.querySelector('[data-shape="kg-image"] image')
      ?.getAttribute('xlink:href') || document.querySelector('[data-shape="kg-image"] image')?.getAttribute('href') || ''""")
    ck.add("图片指向服务的 assets 接口", "/api/asset/" in (href or ""), href or "没取到 href")


async def case_edge_vertices(page: Page, ck: Check) -> None:
    """手工拐点：点边挂手柄 → 拖出拐点存进 layout.edges → 双击清掉。"""
    picked = await page.ev("""(() => {
      const e = [...document.querySelectorAll('.x6-edge')]
        .find((x) => !x.getAttribute('data-cell-id')?.startsWith('agg:'));
      if (!e) return '';
      return e.getAttribute('data-cell-id');
    })()""")
    ck.add("画布上有可编辑的普通边", bool(picked), picked or "只剩聚合束")
    if not picked:
        return

    # X6 的 click 是 mousedown + mouseup 合成的，直接派发 click 事件它不认
    await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="%s"] path');
      const r = el.getBoundingClientRect();
      const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
      for (const t of ['mousedown', 'mouseup']) {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, clientX: x, clientY: y,
          view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      }
      return 'clicked';
    })()""" % picked)
    tool = await poll(page, "document.querySelectorAll('.x6-edge-tool-vertex-path').length", lambda v: (v or 0) > 0)
    ck.add("点边挂上拐点手柄", (tool or 0) > 0, f"{tool} 条工具轨迹")

    # 拐点是在工具自己画的那条 path 上按下才生成的（x6-edge-tool-vertex-path），不是边本身的 path
    moved = await page.ev("""(() => {
      const tool = document.querySelector('.x6-edge-tool-vertex-path');
      if (!tool) return 'no-tool';
      const r = tool.getBoundingClientRect();
      const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
      const fire = (t, px, py, node) => (node || document.elementFromPoint(px, py) || document.body)
        .dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, clientX: px, clientY: py,
          view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      fire('mousedown', x, y, tool);                 // 在工具的 path 上按下 → 新建一个拐点
      for (let i = 1; i <= 6; i++) fire('mousemove', x, y - i * 8, null);
      fire('mouseup', x, y - 48, null);
      return JSON.stringify({ x, y });
    })()""")
    ck.add("在边上拖出拐点", (moved or "").startswith("{"), str(moved))
    await poll(page, "'x'", lambda _: bool(ck.layout()["edges"].get(picked, {}).get("vertices")), timeout=12)
    style = ck.layout()["edges"].get(picked, {})
    ck.add("拐点存进 layout.edges", bool(style.get("vertices")), str(style))

    await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="%s"] path');
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window,
        clientX: Math.round(r.x + r.width / 2), clientY: Math.round(r.y + r.height / 2) }));
      return 'ok';
    })()""" % picked)
    await poll(page, "'x'", lambda _: picked not in ck.layout()["edges"], timeout=12)
    ck.add("双击清掉手工拐点", picked not in ck.layout()["edges"], str(ck.layout()["edges"]))


# ---------------------------------------------------------------- 阶段 6：历史视图

async def case_history(page: Page, ck: Check, vault: Path) -> None:
    """历史视图：只有带 year 的节点进图、泳道与刻度、被激活金线、滑块回放、全程不写结构布局。"""
    (vault / "nodes/组A/甲.md").write_text(
        "---\nname: 甲\nfield: 测试\ndesc: 甲\nyear: 1990\n---\n# 甲\n\n正文\n\n"
        "## 关系\n- 被激活:: [[丙]] (2005)\n", "utf-8")
    (vault / "nodes/组B/丙.md").write_text(
        "---\nname: 丙\nfield: 测试\ndesc: 丙\nyear: 2005\n---\n# 丙\n\n正文\n", "utf-8")
    (vault / "nodes/组B/庚.md").write_text(
        "---\nname: 庚\nfield: 另一域\ndesc: 庚\nyear: 2015\n---\n# 庚\n\n正文\n", "utf-8")
    # 有效期节点：2000 年起、2010 年废止，用来验 F4.5
    (vault / "nodes/组B/辛.md").write_text(
        "---\nname: 辛\nfield: 另一域\ndesc: 辛\nyear: 2000\nstart_year: 2000\nend_year: 2010\n---\n"
        "# 辛\n\n正文\n", "utf-8")
    await click_text(page, "header button", "重新加载")
    await wait_render(page, 4)
    before = ck.layout()["revision"]

    await click_text(page, "header button", "历史视图")
    lanes = await poll(page, "document.querySelectorAll('[data-shape=\"kg-lane\"]').length",
                       lambda v: (v or 0) >= 2, timeout=15)
    ck.add("按 field 分出泳道", (lanes or 0) == 2, f"{lanes} 条泳道（测试 / 另一域）")
    ticks = await poll(page, "document.querySelectorAll('[data-shape=\"kg-tick\"]').length",
                       lambda v: (v or 0) >= 4, timeout=8)
    ck.add("X 轴画出年份刻度", (ticks or 0) == 4, f"{ticks} 个刻度（1990/2000/2005/2015）")
    shown = await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-node"]')]
      .map((e) => e.getAttribute('data-cell-id')).sort())""")
    ck.add("没有 year 的节点不进历史图", json.loads(shown) == ["丙", "庚", "甲", "辛"], shown)
    gold = await page.ev("document.querySelectorAll('.x6-edge path.kg-flow').length")
    ck.add("被激活画成金色流动虚线", (gold or 0) == 1, f"{gold} 条")

    # 只数个数抓不到"全叠在原点"这种错（CSS transform 会盖掉 SVG 的 transform 属性），
    # 所以要按屏幕坐标核对：年份越晚的节点越靠右，且彼此不重叠。
    rects = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-node"]')]
      .map((e) => { const r = e.getBoundingClientRect();
        return [e.getAttribute('data-cell-id'), Math.round(r.x), Math.round(r.y)]; }))"""))
    at = dict((r[0], r[1]) for r in rects)
    ordered = at.get("甲", 0) < at.get("丙", 0) < at.get("庚", 0)
    ck.add("节点按年份从左到右真的分开了", ordered and len({r[1] for r in rects}) == 4,
           f"甲 {at.get('甲')} < 丙 {at.get('丙')} < 庚 {at.get('庚')}")

    # 滑块拖到 1990：只剩当年之前的节点
    await page.ev("""(() => {
      const el = document.querySelector('.timeline-bar input[type="range"]');
      el.value = '1990';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return 'moved';
    })()""")
    left = await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length",
                      lambda v: v == 1, timeout=12)
    ck.add("时间滑块按年份过滤", left == 1, f"≤1990 时剩 {left} 个节点")

    # 有效期过滤：辛 2000 年起、2010 年废止，看 2015 年时它不该还在图上
    await page.ev("""(() => {
      const el = document.querySelector('.timeline-bar input[type="range"]');
      el.value = '2015';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return 'moved';
    })()""")
    await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length", lambda v: v == 4, timeout=12)
    await click_text(page, ".timeline-bar label", "有效期")
    ids = await poll(page, """JSON.stringify([...document.querySelectorAll('[data-shape="kg-node"]')]
      .map((e) => e.getAttribute('data-cell-id')).sort())""", lambda v: v and "辛" not in v, timeout=12)
    ck.add("有效期过滤掉当年已废止的节点", "辛" not in (ids or "x"), f"2015 年还在图上的是 {ids}")
    await click_text(page, ".timeline-bar label", "有效期")     # 关掉，别影响后面的用例

    # 切一条时间线：泳道换成所选分组的直接子分组
    await click_text(page, ".timeline-bar .chips button", "组B")
    lane_names = await poll(page, """JSON.stringify([...document.querySelectorAll('[data-shape="kg-lane"] text')]
      .map((t) => t.textContent))""", lambda v: v and v != "[]", timeout=12)
    ck.add("选中分组后泳道跟着换", "组B" in (lane_names or ""), lane_names or "没取到泳道名")

    ck.add("历史视图不修改结构布局", ck.layout()["revision"] == before,
           f"revision 仍是 {before}")

    await click_text(page, "header button", "结构视图")
    back = await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length",
                      lambda v: (v or 0) >= 4, timeout=15)
    ck.add("切回结构视图恢复原图", (back or 0) >= 4, f"{back} 个节点")


async def scenarios(page: Page, api: str, results: list) -> None:
    ck = Check(api)
    await case_initial(page, ck)
    await case_aggregate(page, ck)
    after_node = await case_drag_node(page, ck)
    after_group = await case_drag_group(page, ck, after_node)
    await case_noop_and_reload(page, ck, after_group)
    await case_undo_redo(page, ck)
    await case_recover_from_crash(page, ck)
    await case_edit_relation(page, ck, VAULT_HOLDER[0])
    await case_search(page, ck)
    await case_note_and_ref(page, ck, VAULT_HOLDER[0])
    await case_focus_cluster(page, ck)
    await case_render_complete(page, ck)
    await case_rendered(page, ck)
    await case_inbox_place(page, ck, VAULT_HOLDER[0])
    await case_due_badge(page, ck)
    await case_drag_from_inbox(page, ck, VAULT_HOLDER[0])
    await case_finalize(page, ck)
    await case_image(page, ck, VAULT_HOLDER[0])
    await case_edge_vertices(page, ck)
    await case_history(page, ck, VAULT_HOLDER[0])
    results.extend(ck.items)


async def run(api: str, cdp: str, results: list) -> None:
    import websockets  # .venv 里由 uvicorn[standard] 带入

    targets = get(cdp + "/json/list")
    target = next(t for t in targets if t["type"] == "page" and api.split("//")[1] in t["url"])
    async with websockets.connect(target["webSocketDebuggerUrl"], proxy=None, max_size=20_000_000) as ws:
        page = Page(ws, api)
        await page.call("Runtime.enable")
        await scenarios(page, api, results)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    if not Path(CHROME).exists():
        raise SystemExit(f"找不到 Chrome：{CHROME}")
    port = free_port()
    api = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-") as tmpdir:
        tmp = Path(tmpdir)
        vault = make_vault(tmp)
        env = {**os.environ, "KNOWRARY_VAULT": str(vault)}
        server = subprocess.Popen([str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "server.app:app",
                                   "--host", "127.0.0.1", "--port", str(port), "--app-dir", str(REPO)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        results: list[tuple[str, bool, str]] = []
        try:
            wait_for(api + "/api/health")
            chrome, cdp = launch_chrome(tmp, api)
            time.sleep(3)  # 等前端首屏渲染完
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
