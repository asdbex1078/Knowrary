#!/usr/bin/env python3
"""模型谱系视图的端到端自测：真无头 Chrome，不调模型。

    .venv/bin/python web/tests/e2e_model_lineage.py

2026-09-30（模型谱系第 3 步）：谱系视图顶上切「模型谱系」→ 一个家族一条泳道、按发布顺序等距、
参数写在名字旁 → 悬停出卡片、点一下钉住 → 技术点点过去是图上的知识点，检查器里反查「用到它的型号」点回来
→ 开源 / 闭源、收起家族、按技术点高亮 → 换目录走引导页。
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
sys.path.insert(0, str(REPO / "tools" / "knowrary"))

import e2e_canvas as E  # noqa: E402
import core  # noqa: E402

DS = core.CATALOG_SAMPLE.replace("[[MLA]]、[[MoE]]、无辅助", "[[甲]]、[[MoE]]、无辅助")
GPT = """---
kind: family
name: GPT
open: false
---
## GPT-3
- 发布:: 2020-06
- 参数:: 175B

## GPT-4
- 发布:: 2023-03
- 技术:: [[MoE]]
"""
CPU_LINEAGE = """---
kind: lineage
name: CPU
badge: 位宽
---
## 字段
- 制程:: 数值，单位 nm
- 位宽
"""
INTEL = """---
kind: family
name: Intel
---
## 80386
- 发布:: 1985
- 制程:: 1500nm
- 位宽:: 32 位
- 技术:: [[MoE]]
"""
OS_LINEAGE = """---
kind: lineage
name: 操作系统
---
## 字段
- 运行在:: 指向 CPU
"""
LINUX = """---
kind: family
name: Linux
---
## Linux 0.11
- 发布:: 1991-12
- 运行在:: 80386
"""
TEXT = "(sel) => (document.querySelector(sel)?.textContent || '').replace(/\\s+/g, '')"


async def text(page, sel: str) -> str:
    return await page.ev(f"({TEXT})({json.dumps(sel)})") or ""


async def hover(page, name: str) -> str:
    await page.ev(f"document.querySelector('[data-model={json.dumps(name)}]').dispatchEvent(new MouseEvent('mouseenter'))")
    return await E.poll(page, f"({TEXT})('[data-model-card]')", lambda v: name in (v or ""), timeout=4) or ""


async def case_view(page, ck) -> None:
    await E.history_view(page, "models")
    tabs = await E.poll(page, f"({TEXT})('[data-lineages]')", bool, timeout=8)
    ck.add("一级子目录一张图：顶上切换", tabs == "CPU大模型操作系统", str(tabs))
    await page.ev("document.querySelector('[data-lineages] [data-lineage=\"CPU\"]').click()")
    await E.poll(page, "document.querySelectorAll('[data-model-lineage] .ml-item').length", lambda v: (v or 0) == 1, timeout=8)
    labels = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('.ml-label')].map((t) => t.textContent))"))
    ck.add("CPU 图：名字旁写 谱系.md 的 badge（位宽）", labels == ["80386 · 32 位"], str(labels))
    card = await hover(page, "80386")
    ck.add("CPU 卡片列 谱系.md 声明的字段、没有开源筛选", "制程1500nm" in card and "位宽32位" in card
           and not await page.ev("!!document.querySelector('[data-open]')"), card[:160])
    await page.ev("document.querySelector('[data-model=\"80386\"]').dispatchEvent(new MouseEvent('mouseleave'))")
    await page.ev("document.querySelector('[data-lineages] [data-lineage=\"大模型\"]').click()")
    await E.poll(page, "document.querySelectorAll('[data-model-lineage] .ml-item').length", lambda v: (v or 0) == 5, timeout=8)
    names = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('.ml-name b')].map((b) => b.textContent))"))
    ck.add("一个家族一条泳道，按首发时间排", names == ["GPT", "DeepSeek"], str(names))
    labels = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('.ml-label')].map((t) => t.textContent))"))
    ck.add("参数写在名字旁", "DeepSeek-V3 · 671B" in labels and "GPT-3 · 175B" in labels, str(labels))
    card = await hover(page, "DeepSeek-V3")
    ck.add("悬停出卡片：时间、总参 / 激活、上下文、训练数据、技术点、亮点、突破、论文",
           all(x in card for x in ("2024-12-26", "参数671B", "激活37B", "128K", "14.8Ttokens", "无辅助损失负载均衡",
                                   "278.8万H800", "接近闭源一线", "arxiv.org")), card[:260])
    await page.ev("document.querySelector('[data-model=\"DeepSeek-V3\"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))")
    await page.ev("document.querySelector('[data-model=\"DeepSeek-V3\"]').dispatchEvent(new MouseEvent('mouseleave'))")
    ck.add("点一下钉住：移开鼠标卡片还在", await page.ev("!!document.querySelector('[data-model-card].pinned')"), "")


async def case_overlay(page, ck) -> None:
    """第 2 步：打开叠加、点图名加进来；两张以上换真实年份轴，跨图关系（运行在）画虚线。"""
    await page.ev("document.querySelector('[data-act=\"ml-overlay\"]').click()")
    for lid in ("CPU", "操作系统"):
        await page.ev(f"document.querySelector('[data-lineages] [data-lineage=\"{lid}\"]').click()")
    await E.poll(page, "document.querySelectorAll('[data-model-lineage] .ml-item').length", lambda v: (v or 0) == 7, timeout=6)
    heads = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('[data-head]')].map((t) => t.textContent))"))
    ck.add("叠加三张：每张图一行图名", heads == ["CPU", "大模型", "操作系统"], str(heads))
    years = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('[data-ml-axis] .ml-year')].map((t) => t.textContent))"))
    ck.add("顶上一条共用的年份轴，从最早的 1985 起", years[:2] == ["1985", "1986"], str(years[:4]))
    xs = json.loads(await page.ev("""JSON.stringify(Object.fromEntries([...document.querySelectorAll('.ml-item')]
      .map((g) => [g.dataset.model, +g.querySelector('circle').getAttribute('cx')])))"""))
    ck.add("按真实时间对齐：Linux 0.11（1991）在 80386（1985）右边、GPT-3（2020）更右",
           xs["80386"] < xs["Linux 0.11"] < xs["GPT-3"], str({k: xs[k] for k in ("80386", "Linux 0.11", "GPT-3")}))
    ref = await page.ev("document.querySelectorAll('.ml-link.ref').length")
    ck.add("跨图关系「运行在」画成虚线", ref == 1, str(ref))
    await page.ev("document.querySelector('[data-act=\"ml-overlay\"]').click()")
    await page.ev("document.querySelector('[data-lineages] [data-lineage=\"大模型\"]').click()")
    await E.poll(page, "document.querySelectorAll('[data-model-lineage] .ml-item').length", lambda v: (v or 0) == 5, timeout=6)
    ck.add("关掉叠加回到单张、等距", not await page.ev("!!document.querySelector('[data-ml-axis]')"), "")


async def case_entry(page, ck) -> None:
    """第 4 步（2026-10-12）：历史入口合一。顶栏只有一个「历史」，里面三档；从别处回来进的是上次那一档。"""
    tabs = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('[data-history-view]')].map((b) => b.textContent.trim()))"))
    ck.add("历史里顶上三档：时间线 / 演化树 / 谱系图", tabs == ["时间线", "演化树", "谱系图"], str(tabs))
    await E.switch_mode(page, "全局图")
    ck.add("顶栏没有单独的「谱系」按钮了",
           not await page.ev("[...document.querySelectorAll('.topbar .seg button')].some((b) => b.textContent.trim() === '谱系')"), "")
    await E.switch_mode(page, "历史")
    back = await E.poll(page, "document.querySelector('[data-history-view].on')?.dataset.historyView || ''",
                        lambda v: v == "models", timeout=6)
    ck.add("从全局图点「历史」回到上次看的谱系图", back == "models" and
           await page.ev("!!document.querySelector('[data-model-lineage]')"), str(back))


async def case_jump(page, ck) -> None:
    await page.ev("[...document.querySelectorAll('[data-model-card] [data-act=\"goto-tech\"]')].find((b) => b.textContent === '甲').click()")
    title = await E.poll(page, "document.querySelector('.insp .node-title')?.textContent || ''", lambda v: "甲" in (v or ""), timeout=8)
    ck.add("技术点点过去：切回全局图、选中那个知识点", "甲" in (title or ""), str(title))
    using = await E.poll(page, f"({TEXT})('.insp [data-models-using]')", bool, timeout=6)
    ck.add("检查器里反查「用到它的型号」（只有 V3 的技术点链着甲）", using == "DeepSeek-V3", str(using))
    await page.ev("document.querySelector('.insp [data-act=\"show-model\"]').click()")
    card = await E.poll(page, f"({TEXT})('[data-model-card].pinned')", lambda v: "DeepSeek-V3" in (v or ""), timeout=8)
    ck.add("点回来：进模型谱系、钉住那个型号", "DeepSeek-V3" in (card or ""), str(card)[:80])


async def case_filters(page, ck) -> None:
    await page.ev("document.querySelector('[data-open=\"closed\"]').click()")
    await asyncio.sleep(0.3)
    names = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('.ml-name b')].map((b) => b.textContent))"))
    ck.add("只看闭源", names == ["GPT"], str(names))
    await page.ev("document.querySelector('[data-open=\"all\"]').click()")
    await page.ev("""(() => { const s = document.querySelector('[data-input="ml-tech"]');
      s.value = 'node:MoE'; s.dispatchEvent(new Event('change')); })()""")
    await asyncio.sleep(0.3)
    lit = json.loads(await page.ev("JSON.stringify([...document.querySelectorAll('.ml-item:not(.dim)')].map((g) => g.dataset.model))"))
    ck.add("按技术点高亮：用了 MoE 的亮（跨家族、只在这张图里），其它变淡",
           sorted(lit) == ["DeepSeek-V2", "DeepSeek-V3", "GPT-4"], str(lit))
    await page.ev("""(() => { const s = document.querySelector('[data-input="ml-tech"]');
      s.value = ''; s.dispatchEvent(new Event('change')); })()""")
    await page.ev("document.querySelector('.ml-name[data-family=\"GPT\"]').click()")
    await asyncio.sleep(0.3)
    ck.add("点家族名收起这条泳道", await page.ev("document.querySelectorAll('.ml-item').length") == 3
           and await page.ev("document.querySelector('.ml-name[data-family=\"GPT\"]').classList.contains('folded')"), "")
    base = await page.ev("document.querySelectorAll('.ml-link.base').length")
    ck.add("跨家族的「基于」画虚线（R1 基于 V3）", base == 1, str(base))


async def case_move(page, ck, vault: Path) -> None:
    """知识点其实是个型号：检查器「这是型号…」→ 先看影响面 → 确认挪过去，点删掉、进模型谱系钉住它。"""
    await E.switch_mode(page, "全局图")
    await page.ev("""(() => { const i = document.querySelector('.search input');
      i.value = 'GPT-5'; i.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    await E.poll(page, "document.querySelectorAll('.search .hits li').length", lambda v: (v or 0) > 0)
    await page.ev("document.querySelector('.search .hits li').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))")
    await E.poll(page, "document.querySelector('.insp .node-title')?.textContent || ''", lambda v: "GPT-5" in (v or ""), timeout=8)
    await E.poll(page, "!!document.querySelector('.insp [data-act=\"to-model\"]')", bool, timeout=6)   # 详情读回来才有这一行
    await page.ev("document.querySelector('.insp [data-act=\"to-model\"]').click()")
    plan = await E.poll(page, f"({TEXT})('[data-model-move-plan]')", bool, timeout=6)
    ck.add("挪之前先看影响面：进哪个家族、补哪几格、正文当笔记、从画布上拿掉",
           "models/大模型/GPT.md" in (plan or "") and "新增这个型号" in (plan or "") and "+发布：2025" in (plan or "")
           and "笔记" in (plan or "") and "画布上拿掉" in (plan or ""), str(plan))
    ck.add("家族按名字猜（GPT-5 → GPT）", await page.ev("document.querySelector('[data-input=\"model-family\"]').value") == "GPT", "")
    await page.ev("document.querySelector('[data-act=\"model-move-apply\"]').click()")
    card = await E.poll(page, f"({TEXT})('[data-model-card].pinned')", lambda v: "GPT-5" in (v or ""), timeout=10)
    ck.add("挪完进模型谱系、钉住它，正文在笔记里", "GPT-5" in (card or "") and "笔记" in (card or ""), str(card)[:120])
    ck.add("节点文件删掉了（写前有快照）", not (vault / "nodes/组A/GPT-5.md").exists()
           and any((vault / ".knowrary/backup").glob("to-model-*")), "")


async def case_dir(page, ck, vault: Path) -> None:
    await page.ev("document.querySelector('[data-act=\"ml-change-dir\"]').click()")
    guide = await E.poll(page, "!!document.querySelector('[data-ml-guide] [data-models-dir]')", bool, timeout=4)
    ck.add("换目录走引导页（带候选和格式样例）", bool(guide), "")
    await page.ev("""(() => { const i = document.querySelector('[data-input="models-dir"]');
      i.value = 'lab'; i.dispatchEvent(new Event('input')); })()""")
    await page.ev("document.querySelector('[data-act=\"models-dir-save\"]').click()")
    empty = await E.poll(page, f"({TEXT})('[data-ml-guide]')", lambda v: "lab/" in (v or ""), timeout=6)
    ck.add("换到一个空目录：说清里面还没有家族文件", "还没有家族文件" in (empty or ""), str(empty)[:120])
    ck.add("换目录存进了 vault.json、没搬文件", json.loads((vault / ".knowrary/vault.json").read_text("utf-8"))["models_dir"] == "lab"
           and (vault / "models/大模型/DeepSeek.md").exists(), "")


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
            for case in (case_view, case_overlay, case_jump, case_filters, case_entry):
                await case(page, ck)
            await case_move(page, ck, vault)
            await case_dir(page, ck, vault)
        except Exception as exc:        # 断言炸了也把已经收集的结论打出来
            ck.add("流程跑完", False, f"{type(exc).__name__}: {exc}")
        results.extend(ck.items)


def main() -> None:
    if not (REPO / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist：先 cd web && npm run build")
    port = E.free_port()
    api = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="knowrary-e2e-lineage-") as tmpdir:
        tmp = Path(tmpdir)
        vault = E.make_vault(tmp)
        (vault / "nodes/组A/MoE.md").write_text("---\nname: MoE\nfield: 测试\ndesc: 混合专家\n---\n# MoE\n\n正文\n", "utf-8")
        for rel, body in (("models/大模型/DeepSeek.md", DS), ("models/大模型/GPT.md", GPT),
                          ("models/大模型/谱系.md", core.CATALOG_SAMPLE_LINEAGE),
                          ("models/CPU/谱系.md", CPU_LINEAGE), ("models/CPU/Intel.md", INTEL),
                          ("models/操作系统/谱系.md", OS_LINEAGE), ("models/操作系统/Linux.md", LINUX)):
            (vault / rel).parent.mkdir(parents=True, exist_ok=True)
            (vault / rel).write_text(body, "utf-8")
        (vault / "lab").mkdir()
        (vault / "nodes/组A/GPT-5.md").write_text("---\nname: GPT-5\nfield: 测试\nyear: 2025\ndesc: 内建思考\n---\n"
                                                "# GPT-5\n\n## 为什么重要\n不再手动切换推理模型。\n", "utf-8")
        (vault / ".knowrary/vault.json").write_text(json.dumps({"schema_version": 1, "models_dir": "models"}), "utf-8")
        env = {**os.environ, "KNOWRARY_VAULT": str(vault), "KNOWRARY_HOME": str(tmp / "home"),
               "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
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
