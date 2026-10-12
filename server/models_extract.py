"""把原文抽成模型目录（2026-09-30，模型谱系第 2 步）。

三段，前端一段一段地发（每段都要看得见在干什么）：

1. `propose`：learn 读一篇文章，吐出家族和型号；和目录里已有的**合并成计划**——新型号整条新增，
   已有型号**只补空着的格子**，和目录里写的不一样的格子列成冲突、不改。
   同时做**确定性核对**：发布和这张图声明成「数值」的格子（大模型的参数 / 激活 / 上下文 / 训练数据，
   CPU 的制程 / 晶体管…）里的数字必须在原文里原样出现，对不上的标可疑。
2. `verify`：只有可疑的格子才让 review 逐格判一次（ok / 写错了 / 拿不准，带原文引句）。全对得上就不调模型。
3. `apply`：人勾掉不要的型号之后写入。按目标文件分组，预览之后文件被改过就拒（409），
   写前把要动的家族文件整份快照；写入时对着**当时的文件**重新合并一遍，只补不覆盖。

**抽进哪张谱系图要说清楚**（2026-10-12 起一级子目录一张图）：要抽哪些字段、哪几格核数字、
这张图特有的抽法（`谱系.md` 的「抽取说明」），都从那张图的声明来，提示词本身不写死任何一张图。

家族文件的格式见 `core.catalog`。这里只管「文章 → 计划 → 文件」。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import shutil
from pathlib import Path

from . import articles as articles_svc
from .contracts import ModelsApplied, ModelsApply, ModelsProposal, ModelsVerified
from .index_service import current_index
from .llm_call import ask_meta, parse_json
from .paths import core

# 模型吐的 JSON 键 → 家族文件里的中文键（公共字段，反查 core.catalog.COMMON，顺序即写入顺序）。
# 各图自己声明的字段，模型直接用中文键写在 `values` 里
KEYS = {v: k for k, v in core.CATALOG_FIELDS.items()}
MAX_NODES = 400
RE_NUM = re.compile(r"\d+(?:\.\d+)?")
RE_SAFE = re.compile(r"[\\/:*?\"<>|\s]+")
RE_FIELD_LINE = re.compile(r"^\s*[-*]\s+[^:：]+?\s*::")


class ModelsExtractRejected(ValueError):
    """这次抽取 / 写入不成立：没指定目录、文章读不了、模型什么都没抽出来。"""


class ModelsStale(ModelsExtractRejected):
    """预览之后家族文件被改过：让人重新抽一次，不在过期的计划上写。"""


def _prompt(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "tools" / "knowrary" / "prompts" / name).read_text("utf-8")


# ---------------------------------------------------------------- 1. 抽 + 合并成计划

def propose(vault: Path, path: str, lineage: str | None = None) -> ModelsProposal:
    catalog = _catalog(vault)
    spec = pick_lineage(catalog, lineage)
    try:
        art = articles_svc.read_article(vault, path)
    except articles_svc.ArticleRejected as exc:
        raise ModelsExtractRejected(str(exc)) from exc
    prompt = (_prompt("models.md").replace("{{lineage}}", spec["name"]).replace("{{fields}}", _describe_fields(spec, catalog))
              .replace("{{guide}}", spec["guide"] or "（这张图没有额外的抽取说明）")
              .replace("{{existing}}", _describe_existing(catalog, spec["id"]))
              .replace("{{nodes}}", _describe_nodes(vault)).replace("{{article}}", art.text))
    raw, meta = ask_meta(vault, "learn", prompt, op="models-extract")
    data = parse_json(raw, "models-extract", vault=vault) or {}
    numbers = _numbers(art.text)
    families = [_plan_family(vault, f, catalog, numbers, spec) for f in (data.get("families") or [])
                if isinstance(f, dict) and str(f.get("name") or "").strip()]
    families = [{**f, "sources": [_source_link(art.path)]} for f in families if f["models"]]
    if not families:
        raise ModelsExtractRejected(f"《{art.title}》里没抽出任何「{spec['name']}」的型号（它可能不是讲这条线迭代的文章）")
    return ModelsProposal(path=art.path, lineage=spec["id"], title=art.title, source=_source_link(art.path),
                          families=families, **meta)


def _catalog(vault: Path) -> dict:
    catalog = core.load_catalog(vault)
    if not catalog["configured"]:
        raise ModelsExtractRejected("还没指定谱系目录：先选一个目录，家族文件写到那里")
    return catalog


def pick_lineage(catalog: dict, lineage: str | None) -> dict:
    """抽进哪张图。给了就必须是目录里有的；没给时目录里只有一张图（或一张都还没有）才替人定。"""
    known = {x["id"]: x for x in catalog["lineages"]}
    if lineage is not None and lineage in known:
        return known[lineage]
    if lineage:
        raise ModelsExtractRejected(f"谱系目录里没有「{lineage}」这张图：先建 `{catalog['dir']}/{lineage}/{core.LINEAGE_FILE}`")
    if len(known) == 1:
        return next(iter(known.values()))
    if not known:
        return core.bare_lineage("")
    raise ModelsExtractRejected(f"谱系目录里有 {len(known)} 张图（{'、'.join(x['name'] for x in known.values())}），先选抽进哪一张")


def _describe_fields(spec: dict, catalog: dict) -> str:
    if not spec["fields"]:
        return "（这张图除了公共字段没有登记别的字段）"
    def one(f: dict) -> str:
        tags = [t for t in ("数值" if f["numeric"] else "", f"单位 {f['unit']}" if f["unit"] else "",
                            "可多个" if f["list"] else "") if t]
        line = f"- `{f['key']}`" + (f"（{'，'.join(tags)}）" if tags else "")
        return line + (_describe_target(f["target"], catalog) if f.get("target") else "")
    return "\n".join(one(f) for f in spec["fields"])


def _describe_target(target: str, catalog: dict) -> str:
    """跨图关系字段：值是另一张图里的型号名，名字要照那张图里的抄，不然连不上线。"""
    names = [m["name"] for f in catalog["families"]
             for x in catalog["lineages"] if x["id"] == f["lineage"] and target in (x["id"], x["name"])
             for m in f["models"]]
    have = "、".join(names[:80]) if names else "（那张图还是空的，照文章写）"
    return f"：写「{target}」那张图里的型号名，名字照那张图里的抄。那张图现有：{have}"


def _describe_existing(catalog: dict, lineage: str) -> str:
    mine = [f for f in catalog["families"] if f["lineage"] == lineage]
    if not mine:
        return "（这张图还是空的）"
    return "\n".join(f"- {f['name']}：{'、'.join(m['name'] for m in f['models']) or '（还没有型号）'}" for f in mine)


def _describe_nodes(vault: Path) -> str:
    names = sorted({n.get("name") or n["id"] for n in current_index(vault)["nodes"] if not n.get("virtual")})
    return "、".join(names[:MAX_NODES]) or "（图里还没有知识点）"


def _numbers(text: str) -> set[float]:
    """原文里出现过的数。`2022.08` 这种日期写法既当 2022.08，也拆成 2022 和 8——
    不拆的话「发布 2022-08」在原文里永远找不到 2022，每一格都成了可疑（HunDun 初始化时 37 格里几乎全是这个）。"""
    out: set[float] = set()
    for x in RE_NUM.findall(text or ""):
        out.add(float(x))
        if "." in x:
            out.update(float(part) for part in x.split(".") if part)
    return out


def _source_link(path: str) -> str:
    return f"[[{path[:-3] if path.endswith('.md') else path}]]"


def _plan_family(vault: Path, raw: dict, catalog: dict, numbers: set[float], spec: dict) -> dict:
    """一个家族的计划：写到哪个文件、那个文件现在的指纹、每个型号是新增 / 补格 / 不变。

    只在**这张图里**找同名家族：CPU 图里的「Intel」和别处同名的家族不是一回事。新家族建在这张图的子目录里。
    """
    name = str(raw["name"]).strip()
    have = next((f for f in catalog["families"] if f["lineage"] == spec["id"]
                 and (f["name"].lower() == name.lower() or f["id"].lower() == name.lower())), None)
    folder = f"{catalog['dir']}/{spec['id']}" if spec["id"] else catalog["dir"]
    target = have["path"] if have else _new_path(vault, folder, name)
    existing = {m["name"].lower(): m for m in (have or {}).get("models", [])}
    models = [_plan_model(m, existing, numbers, spec) for m in (raw.get("models") or [])
              if isinstance(m, dict) and str(m.get("name") or "").strip()]
    return {"name": have["name"] if have else name, "target": target, "exists": bool(have),
            "digest": _digest(vault / target), "vendor": str(raw.get("vendor") or ""),
            "tagline": str(raw.get("tagline") or ""),
            "open": raw.get("open") if isinstance(raw.get("open"), bool) else None,
            "models": [m for m in models if m["status"] != "same" or m["conflicts"]]}


def _new_path(vault: Path, folder: str, name: str) -> str:
    """新家族的文件名：家族名去掉路径不能用的字符。同名文件已经在了（但不是家族文件）就加序号，不覆盖。"""
    stem = RE_SAFE.sub("-", name).strip("-") or "family"
    rel, n = f"{folder}/{stem}.md", 2
    while (vault / rel).exists():
        rel, n = f"{folder}/{stem}-{n}.md", n + 1
    return rel


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16] if path.is_file() else ""


def checked(spec: dict) -> tuple[str, ...]:
    """这几格的数字要在原文里原样出现：发布 + 这张图声明成「数值」的字段。"""
    return ("发布", *(f["key"] for f in spec["fields"] if f["numeric"]))


def _plan_model(raw: dict, existing: dict, numbers: set[float], spec: dict) -> dict:
    fields = _fields(raw)
    keys = checked(spec)
    have = existing.get(str(raw["name"]).strip().lower())
    if have is None:
        return {"name": str(raw["name"]).strip(), "status": "new", "fields": fields, "conflicts": [],
                "suspicious": _suspicious(fields, numbers, keys)}
    current = _current_fields(have)
    added = {k: v for k, v in fields.items() if not current.get(k)}
    conflicts = [{"key": k, "article": v, "catalog": current[k]} for k, v in fields.items()
                 if current.get(k) and not _same(k, current[k], v, keys)]
    return {"name": have["name"], "status": "update" if added else "same", "fields": added,
            "conflicts": conflicts, "suspicious": _suspicious(added, numbers, keys)}


def _fields(raw: dict) -> dict[str, str]:
    """模型给的一条 → {中文键: 写进文件的值}。公共字段按 KEYS 取；`values`（本图声明的）和 `extra` 的键原样收。
    列表用「、」接起来；空的不要。"""
    out: dict[str, str] = {}
    for key, label in KEYS.items():
        text = _text(raw.get(key))
        if text:
            out[label] = text
    for bag in ("values", "extra"):
        for key, value in (raw.get(bag) or {}).items() if isinstance(raw.get(bag), dict) else []:
            key, text = str(key).strip(), _text(value)
            if key and text and key not in core.CATALOG_FIELDS and key not in out:
                out[key] = text
    return out


def _text(value) -> str:
    if isinstance(value, list):
        return "、".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def _current_fields(model: dict) -> dict[str, str]:
    """目录里这个型号现在各格写的是什么（中文键 → 原文字）：公共字段 + 本图声明的 + 没登记的。"""
    out = {label: model.get(key) for label, key in core.CATALOG_FIELDS.items() if key not in ("tech", "base")}
    out["技术"] = "、".join(t["text"] for t in model.get("tech") or []) or None
    out["基于"] = "、".join(model.get("base") or []) or None
    return {**{k: str(v) for k, v in out.items() if v}, **(model.get("values") or {}), **(model.get("extra") or {})}


def _same(key: str, a: str, b: str, keys: tuple[str, ...]) -> bool:
    """同一个意思就不算冲突：数字类的格子比里面的数（`236B` 和 `236B总参数`），别的去掉空白和括号比。"""
    if key in keys:
        na, nb = {float(x) for x in RE_NUM.findall(a)}, {float(x) for x in RE_NUM.findall(b)}
        if na and na == nb:
            return True
    return _norm(a) == _norm(b)


def _norm(text: str) -> str:
    return re.sub(r"[\s\[\]]+", "", str(text or "")).lower()


def _suspicious(fields: dict[str, str], numbers: set[float], keys: tuple[str, ...]) -> list[dict]:
    """确定性核对：这几格里出现的每个数字，原文里都得有同一个数。`05` 和 `5月` 算同一个数。"""
    out = []
    for key in keys:
        value = fields.get(key)
        if not value or re.search(r"未(披露|公开)", value):
            continue
        missing = [x for x in RE_NUM.findall(value) if float(x) not in numbers]
        if missing:
            out.append({"key": key, "value": value, "why": f"原文里找不到 {'、'.join(missing)}"})
    return out


# ---------------------------------------------------------------- 2. 可疑的格子问 review

def verify(vault: Path, path: str, items: list[dict]) -> ModelsVerified:
    if not items:
        raise ModelsExtractRejected("没有要核对的格子")
    try:
        art = articles_svc.read_article(vault, path)
    except articles_svc.ArticleRejected as exc:
        raise ModelsExtractRejected(str(exc)) from exc
    lines = "\n".join(f"- [{i}] 「{it['model']}」的{it['key']}：{it['value']}" for i, it in enumerate(items))
    prompt = _prompt("models-verify.md").replace("{{items}}", lines).replace("{{article}}", art.text)
    raw, meta = ask_meta(vault, "review", prompt, op="models-verify")
    data = parse_json(raw, "models-verify", vault=vault) or {}
    got = {}
    for r in data.get("checks") or []:
        if isinstance(r, dict) and str(r.get("i", "")).strip().isdigit():
            got[int(r["i"])] = r
    checks = [{**it, **_verdict(got.get(i))} for i, it in enumerate(items)]
    return ModelsVerified(checks=checks, **meta)


def _verdict(r: dict | None) -> dict:
    if not r:
        return {"verdict": "unsure", "why": "review 没回这一格", "quote": "", "suggest": ""}
    verdict = str(r.get("verdict") or "").strip()
    return {"verdict": verdict if verdict in ("ok", "wrong", "unsure") else "unsure",
            "why": str(r.get("why") or "").strip(), "quote": str(r.get("quote") or "").strip(),
            "suggest": str(r.get("suggest") or "").strip()}


# ---------------------------------------------------------------- 3. 写入

def apply(vault: Path, req: ModelsApply) -> ModelsApplied:
    _catalog(vault)
    groups: dict[str, list] = {}
    for fam in req.families:
        if fam.models:
            groups.setdefault(fam.target, []).append(fam)
    if not groups:
        raise ModelsExtractRejected("一个型号都没勾，没有要写的")
    for target, fams in groups.items():
        if _digest(vault / target) != fams[0].digest:
            raise ModelsStale(f"`{target}` 在预览之后被改过：重新抽一次，别在过期的计划上写")
    backup = _snapshot(vault, list(groups))
    written = 0
    for target, fams in groups.items():
        # 出处按家族走：一批抽了六篇，DeepSeek 的出处只该是讲 DeepSeek 的那一篇（HunDun 初始化时六篇全挂到了每个家族上）
        text, n = _render(vault / target, fams, list(dict.fromkeys(s for f in fams for s in f.sources)))
        core.write(vault / target, text)
        written += n
    return ModelsApplied(files=list(groups), models=written, backup=backup)


def _snapshot(vault: Path, targets: list[str]) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = vault / ".knowrary" / "backup" / f"models-{stamp}"
    for rel in targets:
        if (vault / rel).is_file():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(vault / rel, root / rel)
    return root.relative_to(vault).as_posix()


def _render(path: Path, fams: list, sources: list[str]) -> tuple[str, int]:
    """把几份计划合进这个家族文件：已有型号补空格（对着现在的文件重新判一次），新型号接在末尾。"""
    text = core.read(path) if path.is_file() else _new_family(fams[0])
    fm, body = core.split_frontmatter(text)
    have = {m["name"].lower(): m for m in core.parse_family(text, "", path.stem)["models"]} if fm else {}
    lines, written = body.rstrip("\n").split("\n") if body.strip() else [], 0
    for fam in fams:
        for m in fam.models:
            current = _current_fields(have[m.name.lower()]) if m.name.lower() in have else None
            fields = {k: v for k, v in m.fields.items() if current is None or not current.get(k)}
            notes = (getattr(m, "notes", "") or "").strip("\n")
            if not fields and not notes:
                continue
            lines = _append_fields(lines, m.name, fields, notes) if current is not None else \
                _append_section(lines, m.name, fields, notes)
            written += 1
    fm["sources"] = list(dict.fromkeys([*(fm.get("sources") or []), *sources]))
    return core.dump_frontmatter(fm) + "\n" + "\n".join(lines).strip("\n") + "\n", written


def _new_family(fam) -> str:
    fm = {"kind": "family", "name": fam.name}
    fm.update({k: v for k, v in (("vendor", fam.vendor), ("open", fam.open), ("tagline", fam.tagline))
               if v not in (None, "")})
    return core.dump_frontmatter(fm)


def _append_section(lines: list[str], name: str, fields: dict[str, str], notes: str = "") -> list[str]:
    return [*lines, "", f"## {name}", *(f"- {k}:: {v}" for k, v in fields.items()), *_note_lines(notes)]


def _note_lines(notes: str) -> list[str]:
    return ["", *notes.split("\n")] if notes else []


def _append_fields(lines: list[str], name: str, fields: dict[str, str], notes: str = "") -> list[str]:
    """在 `## name` 那一节接上新的格子：格子接在最后一条 `- 键:: 值` 后面，笔记接在这一节末尾。"""
    start = next(i for i, ln in enumerate(lines) if re.match(rf"^##\s+{re.escape(name)}\s*#*\s*$", ln, re.I))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    items = [i for i in range(start, end) if RE_FIELD_LINE.match(lines[i])]
    at = (items[-1] if items else start) + 1
    lines = [*lines[:at], *(f"- {k}:: {v}" for k, v in fields.items()), *lines[at:]]
    end += len(fields)
    tail = max((i for i in range(start, end) if lines[i].strip()), default=start) + 1
    return [*lines[:tail], *_note_lines(notes), *lines[tail:]]
