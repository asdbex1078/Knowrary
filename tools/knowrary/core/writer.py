"""Markdown 写回：ChangeSet 是唯一入口，只改 `## 关系` 区块和白名单 frontmatter 字段。

三条硬约束（设计文档 3.4.3 / 阶段 3 验收）：
1. **其余原文逐字保留**——正文、代码块、列表、`## 参考资料`、`## 待办` 全都按原样吐回；
   没有 update_frontmatter 变更的文件，连 frontmatter 都不重新序列化。
2. **先预览后执行**：dry_run 返回每个文件的新内容与差异摘要，不碰磁盘。
3. **外部改过就拒绝**：比对文件指纹，Obsidian 里改过而索引还没更新时直接报冲突，不覆盖。
"""
from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .mdio import (RE_ID_OK, RE_NEXT_H2, RE_REL_HEADER, dump_frontmatter, read, split_frontmatter,
                    write)
from .parser import LAYERS, LAYOUT_KEYS, STATUS_VALUES, digest_of
from .relations import Edge, parse_relations

# 允许通过 ChangeSet 修改的 frontmatter 字段；布局字段和 id 永远不许改
EDITABLE_FIELDS = ("name", "field", "layer", "params", "type", "status", "year", "start_year", "end_year",
                   "aliases", "tags", "desc", "learned", "source")
CHANGE_TYPES = ("add_edge", "remove_edge", "update_edge", "update_frontmatter", "create_node",
                "update_body", "append_body")
# 新建的知识点只允许落在这两棵树下（规范 2：nodes/ 是知识点，fields/ 是领域总览）
NODE_ROOTS = ("nodes", "fields")
MAX_BODY = 40000      # 正文写回的上限：编辑框写崩了也不至于把一个文件撑爆


class ChangeRejected(Exception):
    """变更本身不合法（未知类型、改了不许改的字段、目标不存在…）。"""


class WriteConflict(Exception):
    """文件在索引生成之后被外部改过，拒绝覆盖。"""


@dataclass
class FileEdit:
    path: Path
    rel: str
    before: str
    after: str
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.before != self.after


def split_sections(text: str) -> tuple[str, str, str, str]:
    """把文件拆成 (frontmatter 原文, 正文, 关系段, 关系段之后的原文)。"""
    fm_text = ""
    rest = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 3)
        if end != -1:
            fm_text, rest = text[: end + 5], text[end + 5:]
    parts = RE_REL_HEADER.split(rest, maxsplit=1)
    if len(parts) == 1:
        return fm_text, parts[0], "", ""
    body = parts[0]
    m = RE_NEXT_H2.search(parts[1])
    return (fm_text, body, parts[1], "") if m is None else (fm_text, body, parts[1][:m.start()], parts[1][m.start():])


def _relation_lines(section: str, node_id: str) -> tuple[list[Edge], list[str]]:
    """解析关系段，同时保留无法识别的行（注释、空行）原样。"""
    edges, _ = parse_relations(section, node_id)
    extras = [ln for ln in section.splitlines()
              if ln.strip() and not ln.strip().startswith("-")]
    return edges, extras


def _render_section(edges: list[Edge], extras: list[str]) -> str:
    lines = [e.line() for e in edges] + extras
    return "\n" + "\n".join(lines) + "\n" if lines else "\n"


def _same_edge(e: Edge, change: dict) -> bool:
    return e.target == change.get("target") and (
        change.get("relation") in (None, e.type) or change.get("from_relation") in (None, e.type))


def _checked_body(change: dict) -> str:
    """正文类变更共用的校验：非空、长度上限，以及绝不许自带 `## 关系`。"""
    text = str(change.get("body") or "")
    if not text.strip():
        raise ChangeRejected(f"`{change['type']}` 没给 body，没什么可写的")
    if len(text) > MAX_BODY:
        raise ChangeRejected(f"正文太长（{len(text)} 字，上限 {MAX_BODY}）")
    if RE_REL_HEADER.search(text):
        raise ChangeRejected("正文里不能再出现 `## 关系`：关系区块由关系解析器独占，只能改关系行")
    return text


