"""模型目录（2026-09-30）：一个家族一个 md，读成结构化的型号列表，给「模型谱系」和参数量面板用。

**型号不是知识节点。** DeepSeek-V3、GPT-4o 这种具体型号混进概念图，会把 MLA、MoE 这些概念淹没；
所以型号单独放在库级可配的模型目录（`vault.json` 的 `models_dir`）里，只有 frontmatter 写了
`kind: family` 的 md 才算家族文件——和原文放在同一个目录也不会认错。

家族文件长这样：

    ---
    kind: family
    name: DeepSeek
    vendor: 深度求索
    open: true
    tagline: 极致 MoE 稀疏化与推理效率
    sources: ["[[articles/llm/deepseek/DeepSeek开源模型迭代史]]"]
    ---
    ## DeepSeek-V3
    - 发布:: 2024-12-26
    - 参数:: 671B
    - 激活:: 37B
    - 技术:: [[MLA]]、[[MoE]]、无辅助损失负载均衡

一个型号一节 `## 型号名`，字段沿用库里 `- 键:: 值` 的写法（同 `## 速查`）。
登记过的字段驱动画图（FIELDS）；没登记的照收，放进 `extra`，悬停卡片底部列出来。
这里是纯解析：`[[链接]]` 对不对得上知识点、`前代` 指不指得到别的型号，由上层对着索引解析。
"""
from __future__ import annotations

import re

from .mdio import RE_LINK, split_frontmatter
from .parser import parse_params

# 登记字段：键 → 输出字段名。顺序就是卡片上的顺序。
# 上下文、训练数据和参数量一样是核心对比项（2026-09-30 定），不能埋进「其它」
FIELDS = {"发布": "date", "参数": "params", "激活": "active", "上下文": "context", "训练数据": "tokens",
          "前代": "prev", "基于": "base", "技术": "tech", "亮点": "highlight", "突破": "breakthrough",
          "论文": "paper"}
NUMERIC = ("params", "active", "context", "tokens")      # 这几项另给一个 `<字段>_n`（数值），画图、排序用
LISTS = ("base", "tech")                                   # 可以写多个，用 、，, ; 分开

# 格式样例：没指定目录时页面上给人看的就是它，用例也拿它当最小的真实输入
SAMPLE = """---
kind: family
name: DeepSeek
vendor: 深度求索
open: true
tagline: 极致 MoE 稀疏化与推理效率
sources: ["[[DeepSeek开源模型迭代史]]"]
---
一个家族一个文件，一个型号一节。字段都是可选的，没填的格子就空着。

## DeepSeek-V2
- 发布:: 2024-05
- 参数:: 236B
- 激活:: 21B
- 上下文:: 128K
- 技术:: [[MLA]]、[[MoE]]
- 突破:: 低秩压缩 KV 缓存，推理成本大降

## DeepSeek-V3
- 发布:: 2024-12-26
- 参数:: 671B
- 激活:: 37B
- 上下文:: 128K
- 训练数据:: 14.8T tokens
- 前代:: DeepSeek-V2
- 技术:: [[MLA]]、[[MoE]]、无辅助损失负载均衡、[[MTP]]、FP8 混合精度
- 亮点:: 训练开销约 278.8 万 H800 GPU 小时
- 突破:: 用很低的训练成本做到接近闭源一线的水平
- 论文:: https://arxiv.org/abs/2412.19437

## DeepSeek-R1
- 发布:: 2025-01-20
- 基于:: DeepSeek-V3
- 技术:: 纯强化学习激发推理（GRPO）
- 突破:: 开源推理模型追平 o1
"""

RE_ITEM = re.compile(r"^\s*[-*]\s+(?P<key>[^:：]+?)\s*::\s*(?P<value>.*?)\s*$")
RE_H2 = re.compile(r"^##\s+(?P<title>.+?)\s*#*\s*$")
RE_SPLIT = re.compile(r"\s*[、，,;；]\s*")
RE_DATE = re.compile(r"^(?P<y>(?:19|20)\d{2})(?:\s*[-./年]\s*(?P<m>\d{1,2})(?:\s*[-./月]\s*(?P<d>\d{1,2}))?)?")
RE_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(万亿|千亿|万|亿|[kmbt])?", re.I)


def is_family(text: str) -> bool:
    try:
        fm, _ = split_frontmatter(text or "")
    except ValueError:
        return False
    return isinstance(fm, dict) and str(fm.get("kind") or "").strip() == "family"


