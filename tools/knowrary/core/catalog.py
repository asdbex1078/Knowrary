"""谱系目录（2026-09-30 模型目录，2026-10-12 扩成通用谱系）：一个家族一个 md，读成结构化的型号列表。

**型号不是知识节点。** DeepSeek-V3、80386、Linux 0.11 这种具体型号 / 版本混进概念图，会把 MLA、
保护模式这些概念淹没；所以它们单独放在库级可配的目录（`vault.json` 的 `models_dir`）里，只有
frontmatter 写了 `kind: family` 的 md 才算家族文件——和原文放在同一个目录也不会认错。

**一级子目录就是一张谱系图**（2026-10-12 定）：`models/大模型/`、`models/CPU/`、`models/操作系统/`。
子目录里的 `谱系.md`（`kind: lineage`）声明这张图叫什么、型号除了公共字段还登记哪些字段：

    ---
    kind: lineage
    name: CPU
    badge: 位宽                # 写在型号名旁边的那一格
    ---
    ## 字段
    - 制程:: 数值，单位 nm
    - 晶体管:: 数值
    - 位宽:: 文字
    - 指令集:: 可多个

    ## 抽取说明
    （给 learn 看的：这张图特有的抽法，比如大模型的「MoE 总参 / 激活拆开写」）

字段声明写在正文里、用库里一贯的 `- 键:: 值`，不写进 frontmatter 的嵌套映射——项目没把 PyYAML
列为依赖，兜底的 YAML 子集读不了嵌套。声明里认这几个词：`数值`、`可多个`、`单位 X`、`范围 A~B`
（超出范围的数当没填：量级敲错的别画进图里误导人）、`指向 某张图`（**跨图关系**，2026-10-12 第 2 步：
操作系统的 `- 运行在:: 指向 CPU`，版本里写 `- 运行在:: 80386、80486`，连线时去 CPU 那张图里按名字找）。没有 `谱系.md` 的子目录照样是一张图，只是没有登记字段。
**代码里不写死任何一张图**：公共字段（COMMON）之外的一切都来自 `谱系.md`。

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
    - 技术:: [[MLA]]、[[MoE]]、无辅助损失负载均衡

一个型号一节 `## 型号名`，字段沿用库里 `- 键:: 值` 的写法（同 `## 速查`）。
公共字段进固定的键；本图声明过的进 `values`（数值的另给一份进 `numbers`）；都没登记的照收进 `extra`。
一节里不是字段的文字（从知识点挪过来的正文、随手记的几段）原样收进 `notes`，卡片上能展开看——不丢。
这里是纯解析：`[[链接]]` 对不对得上知识点、`前代` 指不指得到别的型号，由上层对着索引解析。
"""
from __future__ import annotations

import re

from .mdio import RE_LINK, split_frontmatter

# 公共字段：每张谱系图都有。键 → 输出字段名，顺序就是卡片上的顺序
COMMON = {"发布": "date", "前代": "prev", "基于": "base", "技术": "tech", "亮点": "highlight",
          "突破": "breakthrough", "论文": "paper"}
LISTS = ("base", "tech")                                   # 可以写多个，用 、，, ; 分开
LINEAGE_FILE = "谱系.md"
UNSORTED = ""                                              # 直接放在目录根上的家族：不属于任何一张图

# 格式样例：没指定目录时页面上给人看的就是它，用例也拿它当最小的真实输入
SAMPLE_LINEAGE = """---
kind: lineage
name: 大模型
badge: 参数
---
一张谱系图一个子目录，这个文件声明这张图登记哪些字段（发布、前代、基于、技术、亮点、突破、论文是公共的，不用写）。

## 字段
- 参数:: 数值，范围 1K~1000T
- 激活:: 数值，范围 1K~1000T
- 上下文:: 数值
- 训练数据:: 数值

## 抽取说明
- MoE 模型「总参 671B、激活 37B」拆成 参数 和 激活 两格。
"""

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
RE_UNIT = re.compile(r"单位\s*[:：]?\s*(?P<unit>[^\s，,、;；]+)")
RE_TARGET = re.compile(r"指向\s*[:：]?\s*(?P<target>[^\s，,、;；]+)")
RE_RANGE = re.compile(r"范围\s*[:：]?\s*(?P<lo>[0-9.]+\s*\S*?)\s*[~～\-到至]\s*(?P<hi>[0-9.]+\s*[^\s，,、;；]*)")


