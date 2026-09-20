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


def send(url: str, body: dict, method: str = "POST") -> dict:
    """给临时服务打一个写请求（用例里要先造点数据时用）。"""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method)
    req.add_header("Content-Type", "application/json")
    with OPENER.open(req) as r:
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

    async def ev(self, expr: str, await_promise: bool = False):
        # await_promise：表达式是 async IIFE 时必须开，否则拿回来的是 Promise 对象本身
        res = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                   "awaitPromise": await_promise})
        if res.get("exceptionDetails"):
            raise AssertionError("页面 JS 异常：" + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:300])
        return res.get("result", {}).get("value")


async def wait_render(page: Page, expect_nodes: int, timeout: float = 20.0,
                      shape: str = "kg-node") -> int:
    """等 X6 把节点画出来再动手（冷启动的无头 Chrome 首屏可能要好几秒）。

    历史视图里的知识点是圆点（kg-dot），不是卡片，所以形状要能指定。
    """
    deadline = time.time() + timeout
    seen = 0
    while time.time() < deadline:
        seen = await page.ev(f"document.querySelectorAll('[data-shape=\"{shape}\"]').length") or 0
        if seen >= expect_nodes:
            return seen
        await asyncio.sleep(0.5)
    detail = await page.ev("JSON.stringify({ banner: document.querySelector('.toast .toast-text')?.textContent,"
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
    """首屏：布局自动生成，全局图上四个节点都画出来了（要在「全局图」模式下数）。"""
    await wait_render(page, 4)
    base = ck.layout()
    ck.add("首次打开自动生成布局", base["revision"] == 1 and len(base["nodes"]) == 4,
           f"revision {base['revision']}，节点 {len(base['nodes'])}")


async def case_morning_brief(page: Page, ck: Check) -> None:
    """晨间简报：当天第一次打开弹一次，关掉之后当天不再弹。

    它有一层全屏遮罩，**必须在别的用例之前关掉**，否则后面所有真鼠标事件都会打在遮罩上。
    """
    text = await poll(page, """(() => {
      const b = document.querySelector('.brief');
      return b ? b.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "今天" in v, timeout=12)
    ck.add("开场自动弹晨间简报", bool(text) and "今天" in (text or ""), (text or "")[:70])
    ck.add("简报里写了今天要复习什么", "待复习" in (text or "") or "没有到期" in (text or ""),
           (text or "")[:70])

    await page.ev("""(() => {
      const b = [...document.querySelectorAll('.brief .btn')].find((x) => x.textContent.includes('待会儿'))
        || document.querySelector('.brief .icon-btn');
      b?.click(); return 'ok';
    })()""")
    gone = await poll(page, "!document.querySelector('.brief')", lambda v: v, timeout=8)
    ck.add("关掉简报后遮罩消失", bool(gone), f"still={not gone}")

    day = await page.ev("localStorage.getItem('knowrary-brief-day')")
    ck.add("当天只弹一次（记的是日期不是布尔）", bool(day) and "-" in str(day), str(day))


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
    """一对分组之间线不多时画真实连线，密了才并成一束；点束展开明细，再点收起。"""
    cross_ids = """JSON.stringify(__kg.graph.getEdges()
      .filter((e) => (e.getData() || {}).kind === 'edge')
      .filter((e) => (__kg.layout.nodes[e.getSourceCellId()] || {}).group
                  !== (__kg.layout.nodes[e.getTargetCellId()] || {}).group)
      .map((e) => e.id))"""
    thin = await edge_counts(page)
    cross = await page.ev(cross_ids)
    # 只有 2 条跨组边时**不聚合**：一根「组B → 组A」的灰线代替不了「丙 依赖 甲」，
    # 而人看图看的正是那些具体关系（按层分泳道之后几乎每条边都跨组，一刀切聚合等于整张图只剩灰线）
    ck.add("跨组边不多时照画真实连线", thin["agg"] == 0 and len(json.loads(cross)) == 2,
           f"聚合 {thin['agg']} 束，跨组明细 {cross}")

    # 加第三条 组B → 组A 的边，越过阈值：这时才该并成一束
    await page.ev("""__kg.write([{ type: 'add_edge', source: '丁', relation: '依赖', target: '甲' }])""",
                  await_promise=True)
    await page.ev("location.reload()")
    await asyncio.sleep(2.4)
    await poll(page, "!!window.__kg", lambda v: v is True, timeout=12)
    start = await edge_counts(page)
    cross = await page.ev(cross_ids)
    ck.add("同一对分组之间密了就并成一束", start["agg"] >= 1 and cross == "[]",
           f"画布 {start['total']} 条、聚合 {start['agg']} 束，漏网的跨组明细边 {cross}")
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
    expect_detail = start["total"] - start["agg"] + 3   # 这束里有 3 条明细
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

    # 收尾：把为了触发聚合加的那条边删掉，后面数边的用例才对得上
    await page.ev("""__kg.write([{ type: 'remove_edge', source: '丁', relation: '依赖', target: '甲' }])""",
                  await_promise=True)
    await page.ev("location.reload()")
    await asyncio.sleep(2.4)
    await poll(page, "!!window.__kg", lambda v: v is True, timeout=12)


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
    got_panel = await poll(page, "!!document.querySelector('.insp .node-title')", lambda v: v)
    ck.add("点节点出检查器（详情页）", bool(got_panel),
           await page.ev("document.querySelector('.insp .node-title')?.textContent || '无面板'"))
    await insp_tab(page, "关系")

    # 填新增关系表单 → 加入变更
    await page.ev("""(() => {
      const set = (el, v) => { el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true })); };
      const box = document.querySelector('.insp .form-grid');
      set(box.querySelector('select'), '相关');
      set(box.querySelector('input'), '丙');
      return 'filled';
    })()""")
    # Vue 要一个 tick 才会把「加入变更」从 disabled 解除，不能在同一段脚本里立刻点
    await poll(page, "!document.querySelector('.insp .form-grid button').disabled", lambda v: v)
    await page.ev("document.querySelector('.insp .form-grid button').click()")
    await asyncio.sleep(0.6)
    await insp_tab(page, "变更")
    queued = await page.ev("document.querySelectorAll('.insp .edge-row').length")
    ck.add("变更进入待写回列表", queued == 1, f"{queued} 条")

    # 预览：必须不碰 md
    await click_text(page, ".insp .act-row button", "预览 diff")
    await poll(page, "!!document.querySelector('.insp pre.diff')", lambda v: v)
    diff = await page.ev("document.querySelector('.insp pre.diff')?.textContent || ''")
    ck.add("预览出 diff 且不写盘", "相关" in (diff or "") and md.read_text("utf-8") == before,
           f"diff {len(diff or '')} 字，md 未变 {md.read_text('utf-8') == before}")

    # 确认写入
    await click_text(page, ".insp .act-row button", "确认写入")
    await poll(page, "document.querySelector('.toast .toast-text')?.textContent || ''", lambda v: "已写回" in (v or ""))
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
    hits = await poll(page, "document.querySelectorAll('.search .hits li').length", lambda v: (v or 0) > 0)
    await page.ev("""(() => { const li = document.querySelector('.search .hits li');
      li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })); })()""")
    await asyncio.sleep(1.2)
    title = await page.ev("document.querySelector('.insp .node-title')?.textContent || ''")
    ck.add("搜索命中并定位", (hits or 0) > 0 and "丙" in (title or ""), f"{hits} 条结果，面板标题「{title}」")


async def case_note_and_ref(page: Page, ck: Check, vault: Path) -> None:
    """便签与引用卡：只写 layout.json，不碰 md。"""
    md_before = sum(len(p.read_text("utf-8")) for p in sorted(vault.rglob("*.md")))
    await page.ev("window.prompt = () => '实验便签'")     # 避开原生弹窗
    await canvas_add(page, "便签")
    await asyncio.sleep(1.6)
    layout = get(page.api + "/api/layout")["layout"] if hasattr(page, "api") else None
    ck.add("便签已落盘", bool(layout and layout["notes"] and layout["notes"][0]["text"] == "实验便签"),
           str(layout["notes"] if layout else "读不到"))
    ck.add("便签不碰 md", sum(len(p.read_text("utf-8")) for p in sorted(vault.rglob("*.md"))) == md_before,
           "md 总长度未变")

    # 引用卡：详情面板上的按钮（此时已选中某个节点）
    await page.ev("""(() => {
      const btn = [...document.querySelectorAll('.insp button')].find((b) => b.textContent.includes('放引用卡'));
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
    # 折叠阈值按"卡片在屏幕上还剩多大"定（LEAF_ZOOM 0.28），0.3 还不折
    await page.ev("__kg.graph.zoomTo(0.2)")
    clusters = await poll(page, "document.querySelectorAll('[data-shape=\"kg-cluster\"]').length",
                          lambda v: (v or 0) > 0)
    if not clusters:
        diag = await page.ev("""JSON.stringify({ zoom: +__kg.graph.zoom().toFixed(2),
          lastError: window.__lastError || null,
          banner: (document.querySelector('.toast .toast-text')?.textContent || '').slice(0, 60),
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
      crumb: document.querySelector('.float.context .crumbs .cur')?.textContent || '',
      statusbar: document.querySelector('.statusbar .sb-btn')?.textContent || '' })""")
    state = json.loads(inside)
    ck.add("点簇卡片放大进那个域", state["nodes"] > 0 and bool(state["crumb"])
           and state["crumb"] in state["statusbar"], inside)
    await page.ev("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    await asyncio.sleep(1.5)
    back = await page.ev("document.querySelectorAll('[data-shape=\"kg-cluster\"]').length")
    crumb_gone = await page.ev("!document.querySelector('.float.context')")
    ck.add("Esc 回到全景（重新折叠、面包屑消失）", (back or 0) >= (clusters or 0) and bool(crumb_gone),
           f"{back} 张簇卡片，面包屑已消失 {bool(crumb_gone)}")


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
      status: document.querySelector('.save-state')?.textContent })"""))
    ck.add("画布渲染出分组/节点/边",
           counts["groups"] >= 3 and counts["nodes"] == 4 and counts["edges"] >= 1,
           f"分组 {counts['groups']}，节点 {counts['nodes']}，边 {counts['edges']}，状态 {counts['status']}")


# ---------------------------------------------------------------- 阶段 4：Inbox / 放置 / 欠账 / 复习

NEW_MD = ("---\nname: {n}\nfield: 测试\ndesc: {n} 的摘要\nlearned: 2020-01-01\n---\n"
          "# {n}\n\n正文\n\n## 关系\n- 部件:: [[{to}]]\n")


async def click_text(page: Page, sel: str, text: str) -> str:
    """点一个按钮。工具条上的下拉先展开——隐藏元素的 click() 也能触发，
    但那样测的就不是"用户点得到"，展开一下才算真链路。"""
    out = await page.ev(f"""(() => {{
      const b = [...document.querySelectorAll({sel!r})].find((x) => x.textContent.includes({text!r}));
      if (!b) return 'missing';
      b.click(); return 'ok';
    }})()""")
    assert out == "ok", f"点不到「{text}」（{sel}）：{out}"
    return out


async def use_scope(page: Page, project: str) -> None:
    """切作用域。左侧栏是按作用域过滤的：在项目下只给项目的工具，
    全局的（Inbox / 欠账 / 日历 / 项目管理）要先切到「🌐 全局」。"""
    await page.ev(f"""(() => {{
      const s = document.querySelector('.proj-switch');
      if (!s || s.value === {project!r}) return 'same';
      s.value = {project!r}; s.dispatchEvent(new Event('change', {{ bubbles: true }}));
      return 'ok';
    }})()""")
    await asyncio.sleep(0.8)


async def open_rail(page: Page, tip: str) -> str:
    """点左侧活动栏上的工具窗口图标（按 data-tip 里的名字找）。"""
    out = await page.ev(f"""(() => {{
      const b = [...document.querySelectorAll('.rail .rail-btn')]
        .find((x) => (x.dataset.tip || '').includes({tip!r}));
      if (!b) return 'missing';
      b.click(); return 'ok';
    }})()""")
    assert out == "ok", f"活动栏上点不到「{tip}」：{out}"
    await asyncio.sleep(0.4)
    return out


async def open_popover(page: Page, trigger_sel: str) -> None:
    """展开一个 Popover（顶栏 ⋯ / 画布工具条上的下拉）。"""
    out = await page.ev(f"""(() => {{
      const b = document.querySelector({trigger_sel!r});
      if (!b) return 'missing';
      b.click(); return 'ok';
    }})()""")
    assert out == "ok", f"点不到弹层触发器 {trigger_sel}：{out}"
    await asyncio.sleep(0.35)


async def menu_click(page: Page, text: str) -> None:
    """顶栏 ⋯ 菜单里的一项。"""
    await open_popover(page, '.topbar .pop-root:last-of-type .icon-btn')
    await click_text(page, ".pop-panel .pop-item", text)
    await asyncio.sleep(0.3)


async def canvas_add(page: Page, text: str) -> None:
    """画布工具条「＋」菜单里的一项（便签 / 图片）。"""
    out = await page.ev("""(() => {
      const b = [...document.querySelectorAll('.float.tools .icon-btn')]
        .find((x) => (x.title || '').includes('往画布上加东西'));
      if (!b) return 'missing';
      b.click(); return 'ok';
    })()""")
    assert out == "ok", f"点不到画布工具条的「＋」：{out}"
    await asyncio.sleep(0.35)
    await click_text(page, ".pop-panel .pop-item", text)
    await asyncio.sleep(0.3)


async def switch_mode(page: Page, text: str) -> None:
    """顶栏分段控件：对话 / 项目图 / 全局图 / 历史（三期从两个模式扩到四个）。"""
    await click_text(page, ".topbar .seg button", text)
    await asyncio.sleep(0.6)


async def insp_tab(page: Page, text: str) -> None:
    """右侧检查器的分页：详情 / 关系 / 变更。"""
    await click_text(page, ".insp .tabs button", text)
    await asyncio.sleep(0.35)


async def md_size(vault: Path) -> int:
    return sum(len(p.read_text("utf-8")) for p in sorted(vault.rglob("*.md")))


async def case_inbox_place(page: Page, ck: Check, vault: Path) -> None:
    """新写的 md 进 Inbox → 「放进去」落成草稿；全程不碰 md。"""
    (vault / "nodes/组A/戊.md").write_text(NEW_MD.format(n="戊", to="甲"), "utf-8")
    before = await md_size(vault)
    await menu_click(page, "重新加载")
    label = await poll(page, """[...document.querySelectorAll('.rail .rail-btn')]
      .find((b) => (b.dataset.tip || '').includes('Inbox'))?.querySelector('.badge')?.textContent || ''""",
                       lambda v: "1" in (v or ""))
    ck.add("新写的节点进 Inbox", "1" in (label or ""), f"活动栏角标「{(label or '').strip()}」")

    await open_rail(page, "Inbox")
    item = await poll(page, """(() => {
      const li = document.querySelector('aside.inbox .inbox-item');
      return li ? li.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: bool(v))
    host = ck.layout()["nodes"]["甲"]["group"]                 # 前面的拖拽用例可能把「甲」挪过组
    host_name = ck.layout()["groups"][host]["name"]
    ck.add("Inbox 列出它并给出建议分组（跟着邻居走）",
           "戊" in (item or "") and host_name in (item or ""),
           f"{(item or '')[:70]}（「甲」在{host_name}）")

    await page.ev("""(() => { const b = document.querySelector('aside.inbox .inbox-item [data-act="place"]');
      if (!b) return 'missing'; b.click(); return 'ok'; })()""")
    await poll(page, "'x'", lambda _: "戊" in ck.layout()["nodes"], timeout=12)
    node = ck.layout()["nodes"].get("戊")
    same_group = bool(node) and node["group"] == host
    ck.add("放进建议分组并标成草稿", bool(node) and node["state"] == "draft" and same_group,
           f"{node}")
    ck.add("放置不碰 md", await md_size(vault) == before, "md 总长度未变")
    shown = await poll(page, "document.querySelectorAll('[data-cell-id=\"戊\"]').length", lambda v: (v or 0) > 0)
    ck.add("草稿画在画布上", (shown or 0) > 0, f"{shown} 个 cell")


async def case_inbox_new_field(page: Page, ck: Check, vault: Path) -> None:
    """Inbox 增强（第五步）：零边节点显示警示 chip；新领域的节点给「建域框并放入」，点了在最下面开框、节点落进去。"""
    (vault / "nodes/新域").mkdir(parents=True, exist_ok=True)
    (vault / "nodes/新域/量子比特.md").write_text(
        "---\nname: 量子比特\nfield: 量子计算\ndesc: 量子比特的摘要\n---\n# 量子比特\n\n正文\n", "utf-8")
    await use_scope(page, "")
    await switch_mode(page, "全局图")
    await menu_click(page, "重新加载")
    await asyncio.sleep(1.0)
    if not await page.ev("!!document.querySelector('aside.inbox')"):
        await open_rail(page, "Inbox")               # open_rail 是开关：已经开着就别再点一下关掉
    row = await poll(page, """(() => {
      const li = [...document.querySelectorAll('aside.inbox .inbox-item')].find((x) => x.textContent.includes('量子比特'));
      return li ? li.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: bool(v), timeout=8)
    ck.add("零边节点的 Inbox 条目显示「0 关系 · 让 AI 建议」", "0 关系" in (row or "") and "让 AI 建议" in (row or ""), (row or "")[:80])
    ck.add("新领域的条目给「建域框并放入」而不是「需手动拖」", "建「量子计算」域框并放入" in (row or "") and "需手动拖" not in (row or ""), (row or "")[:80])
    before = await md_size(vault)
    await page.ev("""(() => {
      const li = [...document.querySelectorAll('aside.inbox .inbox-item')].find((x) => x.textContent.includes('量子比特'));
      li?.querySelector('[data-act="place-new-group"]')?.click();
    })()""")
    await poll(page, "'x'", lambda _: "量子比特" in ck.layout()["nodes"], timeout=12)
    lay = ck.layout()
    node = lay["nodes"].get("量子比特")
    box = lay["groups"].get(node["group"]) if node else None
    others = [g for gid, g in lay["groups"].items() if not g.get("parent") and gid != (node or {}).get("group")]
    ck.add("最下面开了「量子计算」顶层框，节点落进去成草稿",
           bool(box) and box["name"] == "量子计算" and box.get("parent") is None and node["state"] == "draft"
           and box["y"] >= max(g["y"] + g["h"] for g in others), f"node={node} box={box}")
    ck.add("建框不碰 md", await md_size(vault) == before, "md 总长度未变")
    shown = await poll(page, "document.querySelectorAll('[data-cell-id=\"量子比特\"]').length", lambda v: (v or 0) > 0)
    ck.add("放进去的点画在画布上", (shown or 0) > 0, f"{shown} 个 cell")
    await page.ev("""document.querySelector('aside .icon-btn[title*="收起"]')?.click()""")


async def case_import_panel(page: Page, ck: Check, vault: Path) -> None:
    """导入面板（第三步）：仓库里的文件能列出来、点一下正文进编辑框、来源名自动填。
    不点「拆成知识点」——那是一次真模型调用，e2e 的临时 vault 没配 LLM。"""
    (vault / "doc/读书笔记/一篇文章.md").parent.mkdir(parents=True, exist_ok=True)
    (vault / "doc/读书笔记/一篇文章.md").write_text("# 一篇文章\n\n这篇讲甲和乙。", "utf-8")
    await open_rail(page, "导入")
    opened = await poll(page, """!!document.querySelector('aside .imp-src')""", lambda v: bool(v), timeout=6)
    ck.add("活动栏有「导入」，点开是导入面板", bool(opened), "" if opened else "没打开")
    if not opened:
        return
    tabs = await page.ev("""JSON.stringify([...document.querySelectorAll('aside .imp-src .seg button')].map((b) => b.textContent.trim()))""")
    ck.add("三种来源：粘贴 / 仓库里的文件 / 本地文件", json.loads(tabs or "[]") == ["粘贴", "仓库里的文件", "本地文件"], str(tabs))
    await click_text(page, "aside .imp-src .seg button", "仓库里的文件")
    listed = await poll(page, """JSON.stringify([...document.querySelectorAll('aside .imp-file')].map((b) => b.title))""",
                        lambda v: v and "一篇文章" in v, timeout=8)
    ck.add("仓库文件列表里有 doc/ 下的那篇（节点目录不算素材）",
           "doc/读书笔记/一篇文章.md" in json.loads(listed or "[]") and not any(x.startswith("nodes/") for x in json.loads(listed or "[]")),
           str(listed))
    await page.ev("""[...document.querySelectorAll('aside .imp-file')].find((b) => b.title.includes('一篇文章'))
      ?.querySelector('.imp-file-pick')?.click()""")
    filled = await poll(page, """(() => {
      const t = document.querySelector('aside .imp-text'); const s = document.querySelector('aside .imp-form input');
      return t && s ? JSON.stringify([t.value, s.value]) : '';
    })()""", lambda v: v and "这篇讲甲和乙" in v, timeout=6)
    got = json.loads(filled or '["",""]')
    ck.add("点文件 → 正文进编辑框、来源名自动取文件名", "这篇讲甲和乙" in got[0] and got[1] == "一篇文章", str(got))

    # 第四步：批量队列。勾两篇 → 加入队列 → 两条「等着」+「开始」按钮；移出一条剩一条。不点开始（那是模型调用）
    (vault / "doc/读书笔记/第二篇.md").write_text("# 第二篇\n\n讲丙。", "utf-8")
    await page.ev("""document.querySelector('aside .icon-btn[title*="收起"]')?.click()""")
    await asyncio.sleep(0.3)
    await open_rail(page, "导入")
    await poll(page, """!!document.querySelector('aside .imp-src')""", lambda v: bool(v), timeout=6)
    await click_text(page, "aside .imp-src .seg button", "仓库里的文件")
    await poll(page, """document.querySelectorAll('aside .imp-file').length""", lambda v: (v or 0) >= 2, timeout=8)
    await page.ev("""[...document.querySelectorAll('aside .imp-file')]
      .filter((b) => b.title.includes('读书笔记')).forEach((b) => b.querySelector('input[type=checkbox]').click())""")
    await asyncio.sleep(0.2)
    await page.ev("""document.querySelector('aside [data-act="enqueue"]')?.click()""")
    rows = await poll(page, """JSON.stringify([...document.querySelectorAll('aside .imp-queue-item')]
      .map((li) => li.querySelector('.chip').textContent.trim() + ':' + li.querySelector('.nm').textContent.trim()))""",
                      lambda v: v and v.count("等着") == 2, timeout=6)
    ck.add("勾两篇加入队列 → 两条「等着」", sorted(json.loads(rows or "[]")) == ["等着:一篇文章", "等着:第二篇"], str(rows))
    has_run = await page.ev("""!!document.querySelector('aside [data-act="run"]')""")
    ck.add("队列有「开始（一篇一篇来）」而单篇按钮仍可用", bool(has_run), "" if has_run else "没有开始按钮")
    await page.ev("""document.querySelector('aside .imp-queue-item .icon-btn')?.click()""")
    left = await poll(page, """document.querySelectorAll('aside .imp-queue-item').length""", lambda v: v == 1, timeout=6)
    ck.add("移出一条后剩一条", left == 1, f"{left} 条")
    await page.ev("""document.querySelector('aside .icon-btn[title*="收起"]')?.click()""")


async def case_due_badge(page: Page, ck: Check) -> None:
    """learned 是 2020 年 → 早该复习；节点右上角点亮金色圆点，面板上能记一次复习。"""
    fill = await poll(page, """document.querySelector('[data-cell-id="戊"] circle')?.getAttribute('fill') || ''""",
                      lambda v: v and v != "transparent")
    ck.add("到期节点亮出复习圆点", (fill or "").lower() == "#e0891f", f"circle fill = {fill}")

    await open_rail(page, "欠账")
    text = await poll(page, """(() => {
      const a = document.querySelector('aside.digest');
      return a ? a.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "草稿" in v, timeout=12)
    ck.add("欠账清单列出草稿", "草稿" in (text or "") and "戊" in (text or ""), (text or "")[:110])

    # 复习不在欠账里了：图谱的欠账归欠账，"我该复习什么"归「学习」面板
    # 「学习计划」排在「学习」前面，只写"学习"会命中前者——按 tip 找必须给得够长
    await open_rail(page, "今日")
    study = await poll(page, """(() => {
      const a = document.querySelector('aside.study');
      return a ? a.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "待复习" in v, timeout=12)
    ck.add("学习面板列出待复习", "戊" in (study or ""), (study or "")[:110])

    # 「学习」面板上到期项是「考一下」（走测验），不想考、只想手记一笔的三档快捷在详情面板里。
    # 所以先点中这个节点，再点详情面板上的「记得」。
    await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="戊"]');
      const r = el.getBoundingClientRect();
      const at = (x, y) => document.elementFromPoint(x, y) || document.body;
      const fire = (t, x, y) => at(x, y).dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true,
        clientX: x, clientY: y, view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      fire('mousedown', x, y); fire('mouseup', x, y);
      return 'clicked';
    })()""")
    await poll(page, """!!document.querySelector('.insp [data-act="review"]')""", lambda v: v, timeout=12)
    await page.ev("""(() => { const b = document.querySelector('.insp [data-act="review"][data-grade="记得"]');
      if (!b) return 'missing'; b.click(); return 'ok'; })()""")
    banner = await poll(page, "document.querySelector('.toast .toast-text')?.textContent || ''",
                        lambda v: "记为" in (v or ""), timeout=12)
    ck.add("记一次复习后到期列表少一个", "记得" in (banner or ""), (banner or "").strip()[:60])
    gone = await poll(page, """document.querySelector('[data-cell-id="戊"] circle')?.getAttribute('fill') || ''""",
                      lambda v: v == "transparent")
    ck.add("复习完圆点熄灭", gone == "transparent", f"circle fill = {gone}")


async def case_modes(page: Page, ck: Check) -> None:
    """四个模式：对话 / 项目图 / 全局图 / 历史。**默认落在对话**——
    启动成本最低的入口应该是默认入口。跑完停在全局图，后面的画布用例才有工具条可点。
    """
    on = await page.ev("""(() => {
      const b = document.querySelector('.topbar .seg button.on');
      return b ? b.textContent.trim() : '';
    })()""")
    ck.add("默认落在「对话」", on == "对话", f"当前是「{on}」")

    chat = await poll(page, """(() => {
      const v = document.querySelector('.chat-view');
      return v ? v.getBoundingClientRect().width : 0;
    })()""", lambda v: v and v > 300, timeout=8)
    ck.add("对话是全屏主体而不是抽屉", (chat or 0) > 300, f"{round(chat or 0)}px")
    ck.add("对话模式下画布还在（只是让出宽度，不销毁）",
           await page.ev("!!document.querySelector('.canvas')"), "canvas 仍在 DOM 里")

    # 收起右侧的图 → 对话占满
    # 按 title 找，别按位置——会话条上的按钮会增减（这里就被新加的「收起对话」顶过一次）
    fold = """(() => {
      const b = [...document.querySelectorAll('.chat-bar .icon-btn')]
        .find(x => (x.title || '').includes('侧的图'));
      b?.click(); return b ? 'ok' : 'missing';
    })()"""
    assert await page.ev(fold) == "ok", "会话条上找不到收起图的按钮"
    solo = await poll(page, """document.querySelector('.stage')?.classList.contains('solo')""",
                      lambda v: v, timeout=6)
    ck.add("图 pane 能收起", bool(solo), f"solo={solo}")
    await page.ev(fold)

    ck.add("对话有关掉的入口（不用绕去顶栏切视图）",
           await page.ev("""[...document.querySelectorAll('.chat-bar .icon-btn')]
             .some(b => (b.title || '').includes('收起对话'))"""), "会话条上有 ✕")

    await switch_mode(page, "历史")
    ck.add("切得到历史视图",
           await page.ev("""document.querySelector('.topbar .seg button.on')?.textContent.trim() === '历史'"""),
           "历史 tab 生效")

    await switch_mode(page, "全局图")
    left = await page.ev("!document.querySelector('.chat-view')")
    ck.add("离开对话后全屏对话收起", bool(left), f"chat-view 还在={not left}")
    await wait_render(page, 4)


async def case_project_view(page: Page, ck: Check, api: str) -> None:
    """项目画布（四期）：**自己一份 layout**，还没建的点画成幽灵占位。

    - 在项目画布上拖节点，全局 layout 的 revision 不变（两份文件各走各的）
    - 「同步到全局」只放已建成、还没上图的点，落 draft，**坐标不搬**
    - 顶栏「↗ 全局图」把项目的点在整张图上高亮——两个视角之间唯一需要的桥
    """
    send(f"{api}/api/projects", {"base_revision": 0, "projects": {"demo": {
        "name": "演示项目", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "甲"}, {"id": "乙"}, {"id": "还没建的"}]}]}]}}},
         method="PUT")
    # 2026-09-18 起项目画布**不画父框**：清单结构只决定初始位置，不该变成一个把所有点圈起来的框
    fresh = get(f"{api}/api/layout?layout=demo")["layout"]
    ck.add("项目画布初始没有任何分组框", fresh["groups"] == {}, str(fresh["groups"]))
    ck.add("初始点都不归任何框", all(n["group"] is None for n in fresh["nodes"].values()), str(fresh["nodes"]))
    # 给它手工套一个框，下面用右键菜单「解散」拆掉——老项目画布带着 g-list-0 的迁移就靠这一下
    # 同时把幽灵从画布上拿掉：下面用「放到右下角」把它补回来（清单后来加的点就是这个形状）
    send(f"{api}/api/layout?layout=demo", {"base_revision": fresh["revision"],
         "groups": {"g-old": {"name": "旧父框", "x": 40, "y": 40, "w": 900, "h": 500}},
         "nodes": {"甲": {"group": "g-old"}, "乙": {"group": "g-old"}, "还没建的": None}}, method="PATCH")
    before_x = get(f"{api}/api/layout?layout=demo")["layout"]["nodes"]["甲"]["x"]
    await page.ev("location.reload()")
    await asyncio.sleep(2.2)
    await page.ev("""(() => { document.querySelector('.brief .icon-btn')?.click(); })()""")

    picked = await poll(page, """document.querySelector('.topbar .proj-switch')?.value || ''""",
                        lambda v: v == "demo", timeout=10)
    ck.add("顶栏有项目切换器且选中了项目", picked == "demo", f"值={picked}")

    opts = await page.ev("""JSON.stringify([...document.querySelectorAll('.proj-switch option')]
      .map(o => o.value))""")
    ck.add("切换器里有「全局」那一档（不绑项目的对话落 _scratch）",
           "" in json.loads(opts or "[]"), str(opts))
    scopes = await page.ev("""JSON.stringify([...document.querySelectorAll('.topbar .seg')]
      .map(g => [...g.querySelectorAll('button')].map(b => b.textContent.trim())))""")
    ck.add("顶栏按作用域分成两组（项目级 / 全局级）",
           json.loads(scopes or "[]") == [["对话", "项目图"], ["全局图", "历史", "谱系"]], str(scopes))

    ck.add("顶栏只有一个「全局图」（tab 自己就是那座桥，不另设按钮）",
           await page.ev("""[...document.querySelectorAll('.topbar button')]
             .filter(b => b.textContent.trim() === '全局图').length""") == 1,
           "两个都叫「全局图」只会让人问为什么有两个")

    glob_rev = get(f"{api}/api/layout")["layout"]["revision"]
    await switch_mode(page, "项目图")
    # 画布上分两拨：项目自己的点，和「周边一跳」借来的外部邻居（靛蓝虚线 ↗，默认开）。
    # 断言要分开写——只数总数的话，借来的点一进来这条就失守，而它恰恰是要盯住的那条线：
    # **项目自己的点必须一个不多**，多出来的每一个都得带着 borrowed 标记。
    got = await poll(page, """JSON.stringify((() => {
      const ns = __kg.graph.getNodes().filter((n) => n.shape === 'kg-node')
      return { own: ns.filter((n) => !(n.getData() || {}).borrowed).map((n) => n.id),
               borrowed: ns.filter((n) => (n.getData() || {}).borrowed).map((n) => n.id) }
    })())""", lambda v: v and '"own":[]' not in v, timeout=10)
    seen = json.loads(got or "{}")
    ck.add("项目画布只画这个项目里的点", set(seen.get("own") or []) == {"甲", "乙"}, str(seen))
    ck.add("多出来的点全是标记过的「借来的」，且都不在这个项目的清单里",
           all(i not in {"甲", "乙"} for i in seen.get("borrowed") or []), str(seen.get("borrowed")))

    # 清单里有、画布上没有的点：同步条上报数，一键停到右下角
    park = await poll(page, """(() => {
      const b = [...document.querySelectorAll('.sync-bar .btn')].find((x) => x.textContent.includes('右下角'));
      return b ? b.textContent.trim() : '';
    })()""", lambda v: bool(v), timeout=8)
    ck.add("同步条提示有点还没上画布", "1 个点" in (park or ""), str(park))
    await page.ev("""[...document.querySelectorAll('.sync-bar .btn')]
      .find((x) => x.textContent.includes('右下角'))?.click()""")
    parked = await poll_api(lambda: get(f"{api}/api/layout?layout=demo")["layout"]["nodes"],
                            lambda n: "还没建的" in n, timeout=8)
    g = (parked or {}).get("还没建的", {})
    others = [n for k, n in (parked or {}).items() if k != "还没建的"]
    ck.add("「放到右下角」把幽灵补回 layout 并停在已有内容的右下方",
           g.get("state") == "ghost" and others and g.get("x", 0) > max(n["x"] + n["w"] for n in others)
           and g.get("y", -1) >= max(n["y"] for n in others), f"幽灵 = {g}")

    ghost = await poll(page, """(() => {
      const el = document.querySelector('[data-cell-id="还没建的"] rect');
      return el ? `${el.getAttribute('stroke-dasharray')}|${el.getAttribute('fill')}` : '';
    })()""", lambda v: bool(v), timeout=6)
    ck.add("还没建的点画成幽灵（更淡的虚线、透明底）", "2 5" in (ghost or ""), str(ghost))

    # 右键框 → 解散：框没了，点一个不动
    has_frame = await poll(page, """!!document.querySelector('[data-shape="kg-group"][data-cell-id="g-old"]')""",
                           lambda v: bool(v), timeout=6)
    ck.add("手工套的旧父框画出来了", bool(has_frame), "" if has_frame else "画布上没有 g-old")
    if has_frame:
        await right_click(page, "g-old", grab="title")
        await menu_pick(page, "解散这个框")
        gone = await poll_api(lambda: get(f"{api}/api/layout?layout=demo")["layout"],
                              lambda l: "g-old" not in l["groups"], timeout=8)
        ck.add("「解散这个框」把框从 layout 里删掉", bool(gone) and "g-old" not in gone["groups"],
               str((gone or {}).get("groups")))
        jia = (gone or {}).get("nodes", {}).get("甲", {})
        ck.add("解散后点留在原位、不再归框", jia.get("x") == before_x and jia.get("group") is None,
               f"甲 = {jia}")
        still = await page.ev("""__kg.graph.getNodes()
          .filter((n) => n.shape === 'kg-node' && !(n.getData() || {}).borrowed).length""")
        ck.add("解散后画布上的点一个没少", still == 3, f"{still} 个（不含借来的外部邻居）")

    # 项目画布有自己的一份 layout：拖它不碰全局图
    proj = get(f"{api}/api/layout?layout=demo")["layout"]
    send(f"{api}/api/layout?layout=demo", {"base_revision": proj["revision"],
                                           "nodes": {"甲": {"x": 4321, "y": 1234}}}, method="PATCH")
    ck.add("动项目画布不碰全局图的 revision",
           get(f"{api}/api/layout")["layout"]["revision"] == glob_rev,
           f"全局 revision 仍是 {glob_rev}")

    clicked = await page.ev("""(() => {
      const bar = document.querySelector('.sync-bar');
      if (!bar) return 'no-bar';
      const b = [...bar.querySelectorAll('.btn')].find((x) => x.textContent.includes('同步'));
      if (!b) return 'no-btn:' + bar.textContent.slice(0, 40);
      b.click(); return 'ok';
    })()""")
    ck.add("项目画布上有「同步到全局」", clicked == "ok", str(clicked))
    # 这个项目的点本来就都在全局图上，所以正常结果是"N 个本来就在图上"，不是"放上去了 N 个"
    msg = await poll(page, """(() => {
      const b = document.querySelector('.banner') || document.querySelector('.toast-text');
      return b ? b.textContent.trim() : '';
    })()""", lambda v: v and any(k in v for k in ("图上", "还没建", "草稿", "没有需要", "失败")), timeout=12)
    ck.add("「同步到全局」给出逐条结果", bool(msg), (msg or "")[:80])
    ck.add("同步不搬坐标（全局图里的甲不是项目画布那个位置）",
           get(f"{api}/api/layout")["layout"]["nodes"].get("甲", {}).get("x") != 4321,
           "全局图有自己的结构")

    # 全局图那座桥就是 tab 本身：选着项目时切过去会顺手高亮那些点（不另设按钮）
    await switch_mode(page, "全局图")
    msg = await poll(page, """(() => {
      const t = [...document.querySelectorAll('.toast-text')].map(x => x.textContent.trim());
      return JSON.stringify(t);
    })()""", lambda v: v and "高亮" in v, timeout=8)
    dim = await page.ev("""document.querySelectorAll('.x6-graph [data-shape="kg-node"]').length""")
    ck.add("切到全局图会高亮当前项目的点", bool(msg) and "高亮" in msg, (msg or "")[:90] + f" | {dim} 个节点")
    await wait_render(page, 4)