def parse_family(text: str, rel: str, fid: str) -> dict:
    """一个家族文件 → {id, name, vendor, open, tagline, sources, path, models, warnings}。

    `warnings` 是 `{line, message}`：写错的日期、读不懂的数字、同名的两节。
    """
    text = text or ""
    fm, body = split_frontmatter(text)
    fm = fm if isinstance(fm, dict) else {}
    # 正文从第几行开始：型号那节的行号要对得上文件里的行，check 报错时才指得准
    offset = text.count("\n", 0, text.rfind(body)) if body and body in text else 0
    models, warnings = _parse_models(body, offset)
    sources = fm.get("sources") or []
    return {"id": fid, "name": str(fm.get("name") or fid), "vendor": str(fm.get("vendor") or ""),
            "open": fm.get("open") if isinstance(fm.get("open"), bool) else None,
            "tagline": str(fm.get("tagline") or ""), "path": rel,
            "sources": [str(s) for s in sources] if isinstance(sources, list) else [str(sources)],
            "models": models, "warnings": warnings}


def _parse_models(body: str, offset: int) -> tuple[list[dict], list[dict]]:
    models: list[dict] = []
    warnings: list[dict] = []
    seen: set[str] = set()
    current: dict | None = None
    for i, line in enumerate(body.splitlines()):
        head = RE_H2.match(line)
        if head:
            current = {"name": head.group("title").strip(), "line": offset + i + 1, "raw": {}}
            if current["name"] in seen:
                warnings.append({"line": current["line"], "message": f"同一个家族里有两节都叫「{current['name']}」"})
            seen.add(current["name"])
            models.append(current)
            continue
        item = RE_ITEM.match(line)
        if current is not None and item and item.group("value"):
            current["raw"][item.group("key").strip()] = item.group("value")
    out = [_model(m, warnings) for m in models]
    return out, warnings


def _model(m: dict, warnings: list[dict]) -> dict:
    """一节的原始键值 → 型号。登记过的按 FIELDS 取，数值字段再给一份 `_n`，其它进 `extra`。"""
    raw = m.pop("raw")
    model = {**m, **{f: None for f in FIELDS.values()}, "base": [], "tech": [], "extra": {}}
    for key, value in raw.items():
        field = FIELDS.get(key)
        if field is None:
            model["extra"][key] = value
        elif field in LISTS:
            model[field] = [v for v in RE_SPLIT.split(value) if v]
        else:
            model[field] = value
    model["tech"] = [_tech(t) for t in model["tech"]]
    model["base"] = [_link_text(b) for b in model["base"]]
    model["prev"] = _link_text(model["prev"]) if model["prev"] else None
    _dates(model, warnings)
    for field in NUMERIC:
        model[f"{field}_n"] = _number(model[field], field, model, warnings)
    return model


def _link_text(value: str) -> str:
    """`[[DeepSeek-V2]]` 和 `DeepSeek-V2` 都认成名字。"""
    hit = RE_LINK.search(value or "")
    return (hit.group(1) if hit else value or "").strip()


def _tech(value: str) -> dict:
    """技术点：写成 `[[MLA]]` 的记下链接目标（上层再对知识点），纯文字的只是文字。"""
    hit = RE_LINK.search(value)
    text = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]", lambda x: x.group(2) or x.group(1), value)
    return {"text": text.strip(), "link": hit.group(1).strip() if hit else None}


def _dates(model: dict, warnings: list[dict]) -> None:
    """`2024-12-26` / `2024.12` / `2024年12月26日` / `2024` → year / month / day + 排序用的 `t`（小数年份）。"""
    model.update({"year": None, "month": None, "day": None, "t": None})
    raw = str(model["date"] or "").strip()
    if not raw:
        return
    hit = RE_DATE.match(raw)
    month = int(hit.group("m")) if hit and hit.group("m") else None
    day = int(hit.group("d")) if hit and hit.group("d") else None
    if not hit or (month and not 1 <= month <= 12) or (day and not 1 <= day <= 31):
        warnings.append({"line": model["line"], "message": f"「{model['name']}」的发布时间「{raw}」读不懂，"
                                                           "写成 2024-12-26 / 2024-12 / 2024"})
        return
    year = int(hit.group("y"))
    model.update({"year": year, "month": month, "day": day,
                  "t": round(year + ((month or 1) - 1) / 12 + ((day or 1) - 1) / 365, 4)})


