"""Inbox / 放置 / Digest / 复习（阶段 4）：把 core 的算法接到 HTTP 上。

分成独立模块而不是堆进 app.py：这四件事共享"读 index + 读 layout"的前置，
而 app.py 里只留一层薄路由。写入仍然只走 layout_store.apply_patch，
revision 并发与备份逻辑一份不复制。
"""
from __future__ import annotations

import datetime as dt
import difflib
from pathlib import Path

from .contracts import (CoachToday, FileDiff, GroupPatch, InboxItem, InboxRead, LayoutDoc, LayoutPatch,
                        NodePatch, Placed, PlaceRequest, PlaceResult, ReviewDone)
from .index_service import current_index
from .layout_store import apply_patch, load_or_init
from .paths import core


class PlaceRejected(Exception):
    """请求本身不合法：节点不在索引里、指定的分组不存在等。"""


def load_pair(vault: Path) -> tuple[dict, LayoutDoc]:
    index = current_index(vault)
    layout, _ = load_or_init(vault, index)
    return index, layout


def inbox(vault: Path) -> InboxRead:
    """还没上画布的节点，附带建议分组。"""
    index, layout = load_pair(vault)
    plain = layout.model_dump()
    by_id = {n["id"]: n for n in index["nodes"]}
    top_names = {g.get("name") for g in plain["groups"].values() if not g.get("parent")}
    homes = core.load_homes(vault)
    items = []
    for nid in core.inbox_ids(index, plain):
        meta = by_id.get(nid, {})
        gid = core.target_group(nid, index, plain)
        field = meta.get("field")
        items.append(InboxItem(
            id=nid, name=meta.get("name") or nid, field=field, desc=meta.get("desc"),
            stub=bool(meta.get("stub")), degree=int(meta.get("degree") or 0),
            suggested_group=gid, suggested_group_name=plain["groups"].get(gid, {}).get("name") if gid else None,
            field_group_missing=bool(field) and gid is None and field not in top_names,
            home=homes.get(nid)))
    return InboxRead(items=items, index_revision=index["revision"], layout_revision=layout.revision)


def _merge_patch(plain: dict, patch_groups: dict, patch_nodes: dict,
                 gpatch: dict, npatch: dict) -> None:
    """长框 / 泳道下移的结果既要并进工作副本（后面的节点才避得开），也要进这一次 PATCH。"""
    for gid, fields in gpatch.items():
        plain["groups"][gid].update(fields)
        merged = {**(patch_groups[gid].model_dump(exclude_none=True) if gid in patch_groups else {}),
                  **fields}
        patch_groups[gid] = GroupPatch(**merged)
    for nid, fields in npatch.items():
        plain["nodes"][nid].update(fields)
        merged = {**(patch_nodes[nid].model_dump(exclude_none=True) if nid in patch_nodes else {}),
                  **fields}
        patch_nodes[nid] = NodePatch(**merged)


def _one_placement(nid: str, req: PlaceRequest, index: dict, plain: dict,
                   today: str) -> tuple[dict | None, dict[str, dict], dict[str, dict]]:
    """算一个节点的落点。返回 (节点条目, 分组补丁, 节点补丁)；人工指定坐标时不长框。"""
    if req.at is not None:
        gid = req.group or core.target_group(nid, index, plain)
        if not gid:
            return None, {}, {}
        return {"x": req.at.x, "y": req.at.y, "w": core.NODE_W, "h": core.NODE_H,
                "group": gid, "state": req.state, "anchor": None, "placedAt": today}, {}, {}
    box, gpatch, npatch = core.place_or_grow(nid, index, plain, today=today, gid=req.group)
    if box:
        box["state"] = req.state
    return box, gpatch, npatch