async def case_calendar(page: Page, ck: Check) -> None:
    """学习日历：热力图画得出来、点某天能看明细。**全派生，点一圈不该写任何文件。**"""
    await use_scope(page, "")
    await open_rail(page, "日历")
    text = await poll(page, """(() => {
      const a = document.querySelector('aside.study');
      return a ? a.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "连续" in v, timeout=12)
    ck.add("日历面板开得出来", "连续" in (text or ""), (text or "")[:60])

    cells = await page.ev("document.querySelectorAll('.heat .heat-cell').length")
    ck.add("热力图铺出格子", (cells or 0) > 30, f"{cells} 格")

    lit = await page.ev("""document.querySelectorAll('.heat .heat-cell:not(.lv-0):not(.void)').length""")
    ck.add("有动静的日子被点亮", (lit or 0) >= 1, f"{lit} 天")

    before = ck.layout()["revision"]
    await page.ev("""(() => {
      const c = document.querySelector('.heat .heat-cell:not(.lv-0):not(.void)');
      c?.click(); return 'ok';
    })()""")
    day = await poll(page, """(() => {
      const d = document.querySelector('.cal-day');
      return d ? d.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and len(v) > 8, timeout=8)
    ck.add("点某天看得到当天明细", bool(day), (day or "")[:60])
    ck.add("看日历不写任何文件", ck.layout()["revision"] == before, f"revision 仍是 {before}")
    # 收拾干净：面板开着会盖住画布，后面右键菜单的落点就点不着了
    await open_rail(page, "日历")


async def case_chat_view(page: Page, ck: Check, api: str) -> None:
    """对话视图：开场白点得动、输入框收得住字、会话条在。**不发消息**——那会真打 LLM。

    这一条守的是"视图渲染不炸"：Vue 里一个模板错就整屏空白，只有真浏览器能发现。
    """
    await switch_mode(page, "对话")
    text = await poll(page, """(() => {
      const a = document.querySelector('.chat-view .chat-wrap');
      return a ? a.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "聊着学" in v, timeout=12)
    ck.add("对话视图渲染出来了", "聊着学" in (text or ""), (text or "")[:60])

    starters = await page.ev("""document.querySelectorAll('.chat-view .starters .btn').length""")
    ck.add("开场白按钮摆出来了", (starters or 0) >= 3, f"{starters} 个")

    stances = await page.ev("""JSON.stringify([...document.querySelectorAll('.chat-bar select')]
      .map(s => [...s.options].map(o => o.value)))""")
    ck.add("会话条上能切口径（教练 / 面试 / 聊天）",
           "面试" in (stances or ""), str(stances)[:80])

    ck.add("会话条上有「梳理这段」（聊完一键整理进图谱）",
           await page.ev("""[...document.querySelectorAll('.chat-bar .btn')]
             .some(b => b.textContent.includes('梳理'))"""), "对话里聊完能一键整理")

    ck.add("会话条上有「新的一段」",
           await page.ev("""[...document.querySelectorAll('.chat-bar .btn')]
             .some((b) => b.textContent.includes('新的一段'))"""), "多段对话切得动")

    # 点「讲个概念」只填输入框、不发出去（它以「：」结尾）
    await page.ev("""(() => {
      const b = [...document.querySelectorAll('.chat-view .starters .btn')]
        .find((x) => x.textContent.includes('讲个概念'));
      if (!b) return 'missing';
      b.click(); return 'ok';
    })()""")
    # Vue 渲染是异步的：点完立刻读 value 会读到空，要等它刷一帧
    filled = await poll(page, """document.querySelector('.chat-view .chat-input textarea')?.value || ''""",
                        lambda v: v and "我想搞懂" in v, timeout=8)
    ck.add("要补话的开场白只填进输入框", "我想搞懂" in (filled or ""), str(filled)[:40])

    sendable = await page.ev("""!document.querySelector('.chat-view .chat-input .icon-btn.primary')?.disabled""")
    ck.add("有字之后发送键才亮", bool(sendable), f"disabled={not sendable}")

    # 对话和画布是**左右分栏**，不是画布盖在上面——重叠的话看起来就像"点了对话没反应"
    box = await page.ev("""JSON.stringify((() => {
      const c = document.querySelector('.chat-view').getBoundingClientRect();
      const g = document.querySelector('.canvas').getBoundingClientRect();
      const top = document.elementFromPoint(c.x + c.width / 2, c.y + c.height / 2);
      return { split: Math.round(g.x) >= Math.round(c.x + c.width) - 2,
               onTop: !!top?.closest('.chat-view') };
    })())""")
    info = json.loads(box)
    ck.add("对话与画布左右分栏、不重叠", info["split"] and info["onTop"], box)

    await use_scope(page, "demo")      # 上一个用例把作用域切到「全局」了，这里要的是项目下的样子
    rail_proj = await page.ev("""JSON.stringify([...document.querySelectorAll('.rail .rail-btn')]
      .map(b => (b.dataset.tip || '').split(' · ')[1]).filter(Boolean))""")
    ck.add("项目下只给项目工具（不塞全局的欠账 / 日历 / Inbox）",
           "清单" in (rail_proj or "") and "欠账" not in (rail_proj or ""), str(rail_proj))

    # 切项目要跟着切到那个项目的对话线
    await page.ev("""(() => {
      const s = document.querySelector('.proj-switch');
      s.value = ''; s.dispatchEvent(new Event('change', { bubbles: true })); return 'ok';
    })()""")
    await asyncio.sleep(0.8)
    empty = await page.ev("""document.querySelector('.chat-view .chat-wrap')?.textContent.includes('聊着学')""")
    ck.add("切到「全局」换成那条线的对话", bool(empty), "全局线是空的（还没聊过）")
    # 对话右边那块图用的是**项目画布**；切项目时它必须跟着换，
    # 不然你在 B 项目里聊天、背后摆着 A 项目的图
    glob_nodes = await page.ev("""document.querySelectorAll('[data-shape="kg-node"]').length""")
    await use_scope(page, "demo")
    # 对话模式下画布只剩右边一条，auto-LOD 会把分组折叠成簇卡片 → 数不到 kg-node，
    # 所以直接看当前加载的是哪一份 layout（__kg 那几个 getter 就是为这个留的）
    loaded = await poll(page, "JSON.stringify(Object.keys(__kg.layout.nodes).sort())",
                        lambda v: v and "还没建的" in v, timeout=10)
    ck.add("对话模式下切项目，右边那块图跟着换（加载的是项目画布）",
           set(json.loads(loaded or "[]")) == {"甲", "乙", "还没建的"},
           f"全局时 {glob_nodes} 个节点 → 现在 {loaded}")

    rail_proj2 = await page.ev("""JSON.stringify([...document.querySelectorAll('.rail .rail-btn')]
      .map(b => (b.dataset.tip || '').split(' · ')[1]).filter(Boolean))""")
    ck.add("项目下左侧栏是项目那套", "清单" in (rail_proj2 or ""), str(rail_proj2))

    # 项目下的清单面板只看这一个项目：不给项目切换器、不给新建（那些去「🌐 全局」做）
    await open_rail(page, "清单")
    panel = await poll(page, """(() => {
      const a = document.querySelector('aside.study');
      if (!a) return '';
      return JSON.stringify({ title: a.querySelector('.drawer-head')?.textContent.trim().slice(0, 4),
                              switcher: a.querySelectorAll('.plan-switch').length,
                              txt: a.textContent.slice(0, 40) });
    })()""", lambda v: v and "title" in v, timeout=10)
    info2 = json.loads(panel or "{}")
    ck.add("项目下的清单面板不列别的项目", info2.get("switcher") == 0, str(info2))
    ck.add("项目下这一屏叫「清单」不叫「项目」", "清单" in (info2.get("title") or ""), str(info2))

    # 「建」按钮必须在可视区内：why 一长就把它挤出去的话，等于这个按钮不存在
    fit = await page.ev("""JSON.stringify((() => {
      const li = [...document.querySelectorAll('aside.study .edge-row.point')]
        .find(x => x.querySelector('.row-acts .btn'));
      if (!li) return { found: false };
      const p = li.getBoundingClientRect(), b = li.querySelector('.row-acts .btn').getBoundingClientRect();
      return { found: true, inside: b.right <= p.right + 1 && b.left >= p.left,
               whyOwnLine: !!li.querySelector('.why-line') };
    })())""")
    box2 = json.loads(fit or "{}")
    ck.add("「建」按钮没被 why 挤出行外", box2.get("found") and box2.get("inside"), str(box2))

    # 阶段必须是竖着排的：类名撞上画布的 .stage（flex + overflow:hidden）时，
    # 阶段头会和点列表并排挤扁、超出部分被裁掉
    stack = json.loads(await page.ev("""JSON.stringify((() => {
      const st = document.querySelector('aside.study .section.plan-stage');
      const h = st.querySelector('.section-head').getBoundingClientRect();
      const u = st.querySelector('ul').getBoundingClientRect();
      return { headH: Math.round(h.height), listTop: Math.round(u.top - h.bottom) };
    })())""") or "{}")
    ck.add("阶段头和点列表上下排，不是并排挤扁",
           stack.get("headH", 999) < 60 and stack.get("listTop", -1) >= 0, str(stack))

    # 加点的输入框默认收起：常驻一行 × N 个阶段纯属白占地方
    add0 = await page.ev("document.querySelectorAll('aside.study .add-point').length")
    ck.add("加点输入框默认收起", add0 == 0, f"可见 {add0} 行")
    opened = await page.ev("""JSON.stringify((() => {
      const head = document.querySelector('aside.study .section.plan-stage .section-head');
      const btn = [...head.querySelectorAll('.icon-btn')].find(x => (x.title || '').includes('加知识点'));
      if (!btn) return { err: 'no-toggle' };
      btn.click();
      return { ok: true };
    })())""")
    ck.add("阶段头上有「加知识点」开关", "ok" in (opened or ""), str(opened))
    box3 = json.loads(await poll(page, """JSON.stringify((() => {
      const st = document.querySelector('aside.study .section.plan-stage');
      const row = st.querySelector('.add-point'), head = st.querySelector('.section-head');
      const ul = st.querySelector('ul');
      if (!row) return { open: false };
      return { open: true, focused: document.activeElement === row.querySelector('input'),
               underHead: row.getBoundingClientRect().top >= head.getBoundingClientRect().bottom - 1,
               aboveList: !ul || row.getBoundingClientRect().bottom <= ul.getBoundingClientRect().top + 1 };
    })())""", lambda v: v and '"open":true' in v, timeout=6) or "{}")
    ck.add("展开后就贴在阶段名下方、在点列表之前",
           box3.get("underHead") and box3.get("aboveList"), str(box3))
    ck.add("展开后光标直接落在输入框里", box3.get("focused"), str(box3))

    # 清单能拉宽（和对话那条一样的机制），长文本靠省略号 + title 兜底
    wide = await page.ev("""(() => {
      const a = document.querySelector('aside.study');
      const before = a.getBoundingClientRect().width;
      const b = [...a.querySelectorAll('.drawer-head .icon-btn')]
        .find(x => (x.title || '').includes('展宽'));
      if (!b) return JSON.stringify({ err: 'no-expand' });
      b.click();
      // 上限 = min(面板自己的 max, 视口 - 320)，两者取小
      return JSON.stringify({ before, cap: Math.min(1100, window.innerWidth - 320) });
    })()""")
    w = json.loads(wide)
    after2 = await poll(page, "document.querySelector('aside.study')?.getBoundingClientRect().width || 0",
                        lambda v: v and abs(v - (w.get("cap") or 0)) < 3, timeout=8)
    ck.add("清单面板能一键展宽", abs((after2 or 0) - w.get("cap", 0)) < 3,
           f"{round(w.get('before', 0))} → {round(after2 or 0)}px")
    ck.add("放不下的点名有 title 兜底",
           await page.ev("""[...document.querySelectorAll('aside.study .edge-row.point .to')]
             .every(el => el.title !== undefined && el.title !== '')"""), "悬停能看全")
    await open_rail(page, "清单")

    # 刷新后还在原来的项目上：挂载时第一次 fetchLayout 就得知道拉哪一份，
    # 否则画布会先画成全局图（"刷新跳回全局"那个 bug）
    await page.ev("location.reload()")
    await asyncio.sleep(2.2)
    await page.ev("""(() => { document.querySelector('.brief .icon-btn')?.click(); })()""")
    after = await poll(page, "JSON.stringify({p: __kg.project, "
                             "n: Object.keys(__kg.layout.nodes).sort()})",
                       lambda v: v and '"p":"demo"' in v, timeout=12)
    ck.add("刷新后还在原项目，画布也还是项目画布",
           json.loads(after or "{}").get("n") == ["乙", "甲", "还没建的"], str(after))

    # 项目画布必须**可写**：只认 structure 的话，这里拖节点、右键、连边全部静默失效
    await switch_mode(page, "项目图")
    await asyncio.sleep(0.8)
    rev0 = get(f"{api}/api/layout?layout=demo")["layout"]["revision"]
    await drag(page, "甲", dx=70, dy=40)
    moved = await poll(page, "'x'", lambda _: get(f"{api}/api/layout?layout=demo")["layout"]["revision"] > rev0,
                       timeout=10)
    ck.add("项目画布上拖节点会落盘", bool(moved),
           f"revision {rev0} → {get(f'{api}/api/layout?layout=demo')['layout']['revision']}")

    # 幽灵占位的右键菜单不一样：能建、能拿掉，但不给「建立关系」（它还没有 md）
    await right_click(page, "还没建的")
    ghost_menu = await poll(page, """JSON.stringify([...document.querySelectorAll('.ctx-menu .pop-item')]
      .map(b => b.textContent.trim()))""", lambda v: v and v != "[]", timeout=8)
    items = json.loads(ghost_menu or "[]")
    ck.add("幽灵占位能直接建出来", any("建出来" in x for x in items), str(items))
    ck.add("幽灵占位不给「建立关系」（没有 md，关系行没处写）",
           not any("建立关系" in x for x in items), str(items))
    await page.ev("document.body.click()")

    # 切回「🌐 全局」：左侧栏该换成全局那套，「项目图」该变灰
    await use_scope(page, "")
    rail_global = await page.ev("""JSON.stringify([...document.querySelectorAll('.rail .rail-btn')]
      .map(b => (b.dataset.tip || '').split(' · ')[1]).filter(Boolean))""")
    ck.add("「全局」下只给全局工具（项目管理 / Inbox / 欠账 / 日历）",
           "项目" in (rail_global or "") and "清单" not in (rail_global or ""), str(rail_global))
    ck.add("选了「全局」时「项目图」是灰的（没有项目就没有项目画布）",
           await page.ev("""[...document.querySelectorAll('.topbar .seg button')]
             .find(b => b.textContent.trim() === '项目图')?.disabled === true"""), "disabled")

    await switch_mode(page, "全局图")      # 收拾干净，后面的用例要画布


async def case_drag_from_inbox(page: Page, ck: Check, vault: Path) -> None:
    """从 Inbox 拖到画布：落在哪个分组框里就归哪个组，坐标就是松手的位置。"""
    (vault / "nodes/组B/己.md").write_text(NEW_MD.format(n="己", to="丙"), "utf-8")
    await menu_click(page, "重新加载")
    await poll(page, "document.querySelectorAll('aside.inbox .inbox-item').length", lambda v: (v or 0) >= 1)

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
    await poll(page, "document.querySelector('.insp .node-title')?.textContent || ''", lambda v: "己" in (v or ""))
    await click_text(page, ".insp .act-row button", "定稿")
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
    await open_rail(page, "素材")
    thumbs = await poll(page, "document.querySelectorAll('aside.picker .thumbs li').length",
                        lambda v: (v or 0) > 0, timeout=12)
    ck.add("贴图面板列出 assets/ 里的图", (thumbs or 0) == 1, f"{thumbs} 张")
    await page.ev("document.querySelector('aside.picker .thumbs li').click()")
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


# ---------------------------------------------------------------- 线不许从卡片身上穿过去

CROSS_PROBE = """(() => {
  const g = __kg.graph
  const boxes = g.getNodes().filter((n) => (n.getData() || {}).kind === 'node')
                 .map((n) => ({ id: n.id, ...n.getBBox() }))
  const hits = []
  for (const e of g.getEdges()) {
    const view = g.findViewByCell(e)
    const path = view && view.container.querySelector('path')
    if (!path) continue
    const len = path.getTotalLength()
    if (!len) continue
    const ends = new Set([e.getSourceCellId(), e.getTargetCellId()])
    const crossed = new Set()
    for (let i = 0; i <= 60; i++) {
      const p = path.getPointAtLength((len * i) / 60)
      for (const b of boxes) {
        if (ends.has(b.id)) continue
        if (p.x > b.x + 3 && p.x < b.x + b.width - 3 && p.y > b.y + 3 && p.y < b.y + b.height - 3) crossed.add(b.id)
      }
    }
    if (crossed.size) hits.push(e.id + ' 穿过 ' + [...crossed].join('/'))
  }
  return JSON.stringify({ edges: g.getEdges().length, hits })
})()"""


async def case_edges_dodge(page: Page, ck: Check) -> None:
    """连线不许从别的卡片身上穿过去——这是"卡片挡住连线"那个抱怨的可量化版本。

    量的是**渲染出来的 path**，不是配置：manhattan 搜不出路时会悄悄退回直线，
    只断言"路由挂上了"根本发现不了（真实图上 17 条边有 10 条是这么穿过去的）。
    """
    await switch_mode(page, "全局图")
    await asyncio.sleep(0.8)
    got = json.loads(await page.ev(CROSS_PROBE) or "{}")
    ck.add("连线绕开卡片，一条都不穿模",
           got.get("edges", 0) > 0 and not got.get("hits"), str(got)[:200])


# ---------------------------------------------------------------- 外部改过文件之后还能继续写

async def case_stale_index(page: Page, ck: Check, vault: Path, api: str) -> None:
    """有人在 Obsidian 里改一个字，索引就重建、revision 就变——
    页面上后面**每一次**写回都会 409，直到刷新。真实使用里就是这么炸的。"""
    await switch_mode(page, "全局图")
    # 背着页面改一个文件：等价于在 Obsidian 里手改
    p = vault / "nodes/组B/丙.md"
    p.write_text(p.read_text("utf-8") + "\n外部加的一行\n", "utf-8")
    stale = await page.ev("__kg.indexRevision ?? null")
    await asyncio.sleep(0.5)
    out = await page.ev("""(async () => {
      try {
        const r = await window.__kg.write([{ type: 'update_frontmatter', source: '甲',
                                             fields: { desc: '外部改过之后再写一次' } }])
        return JSON.stringify({ ok: true, files: r.files.length })
      } catch (e) { return JSON.stringify({ ok: false, msg: String(e.body?.detail?.message || e.message) }) }
    })()""", await_promise=True)
    res = json.loads(out or "{}")
    ck.add("外部改过文件之后，写回自己刷新索引再试一次", res.get("ok"), f"{res} · 原 revision {stale}")
    ck.add("写回落到了磁盘上", "外部改过之后再写一次" in (vault / "nodes/组A/甲.md").read_text("utf-8"),
           "甲.md")


# ---------------------------------------------------------------- 表单外观

async def case_form_look(page: Page, ck: Check) -> None:
    """标签独占一行、输入框占满宽、下拉有箭头——「一眼看不出该填什么」的那三件事。

    只钉死能量出来的几何与属性，不钉颜色：颜色随主题走，量它只会让测试天天红。
    """
    # 表单长在项目的清单里：左侧栏那套工具是**跟着视图**走的，所以还得切进项目视图
    await use_scope(page, "demo")
    await switch_mode(page, "项目图")
    rail = await poll(page, """JSON.stringify({ proj: __kg.project,
      tips: [...document.querySelectorAll('.rail .rail-btn')].map((b) => b.dataset.tip) })""",
      lambda v: v and "清单" in v, timeout=8)
    assert rail and "清单" in rail, f"切进项目后左侧栏没换成项目那套：{rail}"
    await open_rail(page, "清单")
    look = json.loads(await poll(page, """JSON.stringify((() => {
      const f = document.querySelector('aside.study .fld');
      if (!f) return null;
      const lb = f.querySelector('.lb'), inp = f.querySelector('input, select, textarea');
      const lr = lb.getBoundingClientRect(), ir = inp.getBoundingClientRect();
      const sel = document.querySelector('aside.study .fld select');
      const cs = sel && getComputedStyle(sel);
      const key = document.querySelector('aside.study .fld.key');
      if (!lb || !inp) return null;
      return { labelAbove: lr.bottom <= ir.top + 1, wide: ir.width > lr.width * 0.9,
               fullWidth: Math.abs(ir.width - f.getBoundingClientRect().width) < 3,
               chevron: !!cs && cs.backgroundImage.includes('svg'),
               padRight: cs ? parseFloat(cs.paddingRight) : 0,
               hasKey: !!key, hints: f.closest('aside').querySelectorAll('.hint').length };
    })())""", lambda v: v and v != "null", timeout=10) or "{}") or {}
    ck.add("标签独占一行、在输入框上方", look.get("labelAbove"), str(look))
    ck.add("输入框占满整行", look.get("fullWidth"), str(look))
    ck.add("下拉框自己画了箭头（原生那个两套主题都不受控）",
           look.get("chevron") and look.get("padRight", 0) >= 20, str(look))
    ck.add("重点字段有标记", look.get("hasKey"), str(look))
    ck.add("字段下面有说明文字", (look.get("hints") or 0) >= 4, str(look))
    await open_rail(page, "清单")            # 关掉，别影响后面的用例
    await switch_mode(page, "全局图")
    await use_scope(page, "")


# ---------------------------------------------------------------- 对话里的图文渲染

async def case_chat_markdown(page: Page, ck: Check, vault: Path) -> None:
    """模型的回答要渲染成图文：Markdown（表格/列表/代码）+ ```mermaid 画出来的图。
    不调模型——用 __kg.fakeReply 塞一条假回复，渲染这条链路和模型无关。"""
    await switch_mode(page, "对话")
    await asyncio.sleep(0.6)
    await page.ev("""__kg.fakeReply(`核心差别：

| | NPU | GPU |
|---|---|---|
| 定位 | 专用 | 通用 |

- 一条
- 两条

\\`\\`\\`mermaid
graph LR
  A[CPU] --> B[GPU]
  B --> C[NPU]
\\`\\`\\`

<img src=x onerror="window.__xss = 1">
`)""")
    shape = json.loads(await poll(page, """JSON.stringify((() => {
      const b = [...document.querySelectorAll('.msg.assistant .bubble')].pop();
      if (!b) return { found: false };
      return { found: true, table: !!b.querySelector('table'), li: b.querySelectorAll('li').length,
               svg: !!b.querySelector('.mmd svg'), raw: !!b.querySelector('.mmd-raw'),
               xss: !!window.__xss, img: b.querySelectorAll('img').length,
               why: b.querySelector('.mmd-raw')?.title || '',
               text: (b.textContent || '').slice(0, 40) };
    })())""", lambda v: v and '"svg":true' in v, timeout=25) or "{}")
    ck.add("Markdown 表格渲染出来了", shape.get("table"), str(shape))
    ck.add("Markdown 列表渲染出来了", shape.get("li") == 2, str(shape))
    ck.add("mermaid 画成了 svg", shape.get("svg"), str(shape))
    ck.add("模型写的 HTML 不会变成真标签（先转义再解析）",
           not shape.get("xss") and shape.get("img") == 0, str(shape))

    # 流式时每来一个增量整条消息的 text 都会重新赋值。按段序号缓存的话，已经画好的图
    # 会被清掉重画——屏幕上闪一下，而且每张图 15~140 ms 全是白烧的。claude-cli 是块级增量
    # 所以只闪几下，换成逐 token 的 provider 就是几百次重画，直接卡死。
    ids_expr = """JSON.stringify([...document.querySelectorAll('.msg.assistant .mmd svg')]
      .map((s) => s.id))"""
    # 每张画好的 svg 带一个自增 id，重画就会换一个新的。**先确认真有图**——
    # 一张都没有时 [] == [] 会让这条断言自己骗自己（第一版就是这么假通过的）。
    before = json.loads(await page.ev(ids_expr) or "[]")
    for _ in range(10):
        await page.ev("__kg.growReply('，再补一句解释')")
        await asyncio.sleep(0.08)
    await asyncio.sleep(0.8)
    grew = await page.ev("""(() => { const b = [...document.querySelectorAll('.msg.assistant .bubble')].pop();
      return (b.textContent.match(/再补一句解释/g) || []).length })()""")
    after = json.loads(await page.ev(ids_expr) or "[]")
    ck.add("正文一路变长时，已经画好的图一次都不重画（流式不闪、不白烧）",
           bool(before) and grew == 10 and after == before,
           f"追加 {grew} 次，id {before} → {after}")

    # mermaid 把配色烤进 svg 里：引擎只 initialize 一次的话，切到深色后图还是浅色那张惨白的
    # 重画的那一瞬间图是空的，取到的会是 'no-rect'。**必须要求拿到一个真的 rgb 值**，
    # 否则"换主题了"和"图正好不见了"这两件事在断言眼里长得一模一样。
    fill_expr = """(() => { const s = [...document.querySelectorAll('.msg.assistant .mmd svg')].pop();
      const n = s && s.querySelector('.node rect, rect');
      return n ? getComputedStyle(n).fill : 'no-rect' })()"""
    is_color = lambda v: bool(v) and str(v).startswith("rgb")
    light = await poll(page, fill_expr, is_color, timeout=10)
    # 直接改属性而不是去点切换按钮：这一条验的是"图跟不跟主题"，不是"按钮点不点得动"。
    # 改完要还原成进来时那个值——后面还有几个用例，别给它们留一个和 app 状态对不上的 DOM。
    was = await page.ev("document.documentElement.dataset.theme || 'light'")
    await page.ev("document.documentElement.dataset.theme = 'dark'")
    dark = await poll(page, fill_expr, lambda v: is_color(v) and v != light, timeout=10)
    ck.add("切深色后图跟着换主题（不是浅色底那张惨白的）",
           is_color(light) and is_color(dark) and dark != light, f"{light} → {dark}")
    await page.ev(f"document.documentElement.dataset.theme = {json.dumps(was)}")
    await poll(page, fill_expr, lambda v: v == light, timeout=10)


# ---------------------------------------------------------------- 阶段 6：历史视图

async def case_chat_card_edit(page: Page, ck: Check, vault: Path) -> None:
    """变更卡写入前能改：desc 是显示层、body 是笔记层，模型给的只是初稿。
    不调模型——用 __kg.fakeReply 塞一张卡，之后的重算（dry_run）和写入走的都是真接口。
    守的是"阈值逻辑单元"那次的教训：正文被压成一句、卡上又改不了，只能写完再去详情面板补。"""
    await switch_mode(page, "对话")
    await asyncio.sleep(0.6)
    target = vault / "nodes/组A/阈值逻辑单元.md"
    await page.ev("""__kg.fakeReply('卡摆出来了，你点写入才落盘。', [{
      changes: [{ type: 'create_node', source: '阈值逻辑单元', path: 'nodes/组A/阈值逻辑单元.md',
                  fields: { name: '阈值逻辑单元', field: '测试', desc: '最简人工神经元' },
                  body: '1943 年提出的最简人工神经元。\\n\\n## 线头\\n- 神经生理学：McCulloch 的出身' }],
      files: [{ path: 'nodes/组A/阈值逻辑单元.md', diff: '（初稿）' }], into: null }])""")
    card = "[...document.querySelectorAll('.change-card')].pop()"
    opened = await poll(page, f"""(() => {{ const c = {card}; if (!c) return '';
      const b = [...c.querySelectorAll('.cc-head .btn')].find((x) => x.textContent.includes('改一改'));
      if (!b) return 'nobtn'; b.click(); return 'ok'; }})()""", lambda v: v in ("ok", "nobtn"), timeout=8)
    ck.add("变更卡上有「改一改」", opened == "ok", str(opened))
    shape = json.loads(await poll(page, f"""JSON.stringify((() => {{ const c = {card};
      return {{ input: !!c?.querySelector('.cc-edit input'), ta: !!c?.querySelector('.cc-edit textarea'),
               body: c?.querySelector('.cc-edit textarea')?.value || '' }}; }})())""",
      lambda v: v and '"ta":true' in v, timeout=6) or "{}")
    ck.add("展开后有摘要框和正文框，正文框里是模型的初稿",
           shape.get("input") and shape.get("ta") and "McCulloch" in shape.get("body", ""), str(shape)[:120])

    # 改 desc、往正文尾部补一行。用原生 setter + input 事件，v-model 才收得到
    await page.ev(f"""(() => {{ const c = {card};
      const set = (el, v) => {{ el.value = v; el.dispatchEvent(new Event('input', {{ bubbles: true }})); }};
      set(c.querySelector('.cc-edit input'), '1943 年神经生理学家 McCulloch 与数学家 Pitts 提出的最简人工神经元');
      const ta = c.querySelector('.cc-edit textarea'); set(ta, ta.value + '\\n- 数学：Pitts 的出身'); }})()""")
    stale = await poll(page, f"""{card}?.querySelector('.cc-edit .cc-acts .dim')?.textContent || ''""",
                       lambda v: "旧的" in v, timeout=4)
    ck.add("改过之后提示卡上的 diff 还是旧的", "旧的" in (stale or ""), str(stale)[:60])

    await page.ev(f"""[...{card}.querySelectorAll('.cc-edit .btn')].find((b) => b.textContent.includes('重算')).click()""")
    diff = await poll(page, f"""{card}?.querySelector('.cc-diff')?.textContent || ''""",
                      lambda v: "Pitts 的出身" in v, timeout=10)
    ck.add("重算后卡上是整篇新文件，摘要和正文都是改过的",
           "数学家 Pitts" in (diff or "") and "## 线头" in (diff or "") and "## 描述" not in (diff or ""), str(diff)[-160:])
    ck.add("重算不落盘", not target.exists(), str(target))

    # diff 上色：新文件是整篇初稿，不该刷成一片绿，只在头上打「新文件」标记
    tint = json.loads(await page.ev(f"""JSON.stringify((() => {{ const c = {card};
      const head = c?.querySelector('.cc-file-head');
      const rows = [...(c?.querySelectorAll('.cc-diff span') || [])];
      return {{ badge: head?.querySelector('.cc-new')?.textContent?.trim() || '',
               path: head?.querySelector('b')?.textContent || '',
               tinted: rows.filter((r) => r.className).length, rows: rows.length }}; }})())"""))
    ck.add("新文件卡片打「新文件」标记、路径挪到 diff 外面",
           "新文件" in tint.get("badge", "") and tint.get("path", "").endswith(".md"), str(tint))
    ck.add("新文件正文一行都不上色（整篇初稿不是改动）",
           tint.get("rows", 0) > 3 and tint.get("tinted") == 0, str(tint))

    await page.ev(f"""[...{card}.querySelectorAll('.cc-acts .btn')].find((b) => b.textContent.trim().endsWith('写入')).click()""")
    # 认「已写入」那枚 chip，别认任意 chip——新文件的卡片头上还有一枚「新文件」标记，
    # 用 querySelector('.chip') 会一开始就命中，这条断言就废了
    written = await poll(page, f"""{card}?.querySelector('.cc-head .chip')?.textContent?.trim() || ''""",
                         lambda v: v == "已写入", timeout=15)
    ck.add("写入后卡片标成「已写入」", written == "已写入", str(written))
    text = target.read_text("utf-8") if target.exists() else ""
    ck.add("落盘的是改过的正文（笔记层整篇保留、desc 也换了）",
           "数学家 Pitts" in text and "- 数学：Pitts 的出身" in text and "## 线头" in text
           and "## 描述" not in text and text.rstrip().endswith("## 关系"), text[:200])

    # 改已有文件：统一 diff 要红绿分明。直接塞一段真 diff——渲染这条链路和后端怎么算的无关。
    # `--- a/…` / `+++ b/…` 也以 -/+ 开头，光看首字符会把文件名染成红绿一整行（旧的 diffLines 就这毛病）
    await page.ev("""__kg.fakeReply('改一处线头。', [{ changes: [], into: null, files: [{
      path: 'nodes/组A/乙.md',
      diff: '--- a/nodes/组A/乙.md\\n+++ b/nodes/组A/乙.md\\n@@ -1,3 +1,3 @@\\n ## 线头\\n-和 RNN 的关系还没写\\n+和 RNN 的关系：见 [[RNN]]' }] }])""")
    tint2 = json.loads(await poll(page, """JSON.stringify((() => {
      const c = [...document.querySelectorAll('.change-card')].pop();
      const rows = [...(c?.querySelectorAll('.cc-diff span') || [])].map((r) => ({
        cls: r.className, t: r.textContent.trim(),
        bg: getComputedStyle(r).backgroundColor, fg: getComputedStyle(r).color }));
      return { rows, badge: !!c?.querySelector('.cc-new'),
               head: c?.querySelector('.cc-file-head b')?.textContent || '' }; })())""",
      lambda v: v and '"rows"' in v and v.count('"cls"') >= 4, timeout=8) or "{}")
    rows = tint2.get("rows", [])
    by = {r["cls"]: r for r in rows}
    ck.add("删除行标红、新增行标绿",
           by.get("del", {}).get("t", "").startswith("-") and by.get("add", {}).get("t", "").startswith("+"),
           str([(r["cls"], r["t"][:14]) for r in rows]))
    ck.add("红绿是两种颜色，且各自有整行底色",
           by.get("del", {}).get("fg") != by.get("add", {}).get("fg")
           and "rgba(0, 0, 0, 0)" not in (by.get("del", {}).get("bg", ""), by.get("add", {}).get("bg", "")),
           str([(r["cls"], r["fg"], r["bg"]) for r in rows if r["cls"] in ("add", "del")]))
    ck.add("文件头 --- / +++ 不进 diff，也就不会被染色",
           not any(r["t"].startswith(("--- ", "+++ ")) for r in rows), str([r["t"][:18] for r in rows]))
    ck.add("上下文行不上色、@@ 是灰的，改已有文件不打「新文件」标记",
           any(r["cls"] == "" for r in rows) and any(r["cls"] == "hunk" for r in rows)
           and not tint2.get("badge") and tint2.get("head", "").endswith("乙.md"), str(tint2)[:140])


async def case_history(page: Page, ck: Check, vault: Path) -> None:
    """历史视图：只有带 year 的节点进图、泳道与刻度、被激活金线、滑块回放、全程不写结构布局。"""
    # layer 故意跨主题：甲在「组A/测试」但属于硬件层，庚在「另一域」却是 AI应用层——
    # 这正是两个维度正交的意义，按层分泳道时它们会重新排队
    (vault / "nodes/组A/甲.md").write_text(
        "---\nname: 甲\nfield: 测试\ndesc: 甲\nyear: 1990\nlayer: 硬件\n---\n# 甲\n\n正文\n\n"
        "## 关系\n- 被激活:: [[丙]] (2005)\n", "utf-8")
    (vault / "nodes/组B/丙.md").write_text(
        "---\nname: 丙\nfield: 测试\ndesc: 丙说明\nyear: 2005\nlayer: 理论\n---\n# 丙\n\n正文\n\n"
        "## 关系\n- 演化为:: [[庚]] (2015)\n", "utf-8")
    (vault / "nodes/组B/庚.md").write_text(
        "---\nname: 庚\nfield: 另一域\ndesc: 庚\nyear: 2015\nlayer: AI应用\n---\n# 庚\n\n正文\n", "utf-8")
    # 有效期节点：2000 年起、2010 年废止，用来验 F4.5
    (vault / "nodes/组B/辛.md").write_text(
        "---\nname: 辛\nfield: 另一域\ndesc: 辛\nyear: 2000\nstart_year: 2000\nend_year: 2010\n---\n"
        "# 辛\n\n正文\n", "utf-8")
    await menu_click(page, "重新加载")
    await switch_mode(page, "全局图")          # 结构图上是卡片，历史图上才是圆点
    await wait_render(page, 4)
    before = ck.layout()["revision"]

    await switch_mode(page, "历史")
    lanes = await poll(page, "document.querySelectorAll('[data-shape=\"kg-lane\"]').length",
                       lambda v: (v or 0) >= 2, timeout=15)
    ck.add("按 field 分出泳道", (lanes or 0) == 2, f"{lanes} 条泳道（测试 / 另一域）")
    ticks = await poll(page, "document.querySelectorAll('[data-shape=\"kg-tick\"]').length",
                       lambda v: (v or 0) >= 4, timeout=8)
    ck.add("X 轴画出年份刻度", (ticks or 0) == 4, f"{ticks} 个刻度（1990/2000/2005/2015）")
    shown = await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-dot"]')]
      .map((e) => e.getAttribute('data-cell-id')).sort())""")
    ck.add("没有 year 的节点不进历史图", json.loads(shown) == ["丙", "庚", "甲", "辛"], shown)
    # 圆心必须**正对年份刻度**：以前放的是 196px 宽的卡片、左沿对齐年份，
    # 一张卡横跨好几年，看不出它到底是哪一年的
    align = json.loads(await page.ev("""JSON.stringify((() => {
      const dot = (id) => { const e = document.querySelector(`[data-cell-id="${id}"] circle`);
        const r = e.getBoundingClientRect(); return r.x + r.width / 2 };
      const tick = (y) => { const e = document.querySelector(`[data-cell-id="tick:${y}"] rect`);
        const r = e.getBoundingClientRect(); return r.x + r.width / 2 };
      return { 甲: Math.round(dot('甲') - tick(1990)), 丙: Math.round(dot('丙') - tick(2005)),
               庚: Math.round(dot('庚') - tick(2015)) };
    })())"""))
    ck.add("圆心对准年份刻度", all(abs(v) <= 2 for v in align.values()), str(align))
    named = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-dot"]')]
      .map((e) => ({ id: e.getAttribute('data-cell-id'),
                     首字: e.querySelector('text')?.textContent || '',
                     全名: [...e.querySelectorAll('text')].pop()?.textContent || '' })))"""))
    ck.add("圆里是名字的第一个字，全名在旁边",
           all(x["首字"] == x["id"][0] and x["全名"] == x["id"] for x in named), str(named))

    gold = await page.ev("document.querySelectorAll('.x6-edge path.kg-flow').length")
    ck.add("被激活画成金色流动虚线", (gold or 0) == 1, f"{gold} 条")

    # 只数个数抓不到"全叠在原点"这种错（CSS transform 会盖掉 SVG 的 transform 属性），
    # 所以要按屏幕坐标核对：年份越晚的节点越靠右，且彼此不重叠。
    rects = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-dot"]')]
      .map((e) => { const r = e.getBoundingClientRect();
        return [e.getAttribute('data-cell-id'), Math.round(r.x), Math.round(r.y)]; }))"""))
    at = dict((r[0], r[1]) for r in rects)
    ordered = at.get("甲", 0) < at.get("丙", 0) < at.get("庚", 0)
    ck.add("节点按年份从左到右真的分开了", ordered and len({r[1] for r in rects}) == 4,
           f"甲 {at.get('甲')} < 丙 {at.get('丙')} < 庚 {at.get('庚')}")

    # 记住 1990 之前那个节点的 DOM 元素，等会儿用它验"拖滑块没有重建 cell"
    await page.ev("""(() => { window.__probe = document.querySelector('[data-cell-id="甲"]');
      window.__probeCount = document.querySelectorAll('[data-shape="kg-dot"]').length; return 'ok'; })()""")

    # 滑块拖到 1990：节点**不消失**，只是 1990 之后的淡成"未来"
    await page.ev("""(() => {
      const el = document.querySelector('.float.player .range');
      el.value = '1990';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return 'moved';
    })()""")
    now = await poll(page, """JSON.stringify({
      all: document.querySelectorAll('[data-shape="kg-dot"]').length,
      on: document.querySelectorAll('[data-shape="kg-dot"]:not(.kg-future)').length })""",
                     lambda v: v and json.loads(v)["on"] == 1, timeout=12)
    got = json.loads(now or "{}")
    ck.add("时间游标把未来的点淡下去（而不是删掉）",
           got.get("all") == 4 and got.get("on") == 1, f"图上 {got.get('all')} 个，已发生 {got.get('on')} 个")

    # 这一条才是"不闪"的根据：拖滑块只加减 class，DOM 元素必须还是原来那一个。
    # 以前每动一下就 fromJSON 重建整张图，节点 DOM 一换、入场动画就重放一遍。
    same = await page.ev("""(() => {
      const el = document.querySelector('[data-cell-id="甲"]');
      return JSON.stringify({ same: el === window.__probe, live: !!el && el.isConnected });
    })()""")
    kept = json.loads(same or "{}")
    ck.add("拖滑块不重建 cell（同一个 DOM 元素）",
           kept.get("same") is True and kept.get("live") is True, same)

    # 游标本体：药丸上写着当前年份，位置随年份右移
    cur = json.loads(await page.ev("""(() => {
      const el = document.querySelector('[data-shape="kg-cursor"]');
      if (!el) return JSON.stringify({ missing: true });
      const r = el.getBoundingClientRect();
      return JSON.stringify({ x: Math.round(r.x), text: el.textContent.trim(),
                              shown: getComputedStyle(el).display !== 'none' });
    })()"""))
    ck.add("时间游标画出来了，标着当前年份", cur.get("text") == "1990" and cur.get("shown") is True, str(cur))

    await page.ev("""(() => {
      const el = document.querySelector('.float.player .range');
      el.value = '2015';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return 'moved';
    })()""")
    moved = await poll(page, """(() => { const el = document.querySelector('[data-shape="kg-cursor"]');
      return Math.round(el.getBoundingClientRect().x); })()""",
                      lambda v: v is not None and v > cur.get("x", 0), timeout=12)
    ck.add("游标随年份右移", (moved or 0) > cur.get("x", 0), f"1990 在 {cur.get('x')}，2015 在 {moved}")

    # 有效期过滤：辛 2000 年起、2010 年废止，2015 年时它该被划进"未来/已失效"那一边
    await poll(page, "document.querySelectorAll('[data-shape=\"kg-dot\"]:not(.kg-future)').length",
               lambda v: v == 4, timeout=12)
    await click_text(page, ".float.player button", "有效期")
    ids = await poll(page, """JSON.stringify([...document.querySelectorAll(
      '[data-shape="kg-dot"]:not(.kg-future)')].map((e) => e.getAttribute('data-cell-id')).sort())""",
                     lambda v: v and "辛" not in v, timeout=12)
    ck.add("有效期把当年已废止的节点淡掉", "辛" not in (ids or "x"), f"2015 年仍有效的是 {ids}")
    ck.add("已废止的节点仍留在图上（看得见它曾经存在）",
           await page.ev("!!document.querySelector('[data-cell-id=\"辛\"]')"), "")
    await click_text(page, ".float.player button", "有效期")     # 关掉，别影响后面的用例

    # 回放按"有事发生的年份"跳站，不是一年一格空转
    stations = await page.ev("JSON.stringify(__kg.histPlan?.eventYears || [])")
    ck.add("回放按事件年份跳站（不是逐个日历年）",
           json.loads(stations or "[]") == [1990, 2000, 2005, 2015], stations)
    await page.ev("document.querySelector('.float.player .icon-btn').click() || 'ok'")   # ▶
    await asyncio.sleep(1.8)
    walked = json.loads(await page.ev("""JSON.stringify({
      yr: document.querySelector('.float.player .yr')?.textContent.trim(),
      cursor: document.querySelector('[data-shape="kg-cursor"]')?.textContent.trim(),
      probeAlive: !!window.__probe && window.__probe.isConnected })"""))
    ck.add("按下播放后游标真的在往前走", "≤" in (walked.get("yr") or ""), str(walked))
    ck.add("回放全程不重建 cell（所以不会一闪一闪）", walked.get("probeAlive") is True, str(walked))
    await page.ev("document.querySelector('.float.player .icon-btn').click() || 'ok'")   # ⏸
    await asyncio.sleep(0.3)

    # 切一条时间线：泳道换成所选分组的直接子分组
    await open_rail(page, "时间线")
    await click_text(page, "aside.timeline .tl-btn", "组B")
    lane_names = await poll(page, """JSON.stringify([...document.querySelectorAll('[data-shape="kg-lane"] text')]
      .map((t) => t.textContent))""", lambda v: v and v != "[]", timeout=12)
    ck.add("选中分组后泳道跟着换", "组B" in (lane_names or ""), lane_names or "没取到泳道名")

    # 「按抽象层」：和主题正交的另一个维度，泳道从下往上是 理论 → … → AI应用
    # （时间线面板上一步已经开着了，再 open_rail 会把它关掉）
    await click_text(page, ".timeline .tl-btn", "按抽象层")
    await asyncio.sleep(1.0)
    lanes = await poll(page, """JSON.stringify([...document.querySelectorAll('[data-shape="kg-lane"]')]
      .sort((a, b) => a.getBoundingClientRect().y - b.getBoundingClientRect().y)
      .map(el => el.textContent.trim()))""", lambda v: v and v != "[]", timeout=12)
    got = json.loads(lanes or "[]")
    ck.add("按抽象层分泳道", set(got) >= {"理论", "硬件", "AI应用"}, str(got))
    ck.add("泳道按层次排序，不是字典序", got.index("AI应用") < got.index("理论") if
           ("AI应用" in got and "理论" in got) else False, f"{got}（上→下）")

    # 关系族开关：真 input 是 opacity:0 的 absolute 元素，它的包含块必须是 .switch-row 自己。
    # 一旦落到 .drawer 上，抽屉内容一滚动 input 就留在原地、跑到视口外；点标题触发 focus()，
    # 浏览器为了把焦点滚进视野去滚根容器，而 body / .app 都是 overflow: hidden，
    # 鼠标滚不回来——表现就是"点演化/依赖/对照，整屏往上拽、下半截全黑"。
    await page.ev("""(() => {
      const body = document.querySelector('aside.timeline .drawer-body');
      body.scrollTop = body.scrollHeight;          // 滚到底，「显示的关系族」那一节露出来
      return 'ok';
    })()""")
    await asyncio.sleep(0.3)
    geo = json.loads(await page.ev("""(() => {
      const rows = [...document.querySelectorAll('aside.timeline .switch-row')];
      const row = rows[rows.length - 1];           // 「对照」那一行，抽屉最底下
      const input = row.querySelector('input');
      const rr = row.getBoundingClientRect();
      const ir = input.getBoundingClientRect();
      input.focus();                                // 点 label 时浏览器做的就是这一步
      row.click();
      return JSON.stringify({ inside: ir.top >= rr.top - 1 && ir.bottom <= rr.bottom + 1,
        rowTop: Math.round(rr.top), inputTop: Math.round(ir.top) });
    })()"""))
    ck.add("关系族开关的隐藏 input 待在自己那一行里",
           geo["inside"], f"行 top={geo['rowTop']}，input top={geo['inputTop']}")
    await asyncio.sleep(0.4)
    scrolled = json.loads(await page.ev("""JSON.stringify({
      app: Math.round(document.querySelector('.app').scrollTop),
      body: Math.round(document.body.scrollTop),
      doc: Math.round(document.scrollingElement.scrollTop) })"""))
    ck.add("点关系族开关不会把整屏拽上去",
           scrolled == {"app": 0, "body": 0, "doc": 0}, str(scrolled))

    await open_rail(page, "时间线")

    ck.add("历史视图不修改结构布局", ck.layout()["revision"] == before,
           f"revision 仍是 {before}")

    await switch_mode(page, "全局图")
    back = await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length",
                      lambda v: (v or 0) >= 4, timeout=15)
    ck.add("切回结构视图恢复原图（卡片，不是圆点）", (back or 0) >= 4, f"{back} 个节点")


# ---------------------------------------------------------------- 谱系树

async def case_lineage(page: Page, ck: Check) -> None:
    """谱系树：只画演化族，根在下、叶在上，枝越粗挂的东西越多。

    和历史视图的区别要钉住：历史的 X 轴是年份，谱系的 Y 轴是"从什么长出什么"。
    """
    await switch_mode(page, "谱系")
    got = json.loads(await poll(page, """JSON.stringify((() => {
      const g = __kg.graph
      const ns = g.getNodes()
      if (!ns.length) return null
      const box = (id) => { const c = g.getCellById(id); return c ? c.getBBox() : null }
      const fam = g.getEdges().map((e) => (e.getData() || {}).family)
      const w = (id) => { const c = g.getCellById(id); return c ? c.attr('line/strokeWidth') : null }
      return { 节点: ns.length, 边族: [...new Set(fam)],
               甲y: box('甲')?.y, 丙y: box('丙')?.y, 庚y: box('庚')?.y,
               甲丙: w('甲->丙#被激活'), 丙庚: w('丙->庚#演化为') };
    })())""", lambda v: v and v != "null", timeout=12) or "{}")
    ck.add("谱系树只画演化族", got.get("边族") == ["演化"], str(got.get("边族")))
    ck.add("根在下、叶在上（甲 1990 → 丙 2005 → 庚 2015）",
           got.get("甲y") > got.get("丙y") > got.get("庚y"),
           f"y：甲 {got.get('甲y')} > 丙 {got.get('丙y')} > 庚 {got.get('庚y')}")
    # 甲 这条枝下面挂着丙和庚两个点，丙 那条只挂着庚一个——所以甲那条必须更粗
    ck.add("枝越粗，挂在上面的东西越多",
           (got.get("甲丙") or 0) > (got.get("丙庚") or 0),
           f"甲→丙 {got.get('甲丙')} > 丙→庚 {got.get('丙庚')}")
    # 结构布局不受影响：谱系树和历史一样是算出来的视图，不落盘
    await switch_mode(page, "全局图")
    await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length",
               lambda v: (v or 0) >= 4, timeout=12)


# ---------------------------------------------------------------- 横向对比

COMPARE_GROUP_MD = ("---\nid: compare-test\nname: 甲乙对比\nfield: 测试\ntype: 对比组\n"
                    "dimensions: [年份, 核心方法, 适用场景]\ndesc: 甲和乙横着比一遍\n---\n"
                    "# 甲乙对比\n\n## 关系\n- 包含:: [[乙]]\n- 包含:: [[甲]]\n")


async def case_compare_edit(page: Page, ck: Check, vault: Path) -> None:
    """双击改一格 → 写回 md 的 `## 速查` 里那一行。

    最该钉住的是**只改那一行**：这条路存在的全部理由就是不走 `update_body`——
    整段替换要求把全文背回来，改一格表就把没看见的那半篇删掉。
    """
    md_before = (vault / "nodes" / "组A" / "乙.md").read_text("utf-8")
    # 表头此时是 成员|年份|核心方法|适用场景|其它（上一段刚把「核心方法」勾了回来）
    out = await page.ev("""(() => {
      const tr = [...document.querySelectorAll('.cmp-table tbody tr')]
        .find((r) => r.querySelector('th').textContent.includes('乙'));
      if (!tr) return 'no-row';
      const th = [...document.querySelectorAll('.cmp-table thead th')].map((x) => x.textContent.trim());
      const td = tr.querySelectorAll('td')[th.indexOf('适用场景') - 1];
      td.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      return 'ok';
    })()""")
    assert out == "ok", out
    typed = await poll(page, """(() => {
      const el = document.querySelector('.cmp-table .cell-edit');
      if (!el) return '';
      el.value = '改成这句';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      return 'ok';
    })()""", lambda v: v == "ok", timeout=8)
    ck.add("双击开得出编辑框", typed == "ok", str(typed))

    text = await poll_file(vault / "nodes" / "组A" / "乙.md", "改成这句")
    ck.add("改一格写进了 `## 速查` 那一行", "- 适用场景:: 改成这句" in text,
           text.split("## 速查")[-1].strip()[:70] if "## 速查" in text else text[-70:])
    # **真正要钉住的不变量**：除了那一行，原文每一行都还在。
    # （不逐字比整篇：这个文件本来没有 `## 关系` 段，writer 会补一个空的——
    #  那是所有变更类型共有的归一化，不是 set_fact 干的。）
    kept = set(text.splitlines())
    lost = [ln for ln in md_before.splitlines() if ln.strip() and ln not in kept]
    ck.add("只改那一行：原文其余每一行都还在", lost == ["- 适用场景:: 只有乙写了"],
           str(lost))

    shown = await poll(page, """(() => {
      const tr = [...document.querySelectorAll('.cmp-table tbody tr')]
        .find((r) => r.querySelector('th').textContent.includes('乙'));
      return tr ? tr.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "改成这句" in v, timeout=10)
    ck.add("表格立刻跟着变", "改成这句" in (shown or ""), (shown or "")[:60])

    # frontmatter 那几列不给在表里改：它们的真相在 frontmatter，从正文写第二份就是双源
    fm_edit = await page.ev("""(() => {
      const tr = [...document.querySelectorAll('.cmp-table tbody tr')]
        .find((r) => r.querySelector('th').textContent.includes('乙'));
      const th = [...document.querySelectorAll('.cmp-table thead th')].map((x) => x.textContent.trim());
      const td = tr.querySelectorAll('td')[th.indexOf('年份') - 1];
      td.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      return document.querySelector('.cmp-table .cell-edit') ? 'editable' : 'readonly';
    })()""")
    ck.add("年份那一列不给在表里改（真相在 frontmatter）", fm_edit == "readonly", str(fm_edit))


async def case_compare(page: Page, ck: Check, vault: Path) -> None:
    """对比组：目录 → 它自己的画布 → 下面那张表。**全程不碰全局图。**

    这是第三种 layout（全局 / 项目 / 对比），所以最该钉住的是"进了对比画布之后，
    拖东西写的是对比那份文件、全局图一个字没动"。
    """
    (vault / "fields" / "对比组").mkdir(parents=True, exist_ok=True)
    (vault / "fields" / "对比组" / "compare-test.md").write_text(COMPARE_GROUP_MD, "utf-8")
    for rel, facts in (
            ("nodes/组A/甲.md", "## 速查\n- 核心方法:: 一样的做法\n- 实现方式:: 甲的实现\n"),
            ("nodes/组A/乙.md", "## 速查\n- 核心方法:: 一样的做法\n- 适用场景:: 只有乙写了\n"
                                "- 实现方式:: 乙的实现\n")):
        f = vault / rel
        text = f.read_text("utf-8")
        # **插在 `## 关系` 之前**：写到后面的话取数、出题、摘要全都读不到（规范 3 的硬约束）。
        # 前面的用例可能已经给这两个文件加过关系段，所以不能图省事往文件尾巴上追加。
        at = text.find("## 关系")
        f.write_text((text[:at] + facts + "\n" + text[at:]) if at >= 0
                     else text.rstrip() + "\n\n" + facts, "utf-8")

    before = ck.layout()
    await use_scope(page, "")
    await switch_mode(page, "全局图")
    await open_rail(page, "对比")
    listed = await poll(page, """(() => {
      const a = document.querySelector('aside.study');
      return a ? a.textContent.replace(/\s+/g, ' ') : '';
    })()""", lambda v: v and "甲乙对比" in v, timeout=12)
    ck.add("对比目录里列出了这个组", "甲乙对比" in (listed or ""), (listed or "")[:70])
    ck.add("目录写明成员数和空格子数", "2 个成员" in (listed or "") and "格空" in (listed or ""),
           (listed or "")[:90])

    await click_text(page, "aside.study .tl-btn", "甲乙对比")
    header = await poll(page, """(() => {
      const t = document.querySelector('.cmp-table thead');
      return t ? [...t.querySelectorAll('th')].map((x) => x.textContent.trim()).join('|') : '';
    })()""", lambda v: v and "成员" in v, timeout=12)
    # 「核心方法」两行值一样 → 整列一致，默认收起；「其它」是残差列
    ck.add("表头 = 成员 + 有差异的列 + 残差列",
           header == "成员|年份|适用场景|其它（速查里没归类的）", header)

    rows = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('.cmp-table tbody tr')]
      .map((tr) => [...tr.querySelectorAll('th,td')].map((c) => c.textContent.trim())))"""))
    ck.add("行序跟着 md 里 `- 包含::` 的书写顺序（乙在前）",
           [r[0] for r in rows] == ["乙", "甲"], str([r[0] for r in rows]))
    ck.add("没填的格子留空不报错", rows[1][2] == "—", str(rows[1]))
    ck.add("残差列把没归类的键原样摆出来", "实现方式" in rows[0][3], rows[0][3])

    promote = await page.ev("document.querySelector('.cmp-promote')?.textContent.replace(/\s+/g, ' ') || ''")
    ck.add("两个成员都写了的残差键，提示升成一列", "实现方式" in (promote or ""), (promote or "")[:70])

    # 列可选：勾回「核心方法」，它该出现在表头里；这只是视图偏好，不回写 md
    md_before = (vault / "nodes" / "组A" / "甲.md").read_text("utf-8")
    await click_text(page, ".cmp-head .btn", "列")
    await click_text(page, ".cmp-cols .chip", "核心方法")
    await asyncio.sleep(0.4)
    header2 = await page.ev("""[...document.querySelectorAll('.cmp-table thead th')]
      .map((x) => x.textContent.trim()).join('|')""")
    ck.add("勾上之后整列一致的那列也出得来", "核心方法" in (header2 or ""), header2)
    ck.add("改列不碰 md", (vault / "nodes" / "组A" / "甲.md").read_text("utf-8") == md_before, "")

    await case_compare_edit(page, ck, vault)

    canvas = await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length",
                        lambda v: (v or 0) == 2, timeout=12)
    ck.add("对比画布上只有这个组的成员", canvas == 2, f"{canvas} 个")

    own = get(ck.api + "/api/layout?layout=compare-test")["layout"]
    ck.add("对比画布自己一份 layout", set(own["nodes"]) == {"甲", "乙"}, str(sorted(own["nodes"])))
    after = ck.layout()
    ck.add("全局图一个字没动", after["revision"] == before["revision"]
           and "compare-test" not in after["nodes"], f"r{before['revision']} → r{after['revision']}")

    await switch_mode(page, "全局图")
    await poll(page, "document.querySelectorAll('[data-shape=\"kg-node\"]').length",
               lambda v: (v or 0) >= 4, timeout=12)


# ---------------------------------------------------------------- 参数量图表

async def case_stats(page: Page, ck: Check, vault: Path) -> None:
    """params 字段 → 两张图。**只画填了的那些**，并且把"多少个没填"写在脸上。"""
    for nid, path, val in (("甲", "nodes/组A/甲.md", "175B"), ("丙", "nodes/组B/丙.md", "340M"),
                           ("庚", "nodes/组B/庚.md", "1.3万亿")):
        f = vault / path
        f.write_text(f.read_text("utf-8").replace("desc:", f"params: {val}\ndesc:", 1), "utf-8")
    # 看不懂的写法：只警告，不该让这个点消失，也不该让 index 变成"有错误"
    bad = vault / "nodes/组B/丁.md"
    bad.write_text(bad.read_text("utf-8").replace("desc:", "params: 大概很多\ndesc:", 1), "utf-8")
    await menu_click(page, "重新加载")
    await asyncio.sleep(1.2)

    await switch_mode(page, "全局图")
    await open_rail(page, "参数量")
    got = json.loads(await poll(page, """JSON.stringify((() => {
      const box = document.querySelector('aside.study')
      if (!box || !box.querySelector('.chart')) return null
      return { 点: box.querySelectorAll('.chart .pt').length,
               线: box.querySelectorAll('.chart .ln').length,
               柱: [...box.querySelectorAll('.bar-row')].map((r) =>
                 [r.querySelector('.nm').textContent.trim(), r.querySelector('.val').textContent.trim()]),
               刻度: [...box.querySelectorAll('.chart .axis text')].map((t) => t.textContent.trim()),
               说明: box.querySelector('.dim')?.textContent.replace(/\\s+/g, '') || '' };
    })())""", lambda v: v and v != "null", timeout=12) or "{}")
    ck.add("填了 params 的点都进了图", got.get("点") == 3, str(got.get("点")))
    ck.add("柱状按参数量从大到小排",
           [x[1] for x in got.get("柱", [])] == ["1.3T", "175B", "340M"], str(got.get("柱")))
    ck.add("Y 轴是对数（刻度按 10 的幂走）",
           any(t in ("100M", "1B", "10B", "100B", "1T") for t in got.get("刻度", [])), str(got.get("刻度")))
    ck.add("说清楚多少个没填", "个填了参数量" in got.get("说明", ""), got.get("说明", "")[:60])
    idx = get(page.api + "/api/index")
    ck.add("看不懂的 params 只警告、不丢节点",
           any(d["code"] == "bad_params" for d in idx["warnings"]) and not idx["errors"],
           str(idx["warnings"])[:120])
    await open_rail(page, "参数量")


async def case_schools(page: Page, ck: Check, vault: Path) -> None:
    """流派时间带：带子画出来、按起始年装箱、**带↔点的演化边不能断**。

    最后一条是这个功能最容易悄悄坏掉的地方：一个概念从圆点变成带子之后，
    如果边的过滤只认圆点，`流派 --演化为--> 某个技术` 就会**无声消失**——
    没有报错、图照常画，只是那条线不见了。而那根线正是"融合"的全部表达。
    """
    (vault / "fields").mkdir(exist_ok=True)
    (vault / "fields/流派").mkdir(parents=True, exist_ok=True)
    (vault / "fields/流派/老派.md").write_text(
        "---\nname: 老派\nfield: 测试\ntype: 流派\nstart_year: 1985\nend_year: 2000\n"
        "color: \"#5b8def\"\ndesc: 老派\n---\n# 老派\n\n## 关系\n"
        "- 包含:: [[甲]]\n- 演化为:: [[丙]] (2005)\n", "utf-8")
    (vault / "fields/流派/新派.md").write_text(
        "---\nname: 新派\nfield: 测试\ntype: 流派\nstart_year: 2005\n"
        "color: \"#d08a3e\"\ndesc: 新派\n---\n# 新派\n\n## 关系\n"
        "- 包含:: [[丙]]\n- 包含:: [[庚]]\n", "utf-8")
    await menu_click(page, "重新加载")
    await switch_mode(page, "历史")
    bands = await poll(page, "document.querySelectorAll('[data-shape=\"kg-band\"]').length",
                       lambda v: (v or 0) >= 2, timeout=15)
    ck.add("流派画成时间带", (bands or 0) == 2, f"{bands} 条带子（老派 / 新派）")

    ids = await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-band"]')]
      .map((e) => e.getAttribute('data-cell-id')).sort())""")
    ck.add("带子的 id 就是流派节点 id", json.loads(ids) == ["新派", "老派"], ids)

    # 老派 1985–2000、新派 2005– 不重叠 → 同一行；重叠才换行
    same = await page.ev("""(() => {
      const y = [...document.querySelectorAll('[data-shape="kg-band"]')]
        .map((e) => e.getBoundingClientRect().top)
      return Math.abs(y[0] - y[1]) < 4
    })()""")
    ck.add("不重叠的两条带子排同一行", bool(same), "能同行就同行，撞上才换行")

    dots = await page.ev("""JSON.stringify([...document.querySelectorAll('[data-shape="kg-dot"]')]
      .map((e) => e.getAttribute('data-cell-id')).sort())""")
    ck.add("流派不再画成圆点", "老派" not in json.loads(dots) and "新派" not in json.loads(dots),
           f"圆点：{dots}")

    # **带 → 点的演化边**：老派 --演化为--> 丙
    linked = await page.ev("""[...document.querySelectorAll('.x6-edge')]
      .some((e) => (e.getAttribute('data-cell-id') || '').includes('老派'))""")
    ck.add("带↔点的演化边没断", bool(linked), "老派 演化为→ 丙 这条线要画出来")

    curve = await page.ev("""document.querySelector('[data-shape="kg-band"] polyline')
      ?.getAttribute('points')?.length > 0""")
    ck.add("带子背后有累计走势", bool(curve), "polyline 有点")


async def case_tour(page: Page, ck: Check) -> None:
    """沿演化链导览：跟着最长的那条「谁接谁」一站站走，镜头推过去、游标跟着走。

    fixture 里的链是 甲(1990) —被激活→ 丙(2005) —演化为→ 庚(2015)，共 3 站。
    """
    await switch_mode(page, "历史")
    await wait_render(page, 4, shape="kg-dot")
    chain = await poll(page, "JSON.stringify(__kg.histPlan?.chain || [])",
                       lambda v: v and v != "[]", timeout=12)
    ck.add("泳道布局下也算得出演化链（导览不依赖主干道）",
           json.loads(chain or "[]") == ["甲", "丙", "庚"], chain or "没算出链")

    await click_text(page, ".float.player button", "导览")
    card = await poll(page, """(() => { const el = document.querySelector('.float.tour');
      return el ? el.textContent.replace(/\s+/g, ' ') : ''; })()""",
                     lambda v: v and "1/3" in v, timeout=10)
    ck.add("导览卡片浮出来，停在第一站", "1/3" in (card or "") and "甲" in (card or ""), (card or "")[:80])

    at = json.loads(await page.ev("""JSON.stringify({
      stop: document.querySelector('.x6-node.kg-stop')?.getAttribute('data-cell-id') || null,
      stops: document.querySelectorAll('.x6-node.kg-stop').length,
      cursor: document.querySelector('[data-shape="kg-cursor"]')?.textContent.trim() })"""))
    ck.add("当前这一站在图上点亮（且同时只有一个）",
           at.get("stop") == "甲" and at.get("stops") == 1, str(at))
    ck.add("游标跟着导览走到那一年（导览管节奏，游标管坐标）", at.get("cursor") == "1990", str(at))

    # 下一站：卡片、点亮、游标、镜头四样都得跟上
    before = json.loads(await page.ev("JSON.stringify(__kg.graph.translate())"))
    await click_text(page, ".float.tour footer .btn", "下一站")
    moved = await poll(page, """JSON.stringify({
      card: (document.querySelector('.float.tour')?.textContent || '').replace(/\s+/g, ' '),
      stop: document.querySelector('.x6-node.kg-stop')?.getAttribute('data-cell-id') || null,
      cursor: document.querySelector('[data-shape="kg-cursor"]')?.textContent.trim() })""",
                      lambda v: v and json.loads(v)["stop"] == "丙", timeout=10)
    got = json.loads(moved or "{}")
    ck.add("下一站：卡片、点亮、游标一起前进",
           got.get("stop") == "丙" and "2/3" in (got.get("card") or "") and got.get("cursor") == "2005",
           str(got)[:160])
    ck.add("卡片写出了上一站怎么接过来的", "被激活" in (got.get("card") or ""), (got.get("card") or "")[:90])

    # 推镜头是渐进的（flyTo 走 520ms），等它走完再比，而且必须比**数值**——
    # 比 JSON 字符串会被 json.dumps 的空格骗过去：看着通过，其实镜头一动没动。
    await asyncio.sleep(1.0)
    after = json.loads(await page.ev("JSON.stringify(__kg.graph.translate())"))
    shifted = abs(after["tx"] - before["tx"]) + abs(after["ty"] - before["ty"])
    ck.add("镜头真的推过去了（不是原地换个高亮）", shifted > 20,
           f"translate 位移 {shifted:.0f}px")
    # 站点条当目录用：点第 1 站直接跳回去
    await page.ev("document.querySelectorAll('.float.tour .stops button')[0].click() || 'ok'")
    back = await poll(page, "document.querySelector('.x6-node.kg-stop')?.getAttribute('data-cell-id')",
                      lambda v: v == "甲", timeout=10)
    ck.add("站点条能当目录点（跳回第一站）", back == "甲", str(back))

    # Esc 退出：卡片收掉、点亮摘掉、边的高亮还原
    await page.ev("""window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))""")
    gone = await poll(page, """JSON.stringify({ card: !!document.querySelector('.float.tour'),
      stop: document.querySelectorAll('.x6-node.kg-stop').length,
      dimmed: [...document.querySelectorAll('.x6-edge path')]
        .filter((p) => +(p.getAttribute('opacity') || 1) < 0.2).length })""",
                      lambda v: v and json.loads(v)["card"] is False, timeout=10)
    out = json.loads(gone or "{}")
    ck.add("Esc 退出导览，点亮和边高亮一起还原",
           out.get("card") is False and out.get("stop") == 0 and out.get("dimmed") == 0, str(out))


async def right_click(page: Page, cell: str, grab: str = "center") -> str:
    """在某个 cell 上按右键。分组要点标题条，点中间会命中里面的节点。"""
    origin = ("{ x: r.x + 40, y: r.y + 12 }" if grab == "title"
              else "{ x: r.x + r.width / 2, y: r.y + r.height / 2 }")
    out = await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="{cell}"]');
      if (!el) return 'missing';
      const r = el.getBoundingClientRect();
      const p = {origin};
      const t = document.elementFromPoint(p.x, p.y) || el;
      t.dispatchEvent(new MouseEvent('contextmenu', {{ bubbles: true, cancelable: true,
        clientX: p.x, clientY: p.y, view: window, button: 2 }}));
      return 'ok';
    }})()""")
    assert out == "ok", f"右键点不到 {cell}：{out}"
    await asyncio.sleep(0.4)
    return out


async def menu_pick(page: Page, text: str) -> None:
    await click_text(page, ".ctx-menu .pop-item", text)
    await asyncio.sleep(0.5)


async def case_wheel_pan(page: Page, ck: Check) -> None:
    """Mac 触控板两指上下滑就是普通 wheel 事件：必须平移画布，不能缩放。"""
    before = json.loads(await page.ev(
        "JSON.stringify({ z: +__kg.graph.zoom().toFixed(3), ty: Math.round(__kg.graph.translate().ty) })"))
    await page.ev("""(() => {
      const el = document.querySelector('.canvas');
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: 200, bubbles: true, cancelable: true }));
      return 'ok';
    })()""")
    await asyncio.sleep(0.4)
    after = json.loads(await page.ev(
        "JSON.stringify({ z: +__kg.graph.zoom().toFixed(3), ty: Math.round(__kg.graph.translate().ty) })"))
    ck.add("两指上下滑 = 平移画布（不是缩放）",
           after["z"] == before["z"] and after["ty"] == before["ty"] - 200,
           f"缩放 {before['z']} → {after['z']}，ty {before['ty']} → {after['ty']}")

    await page.ev("""(() => {
      const el = document.querySelector('.canvas');
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: -200, metaKey: true, bubbles: true, cancelable: true,
        clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 }));
      return 'ok';
    })()""")
    zoomed = await poll(page, "+__kg.graph.zoom().toFixed(3)", lambda v: v and v != after["z"], timeout=4)
    ck.add("⌘ + 滚轮仍然缩放", zoomed != after["z"], f"{after['z']} → {zoomed}")
    await page.ev(f"__kg.graph.zoomTo({before['z']}); __kg.graph.translate(0, {before['ty']})")
    await asyncio.sleep(0.4)


async def case_cluster_drag(page: Page, ck: Check) -> None:
    """拖簇卡片 = 拖它代表的分组：分组框要真的动，且不能在 nodes 里留下同名幽灵记录。"""
    # 折叠阈值按"卡片在屏幕上还剩多大"定（LEAF_ZOOM 0.28），0.3 还不折
    await page.ev("__kg.graph.zoomTo(0.2)")
    gid = await poll(page, """document.querySelector('[data-shape="kg-cluster"]')
      ?.getAttribute('data-cell-id') || ''""", lambda v: bool(v))
    if not gid:
        ck.add("拖簇卡片保存分组位置", False, "没折叠出簇卡片")
        return
    before = ck.layout()
    inside = [nid for nid, n in before["nodes"].items() if n.get("group") == gid]
    await drag(page, gid, dx=160, dy=90)
    await asyncio.sleep(1.4)
    after = ck.layout()
    moved = round(after["groups"][gid]["x"] - before["groups"][gid]["x"])
    ghost = gid in after["nodes"]
    ck.add("拖簇卡片写回分组坐标（不是当成节点写）", moved != 0 and not ghost,
           f"分组 x 位移 {moved}，nodes 里有幽灵记录 {ghost}")
    if inside:
        nid = inside[0]
        kid = round(after["nodes"][nid]["x"] - before["nodes"][nid]["x"])
        ck.add("簇里的节点跟着簇一起移动", kid == moved, f"节点位移 {kid}，分组位移 {moved}")
    await page.ev("__kg.graph.zoomTo(1)")
    await asyncio.sleep(0.8)


async def case_group_menu(page: Page, ck: Check) -> None:
    """右键分组：折叠 / 钉住展开 / 恢复自动——pinned 以前没有任何界面能改。"""
    # 先在一个**里面有两个以上点**的框上看第六步那一项：概括只在这种框上出现，拿空框测等于没测。
    # 看完 Esc 关掉，折叠 / 钉住那套仍用页面上第一个框（子框折叠后右键命中的是别的东西，老流程别动）
    lay = ck.layout()
    tally = {}
    for n in lay["nodes"].values():
        if n.get("group"):
            tally[n["group"]] = tally.get(n["group"], 0) + 1
    rich = next((g for g, c in sorted(tally.items(), key=lambda kv: -kv[1]) if c >= 2), None)
    if rich and await page.ev(f"""!!document.querySelector('[data-shape="kg-group"][data-cell-id="{rich}"]')"""):
        await right_click(page, rich, grab="title")
        labels = json.loads(await page.ev("""JSON.stringify([...document.querySelectorAll('.ctx-menu .pop-item')].map((b) => b.textContent.trim()))""") or "[]")
        ck.add("框里有两个以上的点时给「概括成一个节点」（第六步；不点，那是模型调用）",
               any("概括成一个节点" in x for x in labels), f"{tally.get(rich)} 个点 · {labels}")
        ck.add("分组菜单里有「解散这个框」", any("解散这个框" in x for x in labels), str(labels))
        await page.ev("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
        await asyncio.sleep(0.3)
    else:
        ck.add("框里有两个以上的点时给「概括成一个节点」", False, f"没找到有两个以上点的框：{tally}")

    gid = await poll(page, """document.querySelector('[data-shape="kg-group"]')
      ?.getAttribute('data-cell-id') || ''""", lambda v: bool(v))
    await right_click(page, gid, grab="title")
    opened = await page.ev("!!document.querySelector('.ctx-menu')")
    ck.add("右键分组弹出菜单", bool(opened), "" if opened else "没弹出来")
    if not opened:
        return
    await menu_pick(page, "折叠成簇卡片")
    folded = await poll(page, f"""!!document.querySelector('[data-shape="kg-cluster"][data-cell-id="{gid}"]')""",
                        lambda v: bool(v), timeout=6)
    ck.add("菜单里能把一个域折叠起来", bool(folded),
           f"pinned = {ck.layout()['groups'][gid].get('pinned')}")

    await right_click(page, gid)
    await menu_pick(page, "恢复自动折叠")
    back = await poll(page, f"""!!document.querySelector('[data-shape="kg-group"][data-cell-id="{gid}"]')""",
                      lambda v: bool(v), timeout=6)
    ck.add("恢复自动折叠后 pinned 清空", bool(back) and ck.layout()["groups"][gid].get("pinned") is None,
           f"pinned = {ck.layout()['groups'][gid].get('pinned')}")


async def case_relate_menu(page: Page, ck: Check, vault: Path) -> None:
    """右键节点 → 建立关系 → 选类型 → 搜目标 → 回车：直接写回 md。"""
    await right_click(page, "乙")
    title = await page.ev("document.querySelector('.ctx-menu .ctx-head .t')?.textContent || ''")
    ck.add("右键知识点弹出菜单", title == "乙", f"菜单标题「{title}」")
    await menu_pick(page, "建立关系")
    ck.add("菜单能打开建立关系对话框", bool(await page.ev("!!document.querySelector('.rel-dialog')")))

    await click_text(page, ".rel-dialog .type-chip", "基于")
    await asyncio.sleep(0.4)
    hint = await page.ev("document.querySelector('.rel-dialog .inv-hint')?.textContent || ''")
    ck.add("选完类型当场给出反向读法", "支撑" in hint, hint.strip()[:60])

    await page.ev("""(() => {
      const el = document.querySelector('.rel-dialog .search-row input');
      el.value = '丁';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return 'ok';
    })()""")
    await asyncio.sleep(0.4)
    hit = await page.ev("document.querySelector('.rel-dialog .hits li .nm')?.textContent || ''")
    ck.add("搜索框搜得到目标知识点", hit == "丁", f"第一条命中「{hit}」")
    await click_text(page, ".rel-dialog .hits li", "丁")

    text = await poll_file(vault / "nodes" / "组A" / "乙.md", "基于:: [[丁]]")
    ck.add("确认后直接写回 md（一步到位）", "- 基于:: [[丁]]" in text,
           repr(text.split("## 关系")[-1].strip()[:40]))
    drawn = await poll(page, """document.querySelectorAll('.x6-edge').length""", lambda v: (v or 0) > 0, timeout=10)
    ck.add("新建的关系立刻画在画布上", (drawn or 0) > 0, f"{drawn} 条边")
    closed = await page.ev("!document.querySelector('.rel-dialog')")
    ck.add("写完自动关掉对话框", bool(closed))


async def poll_api(fetch, ok, timeout: float = 8.0):
    """轮询一个接口读取（补丁是延迟批量落盘的，右键之后要等它 flush）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fetch()
        if ok(last):
            return last
        await asyncio.sleep(0.3)
    return last


