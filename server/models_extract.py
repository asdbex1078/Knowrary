"""把原文抽成模型目录（2026-09-30，模型谱系第 2 步）。

三段，前端一段一段地发（每段都要看得见在干什么）：

1. `propose`：learn 读一篇文章，吐出家族和型号；和目录里已有的**合并成计划**——新型号整条新增，
   已有型号**只补空着的格子**，和目录里写的不一样的格子列成冲突、不改。
   同时做**确定性核对**：参数 / 激活 / 上下文 / 训练数据 / 发布里的数字必须在原文里原样出现，对不上的标可疑。
2. `verify`：只有可疑的格子才让 review 逐格判一次（ok / 写错了 / 拿不准，带原文引句）。全对得上就不调模型。
3. `apply`：人勾掉不要的型号之后写入。按目标文件分组，预览之后文件被改过就拒（409），
   写前把要动的家族文件整份快照；写入时对着**当时的文件**重新合并一遍，只补不覆盖。

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

# 模型吐的 JSON 键 → 家族文件里的中文键（反查 core.catalog.FIELDS，顺序即写入顺序）
KEYS = {v: k for k, v in core.CATALOG_FIELDS.items()}
CHECKED = ("发布", "参数", "激活", "上下文", "训练数据")   # 这几格的数字要在原文里原样出现
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

def propose(vault: Path, path: str) -> ModelsProposal:
    catalog = _catalog(vault)
    try:
        art = articles_svc.read_article(vault, path)
    except articles_svc.ArticleRejected as exc:
        raise ModelsExtractRejected(str(exc)) from exc
    prompt = (_prompt("models.md").replace("{{existing}}", _describe_existing(catalog))
              .replace("{{nodes}}", _describe_nodes(vault)).replace("{{article}}", art.text))
    raw, meta = ask_meta(vault, "learn", prompt, op="models-extract")
    data = parse_json(raw, "models-extract", vault=vault) or {}
    numbers = _numbers(art.text)
    families = [_plan_family(vault, f, catalog, numbers) for f in (data.get("families") or [])
                if isinstance(f, dict) and str(f.get("name") or "").strip()]
    families = [f for f in families if f["models"]]
    if not families:
        raise ModelsExtractRejected(f"《{art.title}》里没抽出任何型号（它可能不是讲模型迭代的文章）")
    return ModelsProposal(path=art.path, title=art.title, source=_source_link(art.path), families=families, **meta)


def _catalog(vault: Path) -> dict:
    catalog = core.load_catalog(vault)
    if not catalog["configured"]:
        raise ModelsExtractRejected("还没指定模型目录：先选一个目录，家族文件写到那里")
    return catalog


def _describe_existing(catalog: dict) -> str:
    if not catalog["families"]:
        return "（目录还是空的）"
    return "\n".join(f"- {f['name']}：{'、'.join(m['name'] for m in f['models']) or '（还没有型号）'}"
                     for f in catalog["families"])


def _describe_nodes(vault: Path) -> str:
    names = sorted({n.get("name") or n["id"] for n in current_index(vault)["nodes"] if not n.get("virtual")})
    return "、".join(names[:MAX_NODES]) or "（图里还没有知识点）"


def _numbers(text: str) -> set[float]:
    return {float(x) for x in RE_NUM.findall(text or "")}


def _source_link(path: str) -> str:
    return f"[[{path[:-3] if path.endswith('.md') else path}]]"


def _plan_family(vault: Path, raw: dict, catalog: dict, numbers: set[float]) -> dict:
    """一个家族的计划：写到哪个文件、那个文件现在的指纹、每个型号是新增 / 补格 / 不变。"""
    name = str(raw["name"]).strip()
    have = next((f for f in catalog["families"] if f["name"].lower() == name.lower()
                 or f["id"].lower() == name.lower()), None)
    target = have["path"] if have else _new_path(vault, catalog["dir"], name)
    existing = {m["name"].lower(): m for m in (have or {}).get("models", [])}
    models = [_plan_model(m, existing, numbers) for m in (raw.get("models") or [])
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


def _plan_model(raw: dict, existing: dict, numbers: set[float]) -> dict:
    fields = _fields(raw)
    have = existing.get(str(raw["name"]).strip().lower())
    if have is None:
        return {"name": str(raw["name"]).strip(), "status": "new", "fields": fields, "conflicts": [],
                "suspicious": _suspicious(fields, numbers)}
    current = _current_fields(have)
    added = {k: v for k, v in fields.items() if not current.get(k)}
    conflicts = [{"key": k, "article": v, "catalog": current[k]} for k, v in fields.items()
                 if current.get(k) and not _same(k, current[k], v)]
    return {"name": have["name"], "status": "update" if added else "same", "fields": added,
            "conflicts": conflicts, "suspicious": _suspicious(added, numbers)}


def _fields(raw: dict) -> dict[str, str]:
    """模型给的一条 → {中文键: 写进文件的值}。列表用「、」接起来；空的不要；extra 的键原样收。"""
    out: dict[str, str] = {}
    for key, label in KEYS.items():
        value = raw.get(key)
        text = "、".join(str(v).strip() for v in value if str(v).strip()) if isinstance(value, list) \
            else str(value or "").strip()
        if text:
            out[label] = text
    for key, value in (raw.get("extra") or {}).items() if isinstance(raw.get("extra"), dict) else []:
        if str(key).strip() and str(value or "").strip() and str(key).strip() not in core.CATALOG_FIELDS:
            out[str(key).strip()] = str(value).strip()
    return out


def _current_fields(model: dict) -> dict[str, str]:
    """目录里这个型号现在各格写的是什么（中文键 → 原文字）。"""
    out = {label: model.get(key) for label, key in core.CATALOG_FIELDS.items() if key not in ("tech", "base")}
    out["技术"] = "、".join(t["text"] for t in model.get("tech") or []) or None
    out["基于"] = "、".join(model.get("base") or []) or None
    return {**{k: str(v) for k, v in out.items() if v}, **(model.get("extra") or {})}


def _same(key: str, a: str, b: str) -> bool:
    """同一个意思就不算冲突：数字类的格子比里面的数（`236B` 和 `236B总参数`），别的去掉空白和括号比。"""
    if key in CHECKED:
        na, nb = {float(x) for x in RE_NUM.findall(a)}, {float(x) for x in RE_NUM.findall(b)}
        if na and na == nb:
            return True
    return _norm(a) == _norm(b)


def _norm(text: str) -> str:
    return re.sub(r"[\s\[\]]+", "", str(text or "")).lower()


def _suspicious(fields: dict[str, str], numbers: set[float]) -> list[dict]:
    """确定性核对：这几格里出现的每个数字，原文里都得有同一个数。`05` 和 `5月` 算同一个数。"""
    out = []
    for key in CHECKED:
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
        text, n = _render(vault / target, fams, req.sources)
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