def apply_to_text(text: str, node_id: str, changes: list[dict]) -> tuple[str, list[str]]:
    """把这一批变更作用到单个文件的原文上，返回 (新原文, 变更说明)。"""
    fm_text, body, section, tail = split_sections(text)
    edges, extras = _relation_lines(section, node_id)
    notes: list[str] = []
    fm_changed = False
    fm = split_frontmatter(text)[0] if fm_text else {}

    for change in changes:
        kind = change["type"]
        if kind == "add_edge":
            if any(e.type == change["relation"] and e.target == change["target"] for e in edges):
                raise ChangeRejected(f"关系已存在：{change['relation']} → {change['target']}")
            edges.append(Edge(node_id, change["relation"], change["target"],
                              change.get("year"), (change.get("note") or "").strip()))
            notes.append(f"+ {change['relation']}:: [[{change['target']}]]")
        elif kind == "remove_edge":
            hit = [e for e in edges if _same_edge(e, change)]
            if not hit:
                raise ChangeRejected(f"要删除的关系不存在：{change.get('relation')} → {change.get('target')}")
            for e in hit:
                edges.remove(e)
                notes.append(f"- {e.type}:: [[{e.target}]]")
        elif kind == "update_edge":
            hit = [e for e in edges if e.target == change["target"]
                   and e.type == (change.get("from_relation") or e.type)]
            if not hit:
                raise ChangeRejected(f"要修改的关系不存在：→ {change.get('target')}")
            for e in hit:
                old = e.line()
                e.type = change.get("relation") or e.type
                if "year" in change:
                    e.year = change["year"]
                if "note" in change:
                    e.note = (change["note"] or "").strip()
                notes.append(f"~ {old}  →  {e.line()}")
        elif kind == "update_body":
            new_body = _checked_body(change)
            # 只换 frontmatter 与 `## 关系` 之间这一段；关系区块和它后面的
            # `## 参考资料` / `## 待办` 由下面的 rebuilt 原样接回去。
            # **缩水要在卡片上喊出来**：整段替换最典型的事故不是写错字，是模型带回来的"原文"
            # 少了一截，一按写入就把我以前记的东西删了。diff 里看得见，但卡片上的一行字更看得见。
            if len(new_body) < len(body) * 0.6 and len(body) > 200:
                notes.append(f"⚠️ 正文从 {len(body)} 字缩到 {len(new_body)} 字——"
                             f"确认是有意重写，不是原文没带全（补内容该用 append_body）")
            body = new_body.strip("\n") + "\n"
            notes.append(f"改写正文（{len(new_body)} 字）")
        elif kind == "append_body":
            # **只追加不替换**：`update_body` 要求把原文一字不落地带回来，而模型看到的原文
            # 随时可能是被截断过的——带少了，写回去就是把我以前记的东西抹掉。
            # 往笔记里补一段本来就不需要读全篇，这条路径从根上免掉那个风险。
            add = _checked_body(change)
            if len(body) + len(add) > MAX_BODY:
                raise ChangeRejected(f"追加后正文太长（{len(body) + len(add)} 字，上限 {MAX_BODY}）")
            body = body.rstrip("\n") + "\n\n" + add.strip("\n") + "\n"
            notes.append(f"正文追加一段（+{len(add)} 字）")
        elif kind == "update_frontmatter":
            for key, value in (change.get("fields") or {}).items():
                if key in LAYOUT_KEYS or key == "id":
                    raise ChangeRejected(f"不允许修改字段 `{key}`")
                if key not in EDITABLE_FIELDS:
                    raise ChangeRejected(f"未知 frontmatter 字段 `{key}`")
                if key == "status" and value not in STATUS_VALUES:
                    raise ChangeRejected(f"status `{value}` 不合法")
                # layer 和 create_node 那边同一把尺子。原来只有新建校验、改的时候不校验，
                # 于是 propose_changes 能从这条路写进一个不存在的层名——
                # 它不报错，只会让这个节点在历史视图上凭空消失（泳道按 LAYERS 建，对不上的没地方去）。
                if key == "layer" and value and value not in LAYERS:
                    raise ChangeRejected(f"layer `{value}` 不在已知的抽象层里（{' / '.join(LAYERS)}）")
                fm[key] = value
                fm_changed = True
                notes.append(f"frontmatter {key} = {value!r}")
        else:
            raise ChangeRejected(f"未知变更类型 `{kind}`")

    head = dump_frontmatter(fm) if fm_changed else fm_text
    new_section = _render_section(edges, extras)
    # tail（`## 参考资料` / `## 待办`）前面补一个空行：_render_section 会把关系段尾部的
    # 空行归一掉，直接拼会变成 `- 部件:: [[x]]` 紧贴着下一个 `## 标题`。
    rebuilt = head + body.rstrip("\n") + "\n\n## 关系" + new_section + ("\n" + tail if tail else "")
    return rebuilt, notes


def _render_new_node(fields: dict) -> str:
    """新知识点的初始原文：规范 3 的必填 frontmatter + 规范 4 推荐的正文骨架。"""
    fm = {k: v for k, v in fields.items() if v not in (None, "", [])}
    body = f"# {fm['name']}\n\n## 描述\n{fm['desc']}\n\n## 关系\n"
    return dump_frontmatter(fm) + body


