"""把一个其实是「型号」的知识点挪进模型目录（2026-09-30）。

型号不是知识节点（DeepSeek-V3 那种），但对话 / 导入难免又建出来。挪法：
- **字段搬过去**：frontmatter 的 year → 发布、params → 参数、desc → 亮点；目录里这个型号已经有的格子不动（只补空格）；
- **正文不丢**：原来的正文当这一节的笔记接在字段后面，里面的 `##` 降成 `###`（不然会被当成另一个型号）；
- **节点删掉**：文件、所有画布上的位置和边样式、项目清单里的这一项、复习记录一起拿掉；
- **有别的点链着它就不挪**：`[[它]]` 挪走之后就成了断链，先把那几处改掉再来——列出是哪几个文件。

先 `dry_run` 看影响面，确认再写；写前整份快照，中途失败按快照恢复（`core.guarded`，同改名 / 合并）。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import models_extract as mx
from .contracts import FamilyPlan, ModelFromNode, ModelFromNodeResult, ModelPlan
from .index_service import current_index, invalidate
from .paths import core

RE_HEADING = re.compile(r"^(#{1,6})(\s)", re.M)


class ModelMoveRejected(ValueError):
    """挪不了：不是知识点、还有别的点链着它、没指定模型目录。"""


def move(vault: Path, req: ModelFromNode) -> ModelFromNodeResult:
    catalog = mx._catalog(vault)
    index = current_index(vault)
    node = next((n for n in index["nodes"] if n["id"] == req.node and not n.get("virtual")), None)
    if node is None or not node.get("path"):
        raise ModelMoveRejected(f"「{req.node}」不是图里的知识点")
    links = {f: n for f, n in core.rename.scan_links(vault, req.node).items() if f != node["path"]}
    if links:
        raise ModelMoveRejected(f"还有 {sum(links.values())} 处链着「{req.node}」（{'、'.join(sorted(links))}），"
                                "挪走就成了断链：先把这几处改成纯文字或者删掉")
    family = (req.family or _guess_family(node["name"] or node["id"])).strip()
    plan = _plan(vault, catalog, node, family, _lineage_of(catalog, family, req.lineage))
    impact = _impact(vault, req.node)
    out = ModelFromNodeResult(node=req.node, family=plan.name, target=plan.target, exists=plan.exists,
                              model_exists=plan.models[0].status != "new", fields=plan.models[0].fields,
                              notes_chars=len(plan.models[0].notes), layouts=impact["layouts"],
                              projects=impact["projects"], reviews=impact["reviews"])
    if req.dry_run:
        return out
    snapshot = {"path": node["path"], "files": [plan.target, *(f".knowrary/{p}" for p in impact["layout_files"])]}
    try:
        backup = core.guarded(vault, snapshot, "to-model", lambda: _apply(vault, plan, node, impact), [])
    except core.RolledBack as exc:
        raise ModelMoveRejected(str(exc)) from exc
    finally:
        invalidate(vault)
    return out.model_copy(update={"applied": True, "backup": backup})


def _guess_family(name: str) -> str:
    """`DeepSeek-V3` → `DeepSeek`、`Qwen2.5` → `Qwen`、`GPT-4o` → `GPT`、`Kimi K2` → `Kimi`。"""
    hit = re.match(r"[A-Za-z]+|[一-鿿]+", name)
    return hit.group(0) if hit else name


def _lineage_of(catalog: dict, family: str, lineage: str) -> dict:
    """家族已经在某张图里就是那张；还没有这个家族时按请求里给的图（或目录里唯一的那张）新建。"""
    have = next((f for f in catalog["families"] if f["name"].lower() == family.lower()), None)
    try:
        return mx.pick_lineage(catalog, have["lineage"] if have else (lineage or None))
    except mx.ModelsExtractRejected as exc:
        raise ModelMoveRejected(f"「{family}」还不在任何一张谱系图里：{exc}") from exc


def _plan(vault: Path, catalog: dict, node: dict, family: str, spec: dict) -> FamilyPlan:
    text = core.read(vault / node["path"])
    fm, body = core.split_frontmatter(text)
    fields = {k: str(v) for k, v in (("发布", fm.get("year")), ("参数", fm.get("params")), ("亮点", fm.get("desc")))
              if v not in (None, "")}
    notes = _notes(_strip_relations(body), node["name"] or node["id"])
    raw = {"name": family, "models": [{"name": node["name"] or node["id"]}]}
    fam = mx._plan_family(vault, raw, catalog, set(), spec)
    have = fam["models"][0] if fam["models"] else {"name": node["name"] or node["id"], "status": "same"}
    current = _current(catalog, fam["name"], have["name"])
    keep = {k: v for k, v in fields.items() if not current.get(k)}
    model = ModelPlan(name=have["name"], status="new" if not current else "update", fields=keep, notes=notes)
    return FamilyPlan(**{**fam, "models": [model]})


def _notes(body: str, name: str) -> str:
    """正文 → 这一节的笔记。开头那个和名字一样的一级标题去掉（型号那一节的标题就是它）；
    其余标题整体往下挪，最高的一级落到 `###`——一个 `##` 都不能留，不然会被读成另一个型号。"""
    body = re.sub(rf"^#\s+{re.escape(name)}\s*\n", "", body.lstrip("\n"), count=1)
    levels = [len(m.group(1)) for m in RE_HEADING.finditer(body)]
    shift = 3 - min(levels) if levels else 0
    return RE_HEADING.sub(lambda m: "#" * min(6, len(m.group(1)) + shift) + m.group(2), body).strip("\n")


def _strip_relations(body: str) -> str:
    """正文到 `## 关系` 为止：关系是知识图上的边，型号那边不认。"""
    return re.split(r"^## 关系\s*$", body, maxsplit=1, flags=re.M)[0]


def _current(catalog: dict, family: str, model: str) -> dict:
    fam = next((f for f in catalog["families"] if f["name"].lower() == family.lower()), None)
    m = next((x for x in (fam or {}).get("models", []) if x["name"].lower() == model.lower()), None)
    return mx._current_fields(m) if m else {}


def _impact(vault: Path, node_id: str) -> dict:
    """它在哪几张画布上、哪几个项目的清单里、有几条复习记录。"""
    layout_files = [p.relative_to(vault / ".knowrary").as_posix()
                    for p in [vault / ".knowrary" / "layout.json", *sorted((vault / ".knowrary" / "layouts").glob("*.json"))]
                    if node_id in ((_load(p).get("nodes")) or {})]
    side = core.rename._json_impact(vault, node_id)
    return {"layouts": layout_files, "layout_files": layout_files, "projects": side["projects"],
            "reviews": side["reviews"]}


def _load(path: Path) -> dict:
    try:
        return core.load_json(path) if path.exists() else {}
    except (ValueError, OSError):
        return {}


def _apply(vault: Path, plan: FamilyPlan, node: dict, impact: dict) -> None:
    text, _ = mx._render(vault / plan.target, [plan], [])
    core.write(vault / plan.target, text)
    (vault / node["path"]).unlink()
    for rel in impact["layout_files"]:
        _drop_from_layout(vault / ".knowrary" / rel, node["id"])
    _drop_from_records(vault, node["id"])


def _drop_from_layout(path: Path, node_id: str) -> None:
    doc = _load(path)
    (doc.get("nodes") or {}).pop(node_id, None)
    for key in [k for k in (doc.get("edges") or {}) if core.rename._edge_touches(k, node_id)]:
        doc["edges"].pop(key)
    doc["refs"] = [r for r in doc.get("refs") or [] if r.get("target") != node_id]
    doc["revision"] = int(doc.get("revision") or 0) + 1      # 还开着的页面撞上 409 去重新拉
    core.write_json_atomic(path, doc)


def _drop_from_records(vault: Path, node_id: str) -> None:
    """项目清单里的这一项、复习记录拿掉；答题流水留着（那是做过的题，历史不改）。"""
    review = vault / ".knowrary" / "review-log.json"
    doc = _load(review)
    if node_id in (doc.get("nodes") or {}):
        doc["nodes"].pop(node_id)
        core.write_json_atomic(review, doc)
    path = vault / ".knowrary" / "projects.json"
    doc = _load(path)
    touched = False
    for project in (doc.get("projects") or {}).values():
        for ls in project.get("lists") or []:
            for stage in ls.get("stages") or []:
                before = len(stage.get("points") or [])
                stage["points"] = [p for p in stage.get("points") or [] if p.get("id") != node_id]
                touched = touched or len(stage["points"]) != before
    if touched:
        doc["revision"] = int(doc.get("revision") or 0) + 1
        core.write_json_atomic(path, doc)