async def poll_glob(vault: Path, pattern: str, needle: str, timeout: float = 12.0) -> str:
    """等某个还不知道确切路径的新文件出现（新建知识点的落点由对话框决定）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in sorted(vault.glob(pattern)):
            text = p.read_text("utf-8")
            if needle in text:
                return text
        await asyncio.sleep(0.4)
    return ""


async def poll_file(path: Path, needle: str, timeout: float = 12.0) -> str:
    """等服务端把 md 写下来（写回是异步的一次 POST）。"""
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        text = path.read_text("utf-8") if path.exists() else ""
        if needle in text:
            return text
        await asyncio.sleep(0.4)
    return text


async def case_minimap(page: Page, ck: Check) -> None:
    """小地图：关得掉、还得开得回来（只能靠空白右键就太难找了）。"""
    ck.add("小地图默认显示", bool(await page.ev("!!document.querySelector('.float.map svg')")))
    hidden = await page.ev("""(() => {
      const b = [...document.querySelectorAll('.float.zoom .icon-btn')]
        .find((x) => (x.getAttribute('title') || '').includes('小地图'));
      if (!b) return 'missing';
      b.click(); return 'ok';
    })()""")
    ck.add("缩放条上有小地图开关", hidden == "ok", str(hidden))
    await asyncio.sleep(0.4)
    ck.add("能关掉小地图", not await page.ev("!!document.querySelector('.float.map')"))
    await page.ev("""(() => {
      const b = [...document.querySelectorAll('.float.zoom .icon-btn')]
        .find((x) => (x.getAttribute('title') || '').includes('小地图'));
      b.click(); return 'ok';
    })()""")
    await asyncio.sleep(0.4)
    ck.add("关掉之后还能开回来", bool(await page.ev("!!document.querySelector('.float.map svg')")))

    # 拖动标题条把它搬走，位置要记住
    before = await page.ev("JSON.stringify(document.querySelector('.float.map').getBoundingClientRect())")
    await page.ev("""(() => {
      const h = document.querySelector('.float.map .mm-grip');
      const r = h.getBoundingClientRect();
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      const fire = (t, cx, cy) => h.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true,
        clientX: cx, clientY: cy, view: window, button: 0, buttons: t === 'mouseup' ? 0 : 1 }));
      fire('mousedown', x, y);
      for (let i = 1; i <= 6; i++) {
        window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: x + i * 20, clientY: y - i * 10,
          buttons: 1 }));
      }
      window.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: x + 120, clientY: y - 60 }));
      return 'ok';
    })()""")
    await asyncio.sleep(0.5)
    after = await page.ev("JSON.stringify(document.querySelector('.float.map').getBoundingClientRect())")
    moved = json.loads(after)["x"] - json.loads(before)["x"]
    ck.add("小地图可以拖到别处", round(moved) == 120, f"横向移动 {round(moved)}px")
    saved = await page.ev("localStorage.getItem('knowrary-map-box')")
    ck.add("拖过的位置记在本地", bool(saved) and '"x"' in (saved or ""), saved or "没存")


async def case_blank_menu(page: Page, ck: Check) -> None:
    """空白处右键：新建簇 / 贴便签 / 小地图开关都挂在这儿，它不弹别的都白搭。"""
    out = await page.ev("""(() => {
      const el = document.querySelector('.canvas');
      const r = el.getBoundingClientRect();
      // 找一块真空白：从右下角往里试，避开所有 cell
      for (let i = 0; i < 40; i++) {
        const x = r.right - 60 - i * 12;
        const y = r.bottom - 60 - i * 6;
        const hit = document.elementFromPoint(x, y);
        if (hit && !hit.closest('[data-cell-id]') && !hit.closest('.float')) {
          hit.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
            clientX: x, clientY: y, view: window, button: 2 }));
          return 'ok';
        }
      }
      return 'no-blank-spot';
    })()""")
    await asyncio.sleep(0.4)
    items = await page.ev("""JSON.stringify([...document.querySelectorAll('.ctx-menu .pop-item')]
      .map((b) => b.textContent.trim()))""")
    ck.add("空白处右键弹出菜单", out == "ok" and "新建簇（分组框）" in (items or ""), f"{out} · {items}")
    await page.ev("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    await asyncio.sleep(0.3)


async def case_create_node(page: Page, ck: Check, vault: Path) -> None:
    """空白右键 → 新建知识点：写出一个合规的 md，并立刻放到画布上。"""
    out = await page.ev("""(() => {
      const el = document.querySelector('.canvas');
      const r = el.getBoundingClientRect();
      for (let i = 0; i < 40; i++) {
        const x = r.right - 60 - i * 12;
        const y = r.bottom - 60 - i * 6;
        const hit = document.elementFromPoint(x, y);
        if (hit && !hit.closest('[data-cell-id]') && !hit.closest('.float')) {
          hit.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
            clientX: x, clientY: y, view: window, button: 2 }));
          return 'ok';
        }
      }
      return 'no-blank-spot';
    })()""")
    assert out == "ok", out
    await menu_pick(page, "新建知识点")
    ck.add("菜单能打开新建知识点对话框", bool(await page.ev("!!document.querySelector('.node-dialog')")))

    await page.ev("""(() => {
      const set = (sel, v) => {
        const el = document.querySelector(sel);
        el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
      };
      const inputs = document.querySelectorAll('.node-dialog input[type="text"], .node-dialog input:not([type])');
      inputs[0].value = '控制器';
      inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
      inputs[1].value = '取指、译码、发控制信号';
      inputs[1].dispatchEvent(new Event('input', { bubbles: true }));
      return 'ok';
    })()""")
    await asyncio.sleep(0.3)
    hint = await page.ev("document.querySelector('.node-dialog .hint code')?.textContent || ''")
    ck.add("对话框显示落盘路径", hint.endswith("控制器.md"), hint)
    await click_text(page, ".node-dialog .btn", "创建并放到画布")

    # 落在哪个子目录由对话框的"存到"决定，所以按通配找，别写死路径
    text = await poll_glob(vault, "nodes/**/控制器.md", "name: 控制器", timeout=15)
    ck.add("写出合规的新 md（name/field/desc + 关系段）",
           all(k in text for k in ("name: 控制器", "field:", "desc:", "## 关系")), repr(text[:80]))
    placed = await poll(page, """JSON.stringify(Object.keys(__kg.layout.nodes))""",
                        lambda v: v and "控制器" in v, timeout=12)
    ck.add("新知识点立刻落到画布上", "控制器" in (placed or ""), placed or "没进 layout")
    ck.add("创建后自动接上建立关系对话框",
           bool(await page.ev("!!document.querySelector('.rel-dialog:not(.node-dialog)')")))
    await page.ev("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
    await asyncio.sleep(0.4)


async def case_subgroup(page: Page, ck: Check) -> None:
    """右键一个域 → 在里面新建子簇：层次靠嵌套分组表达。"""
    gid = await poll(page, """document.querySelector('[data-shape="kg-group"]')
      ?.getAttribute('data-cell-id') || ''""", lambda v: bool(v))
    await page.ev("window.prompt = () => '子域'")
    await right_click(page, gid, grab="title")
    await menu_pick(page, "在这里新建子簇")
    await asyncio.sleep(0.8)
    subs = json.loads(await page.ev("""JSON.stringify(Object.entries(__kg.layout.groups)
      .filter(([, g]) => g.parent).map(([id, g]) => [id, g.name, g.parent]))"""))
    ck.add("能在域里建出子簇（parent 指向它）",
           any(x[1] == "子域" and x[2] == gid for x in subs), json.dumps(subs, ensure_ascii=False))
    nested = await poll(page, f"""(() => {{
      const kid = Object.entries(__kg.layout.groups).find(([, g]) => g.parent === '{gid}')?.[0];
      const cell = kid && __kg.graph.getCellById(kid);
      return cell ? (cell.getParent()?.id || '') : '';
    }})()""", lambda v: v == gid, timeout=6)
    ck.add("子簇在画布上真的挂在父簇下", nested == gid, f"父 cell = {nested}")


async def case_group_doc(page: Page, ck: Check, vault: Path) -> None:
    """域的总览文档：点域弹工具条 → 加总览 → 写正文，关系区块不能被碰掉。"""
    await page.ev("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }))")
    await asyncio.sleep(0.8)
    expr = ("[...document.querySelectorAll('[data-shape=\"kg-group\"]')]"
            ".map((e) => e.getAttribute('data-cell-id')).find((id) => id.endsWith('组A')) || ''")
    gid = await poll(page, expr, lambda v: bool(v))
    await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="{gid}"]');
      const r = el.getBoundingClientRect();
      const x = r.x + 40, y = r.y + 12;
      const t = document.elementFromPoint(x, y) || el;
      for (const type of ['mousedown', 'mouseup', 'click']) {{
        t.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true,
          clientX: x, clientY: y, view: window, button: 0 }}));
      }}
      return 'ok';
    }})()""")
    bar = await poll(page, "document.querySelector('.float.groupbar .gb-name')?.textContent || ''",
                     lambda v: bool(v), timeout=6)
    ck.add("点一个域浮出工具条", bool(bar), f"工具条标题「{bar}」")

    # 标题条上常常正好压着一条绕行的线（边的热区有十来像素宽）：点它必须仍然选中这个域
    on_edge = await page.ev(f"""(() => {{
      const el = document.querySelector('[data-cell-id="{gid}"]');
      const r = el.getBoundingClientRect();
      const t = document.elementFromPoint(r.x + 40, r.y + 12);
      return (t && t.closest('[data-cell-id]')?.getAttribute('data-shape')) || '';
    }})()""")
    ck.add("标题条压着连线时也点得开工具条", bool(bar), f"点到的是 {on_edge or '分组本身'}")
    await click_text(page, ".float.groupbar .btn", "加总览文档")
    await asyncio.sleep(0.5)
    ck.add("工具条能开新建总览文档", bool(await page.ev("!!document.querySelector('.node-dialog')")))
    await page.ev("""(() => {
      const ins = document.querySelectorAll('.node-dialog input:not([type=checkbox])');
      ins[1].value = '这个域讲什么';
      ins[1].dispatchEvent(new Event('input', { bubbles: true }));
      const cb = document.querySelector('.node-dialog input[type=checkbox]');
      if (cb.checked) cb.click();          // 别让它接着弹建立关系，挡住后面的断言
      return 'ok';
    })()""")
    await asyncio.sleep(0.3)
    await click_text(page, ".node-dialog .btn", "创建并放到画布")
    doc = await poll(page, f"__kg.layout.groups['{gid}'].doc || ''", lambda v: bool(v), timeout=15)
    ck.add("新建的总览文档自动绑到这个域", bool(doc), f"doc = {doc}")
    # 落点规则：有 fields/ 就放那儿（规范 2 的领域总览），没有就跟着这个域里其他节点走。
    # 测试 vault 没有 fields/，所以这里应该落在 nodes/组A/ 下。
    hit = [p.relative_to(vault).as_posix() for p in vault.glob(f"**/{doc}.md")] if doc else []
    ck.add("总览文档写在约定目录下（fields/ 或域所在目录）",
           any(h.startswith(("fields/", "nodes/")) for h in hit), str(hit))

    # 建完会自动选中并定位到总览文档，检查器就停在它的详情页上
    await poll(page, "document.querySelector('.insp .node-title')?.textContent || ''",
               lambda v: bool(v), timeout=8)
    await click_text(page, ".insp .tabs button", "详情")
    await asyncio.sleep(0.3)
    await click_text(page, ".insp .act-row .btn", "写内容")
    await asyncio.sleep(0.4)
    ck.add("检查器能展开正文编辑框", bool(await page.ev("!!document.querySelector('.write-box textarea')")))
    await page.ev("""(() => {
      const t = document.querySelector('.write-box textarea');
      const NL = String.fromCharCode(10);
      t.value = '# 总览' + NL + NL + '## 描述' + NL + '这个域讲计算机怎么搭起来的。';
      t.dispatchEvent(new Event('input', { bubbles: true }));
      return 'ok';
    })()""")
    await click_text(page, ".write-box .btn", "保存正文")
    saved = await poll_glob(vault, f"**/{doc}.md", "这个域讲计算机怎么搭起来的", timeout=15)
    ck.add("正文写回 md", "## 描述" in saved, repr(saved[-80:]))
    ck.add("写正文不碰关系区块（甲的边还在）",
           "## 关系" in (list(vault.glob("nodes/**/甲.md"))[0].read_text("utf-8")), "")