def _kind(text: str) -> str:
    try:
        fm, _ = split_frontmatter(text or "")
    except ValueError:
        return ""
    return str(fm.get("kind") or "").strip() if isinstance(fm, dict) else ""


def is_family(text: str) -> bool:
    return _kind(text) == "family"


def is_lineage(text: str) -> bool:
    return _kind(text) == "lineage"


# ---------------------------------------------------------------- 谱系声明

def parse_lineage(text: str, lid: str, rel: str) -> dict:
    """`谱系.md` → {id, name, path, badge, fields, guide, warnings}。`fields` 是 `[{key, numeric, list, unit, min, max}]`。"""
    fm, body = split_frontmatter(text or "")
    fm = fm if isinstance(fm, dict) else {}
    sections = _sections(body)
    warnings: list[dict] = []
    fields = []
    for key, spec in _items(sections.get("字段", "")):
        if key in COMMON:
            warnings.append({"line": 1, "message": f"「{key}」是公共字段，每张图都有，不用在 谱系.md 里声明"})
            continue
        fields.append(_field_spec(key, spec))
    badge = str(fm.get("badge") or "").strip()
    if badge and badge not in {f["key"] for f in fields}:
        warnings.append({"line": 1, "message": f"badge「{badge}」不是这张图声明过的字段，名字旁边不会写东西"})
        badge = ""
    return {"id": lid, "name": str(fm.get("name") or lid), "path": rel, "badge": badge, "fields": fields,
            "guide": sections.get("抽取说明", "").strip(), "warnings": warnings}


def bare_lineage(lid: str) -> dict:
    """没有 `谱系.md` 的子目录（或目录根）：照样是一张图，只是除了公共字段什么都没登记。"""
    return {"id": lid, "name": lid or "未分谱系", "path": "", "badge": "", "fields": [], "guide": ""}