def place(vault: Path, req: PlaceRequest) -> PlaceResult:
    """把若干 Inbox 节点放上画布。逐个算，算完立刻并进工作副本，后面的才不会撞上。"""
    index, layout = load_pair(vault)
    plain = layout.model_dump()
    if req.group and req.group not in plain["groups"]:
        raise PlaceRejected(f"分组 `{req.group}` 不存在")
    if req.at is not None and len(req.ids) != 1:
        raise PlaceRejected("指定坐标时一次只能放一个节点")
    known = {n["id"] for n in index["nodes"] if not n.get("virtual")}
    today = dt.date.today().isoformat()

    placed, skipped, patch_nodes, patch_groups = [], [], {}, {}
    created: list[str] = []
    by_id = {n["id"]: n for n in index["nodes"]}
    for nid in req.ids:
        if nid not in known:
            skipped.append({"id": nid, "reason": "不在索引里（可能是还没有 md 文件的占位 stub）"})
            continue
        if nid in plain["nodes"]:
            skipped.append({"id": nid, "reason": "已经在画布上了"})
            continue
        one = req
        if req.create_field_group and not req.group and core.target_group(nid, index, plain) is None:
            gid = _open_field_group(by_id.get(nid, {}).get("field"), plain, patch_groups, created)
            if gid:
                one = req.model_copy(update={"group": gid})
        box, gpatch, npatch = _one_placement(nid, one, index, plain, today)
        if box is None:
            # **"放不下"和"判不出分组"要分开说**：前者要人去挪框，后者要人去填 layer /
            # 建域框。混成一句的话，人只能反复点「放进去」然后反复失败。
            target = one.group or core.target_group(nid, index, plain)
            reason = (core.growth_blocker(target, plain) if target
                      else f"判不出 `{nid}` 该进哪个分组——填一下它的 layer，或者用「建域框并放入」")
            skipped.append({"id": nid, "reason": reason or "目标分组放不下，留在 Inbox"})
            continue
        _merge_patch(plain, patch_groups, patch_nodes, gpatch, npatch)
        plain["nodes"][nid] = box                      # 并进工作副本：下一个节点会避开它
        patch_nodes[nid] = NodePatch(**box)
        placed.append(Placed(id=nid, x=box["x"], y=box["y"], group=box["group"],
                             state=box["state"], anchor=box.get("anchor")))

    if not patch_nodes:
        return PlaceResult(revision=layout.revision, placed=[], skipped=skipped)
    patch = LayoutPatch(base_revision=req.base_revision, nodes=patch_nodes,
                        groups=patch_groups or None)
    doc, _, _ = apply_patch(vault, patch, index)
    return PlaceResult(revision=doc.revision, placed=placed, skipped=skipped,
                       grown_groups=sorted(set(patch_groups) - set(created)), created_groups=created)


def _open_field_group(field: str | None, plain: dict, patch_groups: dict, created: list[str]) -> str | None:
    """判不出分组的节点：给它的领域开一个顶层框（接在整张图最下面），同一批里第二个同领域的直接复用。"""
    if not field:
        return None
    hit = next((gid for gid, g in plain["groups"].items() if not g.get("parent") and g.get("name") == field), None)
    if hit:
        return hit
    plan = core.plan_field_group(field, plain)
    if plan is None:
        return None
    gid, box = plan
    plain["groups"][gid] = box
    patch_groups[gid] = GroupPatch(**box)
    created.append(gid)
    return gid


def regroup(vault: Path, base_revision: int, only_draft: bool = True,
            ids: list[str] | None = None, create_lane: bool = False) -> PlaceResult:
    """把节点挪进它那一层的道。

    为什么需要它：`layer` 是后加的字段，早先建的点没有；一个连边都没有、又没分层的点
    只能落在 field 那个大框里，正好在所有泳道之外。等它补上 `layer` 之后，
    画布不会自己动——这个入口就是那一下"动"。

    两种用法：
    · 不给 `ids` = 批量扫一遍，**只动 draft**（设计文档 4.1：程序不动已定稿的东西）；
    · 给了 `ids` = 人在欠账清单上点了具体某个点，那就连定稿的也挪。
      "程序不动定稿"管的是**背着人的批量行为**，不是"人点了这一个"。

    `create_lane` 为真时，目标那条道不存在就现开一条（见 core.plan_new_lane）。
    默认关着：批量扫描时凭空长出几条道，会把人手排的画布搅乱。
    """
    index, layout = load_pair(vault)
    plain = layout.model_dump()
    by_id = {n["id"]: n for n in index["nodes"]}
    today = dt.date.today().isoformat()
    wanted = set(ids or [])

    moved, skipped, patch_nodes, patch_groups = [], [], {}, {}
    for nid, place in sorted(plain["nodes"].items()):
        if wanted and nid not in wanted:
            continue
        if not wanted and only_draft and place.get("state") != "draft":
            continue
        node = by_id.get(nid) or {}
        want = core.by_field_and_layer(node, plain)
        if not want or want == place.get("group"):
            continue
        # want 落在领域大框上 = 该去的那条道还没建。给了 create_lane 就现开一条。
        if create_lane and node.get("layer") and not plain["groups"].get(want, {}).get("parent"):
            plan = core.plan_new_lane(want, node["layer"], plain)
            if plan is None:
                skipped.append({"id": nid, "reason": f"「{node['layer']}」这条道开不出来"
                                                     f"（往下长会压到隔壁的域）：先自己拖点地方出来"})
                continue
            new_gid, lane, grown = plan
            plain["groups"][new_gid] = lane
            patch_groups[new_gid] = GroupPatch(**lane)
            for gid, h in grown.items():
                plain["groups"][gid]["h"] = h
                patch_groups[gid] = GroupPatch(**{**patch_groups.get(gid, GroupPatch()).model_dump(
                    exclude_none=True), "h": h})
            want = new_gid
        # 它想去的那条道还不存在时，by_field_and_layer 退回领域大框——
        # 那会把一个**已经待在某条道里**的点拽回大框，比原地不动更糟
        if plain["groups"].get(place.get("group"), {}).get("parent") == want:
            continue
        # 先把它从工作副本里摘掉，否则找空位时会被自己挡住
        stash = plain["nodes"].pop(nid)
        box, gpatch, npatch = core.place_or_grow(nid, index, plain, today=today, gid=want)
        if box is None:
            plain["nodes"][nid] = stash
            skipped.append({"id": nid, "reason": f"「{plain['groups'][want]['name']}」这条道塞不下了"
                                                  f"（旁边的点压过来了）：整张按层重排一次再试"})
            continue
        box["state"] = stash.get("state", "draft")     # 挪一下不改定稿状态
        _merge_patch(plain, patch_groups, patch_nodes, gpatch, npatch)
        plain["nodes"][nid] = box
        patch_nodes[nid] = NodePatch(**box)
        moved.append(Placed(id=nid, x=box["x"], y=box["y"], group=box["group"],
                            state=box["state"], anchor=box.get("anchor")))

    if not patch_nodes:
        return PlaceResult(revision=layout.revision, placed=[], skipped=skipped)
    patch = LayoutPatch(base_revision=base_revision, nodes=patch_nodes, groups=patch_groups or None)
    doc, _, _ = apply_patch(vault, patch, index)
    return PlaceResult(revision=doc.revision, placed=moved, skipped=skipped,
                       grown_groups=sorted(patch_groups))