async def scenarios(page: Page, api: str, results: list) -> None:
    ck = Check(api)
    # 顺序有讲究：默认模式是「对话」，画布只占右边一条，**auto-LOD 会把分组折叠成簇卡片**，
    # 这时数不到 kg-node。所以先关简报、切到全局图，再断言首屏渲染。
    await case_morning_brief(page, ck)
    await case_modes(page, ck)
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
    await case_import_panel(page, ck, VAULT_HOLDER[0])
    await case_due_badge(page, ck)
    await case_project_view(page, ck, api)
    await case_calendar(page, ck)
    await case_chat_view(page, ck, api)
    await case_drag_from_inbox(page, ck, VAULT_HOLDER[0])
    await case_finalize(page, ck)
    await case_image(page, ck, VAULT_HOLDER[0])
    await case_edge_vertices(page, ck)
    await case_wheel_pan(page, ck)
    await case_cluster_drag(page, ck)
    await case_group_menu(page, ck)
    await case_relate_menu(page, ck, VAULT_HOLDER[0])
    await case_minimap(page, ck)
    await case_blank_menu(page, ck)
    await case_create_node(page, ck, VAULT_HOLDER[0])
    await case_subgroup(page, ck)
    await case_group_doc(page, ck, VAULT_HOLDER[0])
    await case_edges_dodge(page, ck)
    await case_stale_index(page, ck, VAULT_HOLDER[0], api)
    await case_form_look(page, ck)
    await case_chat_markdown(page, ck, VAULT_HOLDER[0])
    await case_chat_card_edit(page, ck, VAULT_HOLDER[0])
    await case_history(page, ck, VAULT_HOLDER[0])
    await case_tour(page, ck)
    await case_lineage(page, ck)
    await case_stats(page, ck, VAULT_HOLDER[0])
    await case_compare(page, ck, VAULT_HOLDER[0])
    await case_inbox_new_field(page, ck, VAULT_HOLDER[0])   # 放最后：它往 vault 里加了个零边新领域节点，会改「今日」面板的形状
    # 和上面一条同理，甚至更重：它往 vault 里加两个流派文档，会把两个圆点变成带子、改掉最长演化链 —— 导览和谱系那几条用例全靠那条链
    await case_schools(page, ck, VAULT_HOLDER[0])
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