def _sections(body: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current = None
    for line in (body or "").splitlines():
        head = RE_H2.match(line)
        if head:
            current = head.group("title").strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _items(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        hit = RE_ITEM.match(line) or re.match(r"^\s*[-*]\s+(?P<key>[^:：\s][^:：]*?)\s*$", line)
        if hit:
            out.append((hit.group("key").strip(), (hit.groupdict().get("value") or "").strip()))
    return out


def _field_spec(key: str, spec: str) -> dict:
    unit = RE_UNIT.search(spec)
    rng = RE_RANGE.search(spec)
    target = RE_TARGET.search(spec)
    lo = _first_number(rng.group("lo")) if rng else None
    hi = _first_number(rng.group("hi")) if rng else None
    # 指向别的图的字段天然可以写好几个（一个 OS 版本跑在好几代 CPU 上）
    return {"key": key, "numeric": "数值" in spec and not target, "list": "可多个" in spec or bool(target),
            "unit": unit.group("unit") if unit else "", "min": lo, "max": hi,
            "target": target.group("target") if target else ""}


def _first_number(text: str) -> float | None:
    hit = RE_NUMBER.search(text or "")
    return float(hit.group(1)) * _unit(hit.group(2)) if hit else None


# ---------------------------------------------------------------- 家族文件

def parse_family(text: str, rel: str, fid: str, lineage: dict | None = None) -> dict:
    """一个家族文件 → {id, name, vendor, open, tagline, sources, path, lineage, models, warnings}。

    `lineage` 是这个家族所在那张图的声明（`parse_lineage` / `bare_lineage`），决定哪些字段算登记过的。
    `warnings` 是 `{line, message}`：写错的日期、读不懂的数字、同名的两节。
    """
    lineage = lineage or bare_lineage(UNSORTED)
    text = text or ""
    fm, body = split_frontmatter(text)
    fm = fm if isinstance(fm, dict) else {}
    # 正文从第几行开始：型号那节的行号要对得上文件里的行，check 报错时才指得准
    offset = text.count("\n", 0, text.rfind(body)) if body and body in text else 0
    models, warnings = _parse_models(body, offset, lineage)
    sources = fm.get("sources") or []
    return {"id": fid, "name": str(fm.get("name") or fid), "vendor": str(fm.get("vendor") or ""),
            "open": fm.get("open") if isinstance(fm.get("open"), bool) else None,
            "tagline": str(fm.get("tagline") or ""), "path": rel, "lineage": lineage["id"],
            "sources": [str(s) for s in sources] if isinstance(sources, list) else [str(sources)],
            "models": models, "warnings": warnings}


def _parse_models(body: str, offset: int, lineage: dict) -> tuple[list[dict], list[dict]]:
    models: list[dict] = []
    warnings: list[dict] = []
    seen: set[str] = set()
    current: dict | None = None
    for i, line in enumerate(body.splitlines()):
        head = RE_H2.match(line)
        if head:
            current = {"name": head.group("title").strip(), "line": offset + i + 1, "raw": {}, "notes": []}
            if current["name"] in seen:
                warnings.append({"line": current["line"], "message": f"同一个家族里有两节都叫「{current['name']}」"})
            seen.add(current["name"])
            models.append(current)
            continue
        item = RE_ITEM.match(line)
        if current is not None and item and item.group("value"):
            current["raw"][item.group("key").strip()] = item.group("value")
        elif current is not None:
            current["notes"].append(line)
    out = [_model(m, lineage, warnings) for m in models]
    return out, warnings


def _model(m: dict, lineage: dict, warnings: list[dict]) -> dict:
    """一节的原始键值 → 型号。公共字段进固定的键，本图声明过的进 `values`（数值再进 `numbers`），其它进 `extra`。"""
    raw = m.pop("raw")
    notes = "\n".join(m.pop("notes")).strip("\n")
    declared = {f["key"]: f for f in lineage["fields"]}
    model = {**m, **{f: None for f in COMMON.values()}, "base": [], "tech": [], "values": {}, "numbers": {},
             "refs": {}, "extra": {}, "notes": notes}
    for key, value in raw.items():
        field = COMMON.get(key)
        if field in LISTS:
            model[field] = [v for v in RE_SPLIT.split(value) if v]
        elif field:
            model[field] = value
        elif key in declared:
            model["values"][key] = value
            if declared[key]["target"]:
                model["refs"][key] = [_link_text(v) for v in RE_SPLIT.split(value) if v]
            if declared[key]["numeric"]:
                n = _number(value, declared[key], model, warnings)
                if n is not None:
                    model["numbers"][key] = n
        else:
            model["extra"][key] = value
    model["tech"] = [_tech(t) for t in model["tech"]]
    model["base"] = [_link_text(b) for b in model["base"]]
    model["prev"] = _link_text(model["prev"]) if model["prev"] else None
    _dates(model, warnings)
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


def _number(value, spec: dict, model: dict, warnings: list[dict]) -> float | None:
    """`671B` / `1T MoE` / `128K` / `14.8T tokens` / `1.3万亿` / `65nm` → 数值。

    一格里写了好几个数时**取最大的**：`0.5B / 7B / 72B`（一代好几个尺寸）取旗舰，
    `128K→200K`、`7T → 18T`（这一代扩过）取最新，`1T MoE（32B 激活）` 取总参。
    `未披露`、`未公开` 这类是正经信息（厂商确实没说），不算写错，不报。
    声明了范围的，超出范围当没填（量级敲错了，与其画进图里误导人，不如空着）。
    """
    text = str(value or "").strip()
    if not text or re.search(r"未(披露|公开|明确)|不详|未知", text):
        return None
    hits = RE_NUMBER.findall(text)
    if not hits:
        warnings.append({"line": model["line"], "message": f"「{model['name']}」的{spec['key']}「{text}」里没有数字"})
        return None
    number = max(float(n) * _unit(u) for n, u in hits)
    if (spec.get("min") is not None and number < spec["min"]) or (spec.get("max") is not None and number > spec["max"]):
        return None
    return number


def _unit(unit: str | None) -> float:
    return {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "万": 1e4, "亿": 1e8, "千亿": 1e11,
            "万亿": 1e12}.get((unit or "").lower(), 1.0)


# ---------------------------------------------------------------- 整个目录

def load_catalog(vault) -> dict:
    """读谱系目录：{dir, configured, exists, lineages, families, warnings}。没设目录、目录不在，都返回空的，不报错。

    `lineages` 只列真有家族的图（声明了但还空着的也列，抽取时得能选它）。
    `warnings` 带上文件：`{path, line, message}`。只认 `kind: family` 的 md，别的（原文、笔记）跳过。
    """
    from .mdio import read
    from .vault_config import load_vault_config

    rel = load_vault_config(vault)["models_dir"]
    folder = vault / rel if rel else None
    out = {"dir": rel, "configured": bool(rel), "exists": bool(folder and folder.is_dir()),
           "lineages": [], "families": [], "warnings": []}
    if not out["exists"]:
        return out
    lineages: dict[str, dict] = {}
    files = [p for p in sorted(folder.rglob("*.md"))
             if not any(part.startswith(".") for part in p.relative_to(vault).parts)]
    for path in files:
        lid = _lineage_id(path, folder)
        if path.name == LINEAGE_FILE and path.parent != folder and is_lineage(text := read(path)):
            spec = parse_lineage(text, lid, path.relative_to(vault).as_posix())
            out["warnings"] += [{"path": spec["path"], **w} for w in spec.pop("warnings")]
            lineages[lid] = spec
    taken: set[str] = set()
    for path in files:
        text = read(path)
        if not is_family(text):
            continue
        lid = _lineage_id(path, folder)
        lineage = lineages.setdefault(lid, bare_lineage(lid))
        fid = _family_id(path.stem, taken)
        try:
            family = parse_family(text, path.relative_to(vault).as_posix(), fid, lineage)
        except ValueError as exc:            # frontmatter 坏了：报出来，别让整个目录读不出
            out["warnings"].append({"path": path.relative_to(vault).as_posix(), "line": 1,
                                    "message": f"frontmatter 读不出来：{exc}"})
            continue
        out["warnings"] += [{"path": family["path"], **w} for w in family.pop("warnings")]
        out["families"].append(family)
    if any(f["lineage"] == UNSORTED for f in out["families"]) and len(lineages) > 1:
        out["warnings"].append({"path": rel, "line": 0, "message": "有家族文件直接放在目录根上：挪进一张谱系图的子目录里"})
    out["lineages"] = sorted(lineages.values(), key=lambda x: (x["id"] == UNSORTED, x["id"]))
    return out


def _lineage_id(path, folder) -> str:
    parts = path.relative_to(folder).parts
    return parts[0] if len(parts) > 1 else UNSORTED


def _family_id(stem: str, taken: set[str]) -> str:
    """家族 id = 文件名；两张图里各有一个 `Intel.md` 时后来的加序号，型号 id（家族/型号名）才不撞。"""
    fid, n = stem, 2
    while fid in taken:
        fid, n = f"{stem}-{n}", n + 1
    taken.add(fid)
    return fid


def link_models(catalog: dict) -> list[dict]:
    """型号之间的线：同家族按时间顺序的「走向」、`前代`、`基于`（可以跨家族）、跨图关系字段（`ref`，带字段名）。

    `前代` / `基于` 按型号名找，全目录里名字唯一才认；找不到、有重名都记进 catalog 的 warnings。
    跨图关系只在它指向的那张图里找（`运行在:: 80386` 只找 CPU 图），那张图不在、名字对不上都报。
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
    return links + _refs(catalog)


def _refs(catalog: dict) -> list[dict]:
    """跨图关系的线：source 是被指向的那个（80386），target 是写了这个字段的（Linux 0.11），和「基于」同向。"""
    specs = {x["id"]: x for x in catalog.get("lineages") or []}
    out = []
    for fam in catalog["families"]:
        fields = {f["key"]: f for f in specs.get(fam["lineage"], {}).get("fields", []) if f.get("target")}
        for m in fam["models"]:
            for key, names in (m.get("refs") or {}).items():
                home = _lineage_by(specs, fields[key]["target"]) if key in fields else None
                if home is None:
                    catalog["warnings"].append({"path": fam["path"], "line": m["line"],
                                                "message": f"「{m['name']}」的{key}指向「{fields[key]['target']}」，"
                                                           "谱系目录里没有这张图"})
                    continue
                for name in names:
                    hit = _find_in(catalog, home, name)
                    if hit:
                        out.append({"source": hit, "target": _mid(fam, m), "kind": "ref", "label": key})
                    else:
                        catalog["warnings"].append({"path": fam["path"], "line": m["line"],
                                                    "message": f"「{m['name']}」的{key}「{name}」在「{home}」那张图里对不上"})
    return out


def _lineage_by(specs: dict, target: str) -> str | None:
    """`指向 CPU` 认图的 id（子目录名）也认 `谱系.md` 里写的名字。"""
    if target in specs:
        return target
    return next((lid for lid, x in specs.items() if x["name"] == target), None)


def _find_in(catalog: dict, lineage: str, name: str) -> str | None:
    hits = [(f, m) for f in catalog["families"] if f["lineage"] == lineage
            for m in f["models"] if m["name"].lower() == name.lower()]
    return _mid(*hits[0]) if len(hits) == 1 else None


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