def digest(vault: Path) -> dict:
    """图谱本身的欠账。错题归今日清单（coach_today）管，这里不重复开第二个出口。"""
    index, layout = load_pair(vault)
    return core.build_digest(vault, index, layout.model_dump())


REVIEW_KINDS = ("wrong", "due")      # 今日清单里属于"复习"的两类


def coach_today(vault: Path, project: str | None = None) -> CoachToday:
    """今日清单：错题 > 到期 > 未建 > 只有壳 > Inbox。纯排序，不调 LLM，不写任何文件。

    `project` 只过滤**建设项**：到期复习和错题是全局的——同一个大脑，
    不会因为今天在看别的项目就不用复习（重构方案 §1）。

    设置里关掉复习时，**错题和到期在这里就被摘掉**，不只是界面不画：
    今日清单同时是教练 `today` 工具的数据源，只在前端过滤的话，
    界面安静了、教练一调工具照样看见一串欠账，然后开口催。
    """
    index, layout = load_pair(vault)
    today = core.build_today(vault, index, layout.model_dump(),
                             core.load_projects(vault), project=project)
    if not core.review_on(vault):
        today["items"] = [it for it in today["items"] if it.get("kind") not in REVIEW_KINDS]
        counts = dict(today.get("counts") or {})
        for kind in REVIEW_KINDS:
            counts.pop(kind, None)
        today["counts"] = counts
        # 出题范围也要跟着空：`quiz_pools` 是在过滤之前按原始 items 算的，
        # 漏掉这一步就成了"行不显示、数字还在"——范围选择器上「今日」仍旧标着 6 个点，
        # 点下去还真能考出来，而界面上一条到期都看不见。
        pools = dict(today.get("pools") or {})
        pools["今日"] = []
        today["pools"] = pools
    return CoachToday(**today)


def review_due(vault: Path) -> dict:
    index = current_index(vault)
    items = core.due_nodes(index, core.load_log(vault))
    return {"due": items, "count": len(items), "generated_at": dt.date.today().isoformat()}


def mark_reviewed(vault: Path, node_id: str, grade: str = "记得") -> ReviewDone:
    index = current_index(vault)
    if not any(n["id"] == node_id and not n.get("virtual") for n in index["nodes"]):
        raise PlaceRejected(f"节点 `{node_id}` 不在索引里")
    entry = core.record_review(vault, node_id, grade=grade)
    return ReviewDone(id=node_id, reviews=len(entry["reviews"]), step=entry["step"],
                      lapses=entry["lapses"], next_due=entry.get("next_due"))


# ---------------------------------------------------------------- 变更预览（阶段 3 / 12 共用）

def diff_of(edit) -> str:
    """给人看的差异：改已有文件给统一 diff 片段；**新建文件直接给全文**。

    新文件的 diff 每一行都是 `+`，按片段截 60 行只会把正文后半截藏掉——
    对话建点的正文现在按骨架写足，卡片上必须能整篇看完再点「写入」。
    """
    if not edit.before:
        return edit.after
    lines = difflib.unified_diff(edit.before.splitlines(), edit.after.splitlines(),
                                 fromfile=f"a/{edit.rel}", tofile=f"b/{edit.rel}", lineterm="", n=2)
    return "\n".join(list(lines)[:60])


def preview(vault: Path, changes: list[dict], index: dict) -> list[FileDiff]:
    """算出这组变更会改成什么样，**不写盘**。

    `/api/changes` 的 dry_run 和对话里的「变更卡」用的是同一份：卡片上看到的 diff
    必须和真按下写入时写下去的一模一样，各算一遍迟早对不上。
    """
    edits = core.plan(vault, changes, index)
    return [FileDiff(path=e.rel, notes=e.notes, diff=diff_of(e)) for e in edits]
