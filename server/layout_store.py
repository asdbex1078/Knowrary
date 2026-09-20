"""layout.json 的读写：初始生成、部分合并（PATCH）、revision 乐观并发、原子写、孤立引用诊断。

三条硬约束（设计文档 3.4.2）：
1. 客户端必须带 base_revision；与服务端不一致就拒绝，不做"最后写入者赢"。
2. 写盘走临时文件 + rename（core.write_json_atomic），崩溃不会留半个 JSON。
3. 引用不到的节点/分组/图片只进诊断列表，绝不自动删除用户数据。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any, Callable

from .contracts import LAYOUT_SCHEMA_VERSION, EdgeStyle, GroupBox, LayoutDoc, LayoutPatch, NodeBox
from .paths import DEFAULT_LAYOUT, core, layout_path

# 所有 layout 共用一把锁。按名字分锁能多一点并发，但写 layout 本来就稀疏
# （拖一下才写一次），一把锁换来的是"不用想锁表怎么回收"。
_LOCK = threading.Lock()


class LayoutBroken(Exception):
    """layout.json 存在但读不出来：保留原文件，交给人处理，不覆盖。"""


class RevisionConflict(Exception):
    def __init__(self, current: LayoutDoc):
        super().__init__(f"base_revision 过期，当前 revision {current.revision}")
        self.current = current


class PatchRejected(Exception):
    """客户端补丁本身不合法（新增条目缺必填字段、引用了不存在的分组等）。"""


# 这份 layout 还不存在时，拿什么把它填出来：index → layout dict
Builder = Callable[[dict], dict]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_layout(vault: Path, name: str = DEFAULT_LAYOUT) -> LayoutDoc | None:
    """读某一份 layout；文件不存在返回 None，内容坏了抛 LayoutBroken。"""
    path = layout_path(vault, name)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise LayoutBroken(f"{path} 解析失败：{exc}") from exc
    try:
        return LayoutDoc.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise LayoutBroken(f"{path} 不符合 layout 契约：{exc}") from exc


def initial_layout(index: dict) -> LayoutDoc:
    """core 生成的初始布局（纯 dict）→ 过一遍契约校验，保证服务端产出的也合法。"""
    return LayoutDoc.model_validate(core.build_initial_layout(index))


def write_layout(vault: Path, doc: LayoutDoc, name: str = DEFAULT_LAYOUT) -> LayoutDoc:
    """revision +1、更新时间戳后原子写盘。"""
    doc.schema_version = LAYOUT_SCHEMA_VERSION
    doc.revision += 1
    doc.updated_at = _now()
    core.write_json_atomic(layout_path(vault, name), doc.model_dump())
    return doc


def load_or_init(vault: Path, index: dict, name: str = DEFAULT_LAYOUT,
                 build: Builder | None = None) -> tuple[LayoutDoc, bool]:
    """没有这份 layout 时生成一份初始的并落盘。返回 (布局, 是否刚生成)。

    全局图按 field / 子目录铺；项目画布和对比画布各有各的铺法，由调用方给 `build`。
    **这一层不认识"项目"也不认识"对比组"**：它只知道"这份 layout 不存在时拿什么填"。
    以前这里直接写死 `build_project_layout`，于是每多一种画布就要改一次存储层。
    """
    with _LOCK:
        doc = read_layout(vault, name)
        if doc is not None:
            return doc, False
        fresh = LayoutDoc.model_validate(build(index)) if build is not None else initial_layout(index)
        return write_layout(vault, fresh, name), True


BACKUP_KEEP = 10


def is_bulk(patch: LayoutPatch) -> bool:
    """整体重排（换布局算法）这类大改动：删分组、或一次动 20 个以上节点。"""
    drops_group = bool(patch.groups) and any(v is None for v in patch.groups.values())
    many_nodes = bool(patch.nodes) and len(patch.nodes) > 20
    return drops_group or many_nodes


def backup_layout(vault: Path, doc: LayoutDoc, name: str = DEFAULT_LAYOUT) -> str:
    """把当前 layout 存一份快照再覆盖，只保留最近 BACKUP_KEEP 份。"""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = vault / ".knowrary" / "backup" / f"{name}-r{doc.revision}-{stamp}.json"
    core.write_json_atomic(path, doc.model_dump())
    old = sorted(path.parent.glob(f"{name}-r*.json"))[:-BACKUP_KEEP]
    for stale in old:
        stale.unlink(missing_ok=True)
    return path.relative_to(vault).as_posix()


def apply_patch(vault: Path, patch: LayoutPatch, index: dict, name: str = DEFAULT_LAYOUT,
                build: Builder | None = None) -> tuple[LayoutDoc, list[dict[str, Any]], str | None]:
    """读-校验-合并-写，全程持锁。返回 (新布局, 孤立引用诊断, 备份路径)。"""
    with _LOCK:
        doc = read_layout(vault, name)
        if doc is None:
            doc = (LayoutDoc.model_validate(build(index)) if build is not None
                   else initial_layout(index))
        if patch.base_revision != doc.revision:
            raise RevisionConflict(doc)
        backup = backup_layout(vault, doc, name) if is_bulk(patch) and doc.revision else None
        _merge(doc, patch)
        _assert_group_refs(doc)
        return write_layout(vault, doc, name), find_orphans(doc, index, vault), backup


def _merge(doc: LayoutDoc, patch: LayoutPatch) -> None:
    """按 JSON Merge Patch 语义合并：条目为 null 删除，否则字段级合并。"""
    if patch.viewport is not None:
        doc.viewport = patch.viewport
    _merge_entries(doc.groups, patch.groups, GroupBox, "分组")
    _merge_entries(doc.nodes, patch.nodes, NodeBox, "节点")
    _merge_entries(doc.edges, patch.edges, EdgeStyle, "边")
    for name in ("refs", "notes", "images"):
        given = getattr(patch, name)
        if given is not None:
            setattr(doc, name, given)


def _merge_entries(target: dict, given: dict | None, model, label: str) -> None:
    if not given:
        return
    for key, value in given.items():
        if value is None:
            target.pop(key, None)
            continue
        fields = value.model_dump(exclude_unset=True)
        if key in target:
            target[key] = model.model_validate({**target[key].model_dump(), **fields})
            continue
        try:
            target[key] = model.model_validate(fields)
        except Exception as exc:
            raise PatchRejected(f"新增{label} `{key}` 的字段不完整：{exc}") from exc


def _assert_group_refs(doc: LayoutDoc) -> None:
    """分组引用必须闭合：父分组、节点所属分组都得存在，否则画布会渲染出悬空元素。"""
    known = set(doc.groups)
    for gid, group in doc.groups.items():
        if group.parent and group.parent not in known:
            raise PatchRejected(f"分组 `{gid}` 的父分组 `{group.parent}` 不存在")
        if group.parent == gid:
            raise PatchRejected(f"分组 `{gid}` 不能以自己为父分组")
    for nid, node in doc.nodes.items():
        if node.group and node.group not in known:
            raise PatchRejected(f"节点 `{nid}` 的分组 `{node.group}` 不存在")


def find_orphans(doc: LayoutDoc, index: dict, vault: Path) -> list[dict[str, Any]]:
    """委托给 core：CLI 的 `knowrary layout check` 与服务端用同一套判定。"""
    return core.find_orphans(doc.model_dump(), index, vault)