def _create_node_edit(vault: Path, change: dict, taken: set[str]) -> FileEdit:
    """把一条 create_node 变成"新建这个文件"。

    路径由客户端给（它知道你在画布哪个域上右键），但必须落在 nodes/ 下、目录已存在、
    文件还不存在——否则一个笔误就能往仓库任意位置写文件。
    """
    node_id = str(change.get("source") or "").strip()
    if not node_id or not RE_ID_OK.match(node_id):
        raise ChangeRejected(f"节点 id `{node_id}` 不合法（不能为空，也不能含 / \\ : * ? \" < > | 和空白）")
    if node_id in taken:
        raise ChangeRejected(f"节点 `{node_id}` 已经存在")
    fields = dict(change.get("fields") or {})
    for key in fields:
        if key not in EDITABLE_FIELDS:
            raise ChangeRejected(f"未知 frontmatter 字段 `{key}`")
    for key in ("name", "field", "desc"):
        if not str(fields.get(key) or "").strip():
            raise ChangeRejected(f"`{key}` 是必填字段（规范 3）")
    if fields.get("status") and fields["status"] not in STATUS_VALUES:
        raise ChangeRejected(f"status `{fields['status']}` 不合法")
    if fields.get("layer") and fields["layer"] not in LAYERS:
        raise ChangeRejected(f"layer `{fields['layer']}` 不在已知的抽象层里（{' / '.join(LAYERS)}）")

    rel = str(change.get("path") or f"{NODE_ROOTS[0]}/{node_id}.md").strip().lstrip("/")
    if not rel.endswith(".md"):
        raise ChangeRejected(f"路径必须以 .md 结尾：{rel}")
    path = vault / rel
    # 用 resolve 比对而不是拿相对路径字符串比：`nodes/../跑出去.md` 这种要在这里现形。
    # 但 rel 本身不能从 resolve 的结果反推——macOS 上 /var 是 /private/var 的软链，
    # vault 没 resolve 过，两边算相对路径会直接抛异常。
    if not any(path.resolve().is_relative_to((vault / root).resolve()) for root in NODE_ROOTS):
        raise ChangeRejected(f"新知识点只能建在 {' / '.join(r + '/' for r in NODE_ROOTS)} 下：{rel}")
    if path.exists():
        raise ChangeRejected(f"文件已存在：{rel}")
    # 目录不存在就顺手建：开一个新领域时 vault 里本来就没有那个文件夹，
    # 逼着人先去 Obsidian 里手动新建一个空目录没有道理。
    # 越界与 `..` 已经在上面的 resolve 比对里拦掉了，这里只可能建在 nodes/ 或 fields/ 底下。
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.stem != node_id:
        raise ChangeRejected(f"文件名要和 id 一致（id 默认取文件名）：{path.name} ≠ {node_id}.md")
    return FileEdit(path=path, rel=rel, before="",
                    after=_render_new_node(fields), notes=[f"新建知识点 {node_id}"])


def plan(vault: Path, changes: list[dict], index: dict) -> list[FileEdit]:
    """把 ChangeSet 变成"每个文件改成什么样"，不写盘。外部改过的文件直接报冲突。"""
    by_node: dict[str, list[dict]] = {}
    creates: list[dict] = []
    for change in changes:
        if change["type"] not in CHANGE_TYPES:
            raise ChangeRejected(f"未知变更类型 `{change['type']}`")
        if change["type"] == "create_node":
            creates.append(change)          # 还不在索引里，不能按"改已有文件"走
            continue
        by_node.setdefault(change["source"], []).append(change)

    nodes = {n["id"]: n for n in index["nodes"]}
    edits: list[FileEdit] = []
    taken = set(nodes)
    created: dict[str, FileEdit] = {}
    for change in creates:
        edit = _create_node_edit(vault, change, taken)
        edits.append(edit)
        taken.add(change["source"])
        created[change["source"]] = edit
    for node_id, group in sorted(by_node.items()):
        # 同一批里刚新建的节点：它还不在索引里，但边要能挂上去——
        # 否则"建一个节点顺便连几条边"只能拆成两次请求，中间那一刻图上多一个孤岛。
        fresh = created.get(node_id)
        if fresh is not None:
            fresh.after, notes = apply_to_text(fresh.after, node_id, group)
            fresh.notes = [*fresh.notes, *notes]
            continue
        meta = nodes.get(node_id)
        if not meta or not meta.get("path"):
            raise ChangeRejected(f"节点 `{node_id}` 不存在或没有对应文件")
        path = vault / meta["path"]
        if not path.exists():
            raise ChangeRejected(f"文件不存在：{meta['path']}")
        text = read(path)
        if meta.get("digest") and digest_of(text) != meta["digest"]:
            raise WriteConflict(f"{meta['path']} 在索引生成后被改过（可能是 Obsidian），请刷新后重试")
        after, notes = apply_to_text(text, node_id, group)
        edits.append(FileEdit(path=path, rel=meta["path"], before=text, after=after, notes=notes))
    return edits


def backup(vault: Path, edits: list[FileEdit]) -> str:
    """写回前把原文件整份快照到 .knowrary/backup/<时间戳>/。"""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = vault / ".knowrary" / "backup" / stamp
    for edit in edits:
        if not edit.path.exists():
            continue                        # 新建的文件没有"原文"可备份
        target = root / edit.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(edit.path, target)
    return root.relative_to(vault).as_posix()


def commit(vault: Path, edits: list[FileEdit]) -> str:
    """备份后逐个写回。返回备份目录的相对路径。"""
    touched = [e for e in edits if e.changed]
    if not touched:
        return ""
    snapshot = backup(vault, touched)
    for edit in touched:
        write(edit.path, edit.after)
    return snapshot