def _number(value, field: str, model: dict, warnings: list[dict]) -> float | None:
    """`671B` / `1T MoE` / `128K` / `14.8T tokens` / `1.3万亿` → 数值。

    一格里写了好几个数时**取最大的**：`0.5B / 7B / 72B`（一代好几个尺寸）取旗舰，
    `128K→200K`、`7T → 18T`（这一代扩过）取最新，`1T MoE（32B 激活）` 取总参。
    `未披露`、`未公开` 这类是正经信息（厂商确实没说），不算写错，不报。
    """
    text = str(value or "").strip()
    if not text or re.search(r"未(披露|公开|明确)|不详|未知", text):
        return None
    hits = RE_NUMBER.findall(text)
    if not hits:
        warnings.append({"line": model["line"], "message": f"「{model['name']}」的{_label(field)}「{text}」里没有数字"})
        return None
    number = max(float(n) * _unit(u) for n, u in hits)
    if field in ("params", "active"):
        return parse_params(number)          # 同一条合理区间：量级敲错的当没填
    return number


def _unit(unit: str | None) -> float:
    return {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "万": 1e4, "亿": 1e8, "千亿": 1e11,
            "万亿": 1e12}.get((unit or "").lower(), 1.0)


def _label(field: str) -> str:
    return next(k for k, v in FIELDS.items() if v == field)


def load_catalog(vault) -> dict:
    """读模型目录：{dir, configured, families, warnings}。没设目录、目录不在，都返回空的，不报错。

    `warnings` 带上文件：`{path, line, message}`。只认 `kind: family` 的 md，别的（原文、笔记）跳过。
    """
    from .mdio import read
    from .vault_config import load_vault_config

    rel = load_vault_config(vault)["models_dir"]
    folder = vault / rel if rel else None
    out = {"dir": rel, "configured": bool(rel), "exists": bool(folder and folder.is_dir()),
           "families": [], "warnings": []}
    if not out["exists"]:
        return out
    for path in sorted(folder.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault).parts):
            continue
        text = read(path)
        if not is_family(text):
            continue
        try:
            family = parse_family(text, path.relative_to(vault).as_posix(), path.stem)
        except ValueError as exc:            # frontmatter 坏了：报出来，别让整个目录读不出
            out["warnings"].append({"path": path.relative_to(vault).as_posix(), "line": 1,
                                    "message": f"frontmatter 读不出来：{exc}"})
            continue
        out["warnings"] += [{"path": family["path"], **w} for w in family.pop("warnings")]
        out["families"].append(family)
    return out


def link_models(catalog: dict) -> list[dict]:
    """型号之间的线：同家族按时间顺序的「走向」、`前代`、`基于`（可以跨家族）。

    `前代` / `基于` 按型号名找，全目录里名字唯一才认；找不到、有重名都记进 catalog 的 warnings。
    同家族的走向线只在没写 `前代` 时补（按发布时间相邻连），写了就以人写的为准。
    """
    by_name: dict[str, list[tuple[dict, dict]]] = {}
    for fam in catalog["families"]:
        for m in fam["models"]:
            by_name.setdefault(m["name"], []).append((fam, m))
    links: list[dict] = []
    for fam in catalog["families"]:
        for m in fam["models"]:
            for kind, names in (("prev", [m["prev"]] if m["prev"] else []), ("base", m["base"])):
                for name in names:
                    hit = _find(by_name, name, fam, m, catalog)
                    if hit:
                        links.append({"source": hit, "target": _mid(fam, m), "kind": kind})
        links += _succession(fam)
    return links


def _find(by_name: dict, name: str, fam: dict, m: dict, catalog: dict) -> str | None:
    hits = by_name.get(name) or []
    if len(hits) == 1:
        return _mid(*hits[0])
    why = "目录里没有这个型号" if not hits else "目录里有好几个叫这个名字的型号"
    catalog["warnings"].append({"path": fam["path"], "line": m["line"],
                                "message": f"「{m['name']}」指向的「{name}」对不上：{why}"})
    return None


def _succession(fam: dict) -> list[dict]:
    """同家族按时间排，没写 `前代` 的型号接到它前一个上（虚的「走向」线，人没写过）。"""
    dated = sorted((m for m in fam["models"] if m["t"] is not None), key=lambda m: m["t"])
    return [{"source": _mid(fam, a), "target": _mid(fam, b), "kind": "next"}
            for a, b in zip(dated, dated[1:]) if not b["prev"]]


def _mid(fam: dict, m: dict) -> str:
    return f"{fam['id']}/{m['name']}"
