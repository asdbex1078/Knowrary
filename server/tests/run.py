#!/usr/bin/env python3
"""服务层自测：.venv/bin/python server/tests/run.py [关键字]

每个用例在临时目录里搭一个最小 vault，用 TestClient 打真实路由。
覆盖阶段 2 验收点：打开就有图、拖动后刷新/重启位置不变、layout 修改不碰 Markdown、
旧 revision 提交被拒绝。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from server import index_service  # noqa: E402
from server.app import app  # noqa: E402
from server import assets as server_assets  # noqa: E402
from server.paths import core  # noqa: E402

CASES: list = []
_TMPDIRS: list[tempfile.TemporaryDirectory] = []


def case(fn):
    CASES.append(fn)
    return fn


def node_md(name: str, *, rels: str = "", extra: str = "") -> str:
    body = f"---\nname: {name}\nfield: 测试\ndesc: {name} 的摘要\n{extra}---\n# {name}\n\n正文\n"
    return body + (f"\n## 关系\n{rels}\n" if rels else "")


DEFAULT_FILES = {
    "nodes/组A/a.md": node_md("A", rels="- 部件:: [[b]]\n- 演化为:: [[c]] (2020)"),
    "nodes/组A/b.md": node_md("B", rels="- 对比:: [[c]]"),
    "nodes/组B/c.md": node_md("C", extra="year: 2020\n"),
}


def make_vault(files: dict[str, str] | None = None) -> Path:
    tmp = tempfile.TemporaryDirectory(prefix="knowrary-server-")
    _TMPDIRS.append(tmp)
    vault = Path(tmp.name)
    (vault / "relation-types.json").write_text((REPO / "relation-types.json").read_text("utf-8"), "utf-8")
    for rel, text in (files or DEFAULT_FILES).items():
        core.write(vault / rel, text)
    os.environ["KNOWRARY_VAULT"] = str(vault)
    index_service.invalidate()
    return vault


def client(files: dict[str, str] | None = None) -> tuple[TestClient, Path]:
    vault = make_vault(files)
    return TestClient(app), vault


def md_digest(vault: Path) -> str:
    """vault 里所有 md 的内容指纹——用来证明 layout 操作没碰 Markdown。"""
    h = hashlib.sha256()
    for p in core.walk_md(vault):
        h.update(p.relative_to(vault).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def get_layout(c: TestClient) -> dict:
    r = c.get("/api/layout")
    assert r.status_code == 200, r.text
    return r.json()


def patch(c: TestClient, body: dict):
    return c.patch("/api/layout", json=body)


# ---------------------------------------------------------------- 用例

@case
def 首次打开自动生成初始布局():
    c, vault = client()
    assert not (vault / ".knowrary" / "layout.json").exists()
    data = get_layout(c)
    assert data["generated"] is True and data["layout"]["revision"] == 1
    groups, nodes = data["layout"]["groups"], data["layout"]["nodes"]
    assert set(nodes) == {"a", "b", "c"}, nodes
    assert "g-测试" in groups and groups["g-测试--组A"]["parent"] == "g-测试", groups
    assert all(n["state"] == "final" for n in nodes.values())
    assert (vault / ".knowrary" / "layout.json").exists()
    again = get_layout(c)
    assert again["generated"] is False and again["layout"]["revision"] == 1, "第二次不该重新生成"


@case
def 初始布局节点落在自己分组框内():
    c, _ = client()
    layout = get_layout(c)["layout"]
    for nid, n in layout["nodes"].items():
        g = layout["groups"][n["group"]]
        assert g["x"] <= n["x"] and g["y"] <= n["y"], (nid, "越界")
        assert n["x"] + n["w"] <= g["x"] + g["w"] and n["y"] + n["h"] <= g["y"] + g["h"], (nid, "越界")


@case
def 拖拽只发坐标其余字段保留():
    c, _ = client()
    before = get_layout(c)["layout"]
    node_before = before["nodes"]["a"]
    r = patch(c, {"base_revision": before["revision"], "nodes": {"a": {"x": 1234, "y": 567}}})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == before["revision"] + 1
    after = get_layout(c)["layout"]["nodes"]["a"]
    assert (after["x"], after["y"]) == (1234, 567), after
    for k in ("w", "h", "group", "state"):
        assert after[k] == node_before[k], (k, after[k], node_before[k])


@case
def 重启后位置保持():
    c, vault = client()
    rev = get_layout(c)["layout"]["revision"]
    patch(c, {"base_revision": rev, "nodes": {"b": {"x": 999, "y": 888}}})
    index_service.invalidate()
    fresh = TestClient(app)                       # 新进程等价：重新读盘
    node = get_layout(fresh)["layout"]["nodes"]["b"]
    assert (node["x"], node["y"]) == (999, 888), node
    on_disk = json.loads((vault / ".knowrary" / "layout.json").read_text("utf-8"))
    assert on_disk["nodes"]["b"]["x"] == 999


@case
def 旧_revision_被拒绝且不改文件():
    c, vault = client()
    rev = get_layout(c)["layout"]["revision"]
    assert patch(c, {"base_revision": rev, "nodes": {"a": {"x": 1, "y": 1}}}).status_code == 200
    before = (vault / ".knowrary" / "layout.json").read_text("utf-8")
    r = patch(c, {"base_revision": rev, "nodes": {"a": {"x": 2, "y": 2}}})
    assert r.status_code == 409, r.status_code
    detail = r.json()["detail"]
    assert detail["current_revision"] == rev + 1, detail
    assert (vault / ".knowrary" / "layout.json").read_text("utf-8") == before, "冲突时不该写盘"


@case
def layout_写入不碰_Markdown():
    c, vault = client()
    digest = md_digest(vault)
    rev = get_layout(c)["layout"]["revision"]
    patch(c, {"base_revision": rev, "nodes": {"a": {"x": 5, "y": 5}},
              "notes": [{"id": "nt1", "text": "便签", "x": 0, "y": 0}],
              "viewport": {"zoom": 1.5, "cx": 10, "cy": 20}})
    assert md_digest(vault) == digest, "layout 操作改动了 md"


@case
def 视口与列表整体替换():
    c, _ = client()
    rev = get_layout(c)["layout"]["revision"]
    patch(c, {"base_revision": rev, "viewport": {"zoom": 0.42, "cx": 1, "cy": 2},
              "refs": [{"id": "r1", "target": "c", "x": 10, "y": 10}]})
    layout = get_layout(c)["layout"]
    assert layout["viewport"]["zoom"] == 0.42, layout["viewport"]
    assert [r["id"] for r in layout["refs"]] == ["r1"]
    patch(c, {"base_revision": layout["revision"], "refs": []})
    assert get_layout(c)["layout"]["refs"] == []


@case
def 条目置_null_表示删除():
    c, _ = client()
    layout = get_layout(c)["layout"]
    r = patch(c, {"base_revision": layout["revision"], "nodes": {"a": None}})
    assert r.status_code == 200, r.text
    after = get_layout(c)
    assert "a" not in after["layout"]["nodes"]
    assert after["layout"]["nodes"].keys() == {"b", "c"}


@case
def 新增分组与节点():
    c, _ = client()
    layout = get_layout(c)["layout"]
    r = patch(c, {"base_revision": layout["revision"],
                  "groups": {"g-新": {"name": "新", "x": 0, "y": 3000, "w": 400, "h": 200}},
                  "nodes": {"a": {"x": 24, "y": 3044, "group": "g-新"}}})
    assert r.status_code == 200, r.text
    after = get_layout(c)["layout"]
    assert after["groups"]["g-新"]["collapsed"] is False        # 默认值补齐
    assert after["nodes"]["a"]["group"] == "g-新"


@case
def 非法补丁被拒绝():
    c, _ = client()
    rev = get_layout(c)["layout"]["revision"]
    bad = [
        ({"base_revision": rev, "nodes": {"新节点": {"x": 1}}}, "新增节点缺 y"),
        ({"base_revision": rev, "nodes": {"a": {"群组": "x"}}}, "未知字段"),
        ({"base_revision": rev, "nodes": {"a": {"group": "g-不存在"}}}, "引用不存在的分组"),
        ({"base_revision": rev, "groups": {"g-测试": {"parent": "g-测试"}}}, "自己当父分组"),
        ({"nodes": {"a": {"x": 1, "y": 1}}}, "缺 base_revision"),
    ]
    for body, why in bad:
        r = patch(c, body)
        assert r.status_code == 422, f"{why} 应被拒绝，实际 {r.status_code} {r.text[:120]}"


@case
def 孤立引用只报告不删除():
    c, vault = client()
    layout = get_layout(c)["layout"]
    patch(c, {"base_revision": layout["revision"],
              "nodes": {"幽灵": {"x": 10, "y": 10}},
              "edges": {"a->不存在#部件": {"vertices": [{"x": 1, "y": 2}]}},
              "images": [{"id": "im1", "file": "assets/无.png", "x": 0, "y": 0, "w": 10, "h": 10}]})
    data = get_layout(c)
    kinds = {o["kind"] for o in data["orphans"]}
    assert kinds == {"node", "edge", "image"}, data["orphans"]
    assert "幽灵" in data["layout"]["nodes"], "孤立记录被删掉了"


@case
def 删除_md_后节点变孤立但布局保留():
    c, vault = client()
    get_layout(c)
    (vault / "nodes/组B/c.md").unlink()
    index_service.invalidate()
    data = get_layout(c)
    assert [o["id"] for o in data["orphans"]] == ["c"], data["orphans"]
    assert "c" in data["layout"]["nodes"], "md 删了不代表可以丢用户布局"


@case
def layout_坏文件不被覆盖():
    c, vault = client()
    get_layout(c)
    path = vault / ".knowrary" / "layout.json"
    path.write_text("{ 半个 JSON", encoding="utf-8")
    r = c.get("/api/layout")
    assert r.status_code == 500, r.status_code
    assert path.read_text("utf-8") == "{ 半个 JSON", "坏文件被覆盖了"
    assert "保留" in r.json()["hint"]


@case
def index_接口符合契约():
    c, _ = client()
    data = c.get("/api/index").json()
    assert not core.validate_index(data), core.validate_index(data)
    assert data["stats"]["nodes"] == 3 and data["stats"]["edges"] == 3, data["stats"]
    fams = {f["name"] for f in data["families"]}
    assert {"结构", "依赖", "演化", "对照", "弱关联"} <= fams, fams


@case
def index_随_md_变化而更新():
    c, vault = client()
    assert c.get("/api/index").json()["stats"]["nodes"] == 3
    core.write(vault / "nodes/组B/d.md", node_md("D", rels="- 依赖:: [[c]]"))
    data = c.get("/api/index").json()
    assert data["stats"]["nodes"] == 4, data["stats"]
    assert any(e["id"] == "d->c#依赖" for e in data["edges"])


# ---------------------------------------------------------------- 阶段 3：节点详情与写回

@case
def 节点详情返回原文与出入边():
    c, _ = client()
    r = c.get("/api/node/a")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["raw"].startswith("---") and "## 关系" in data["raw"], data["raw"][:60]
    assert sorted(e["target"] for e in data["out"]) == ["b", "c"], data["out"]
    assert data["obsidian_uri"].startswith("obsidian://open?vault="), data["obsidian_uri"]
    assert c.get("/api/node/不存在").status_code == 404


@case
def 变更预览不写盘():
    c, vault = client()
    digest = md_digest(vault)
    rev = c.get("/api/index").json()["revision"]
    r = c.post("/api/changes", json={"base_revision": rev, "dry_run": True, "changes": [
        {"type": "add_edge", "source": "a", "relation": "相关", "target": "c"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False and body["files"][0]["notes"] == ["+ 相关:: [[c]]"], body
    assert "相关" in body["files"][0]["diff"], body["files"][0]["diff"]
    assert md_digest(vault) == digest, "预览阶段改了 md"


@case
def 确认后写回并备份():
    c, vault = client()
    rev = c.get("/api/index").json()["revision"]
    r = c.post("/api/changes", json={"base_revision": rev, "dry_run": False, "changes": [
        {"type": "add_edge", "source": "a", "relation": "相关", "target": "c"},
        {"type": "update_frontmatter", "source": "c", "fields": {"desc": "新摘要"}}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] is True and out["backup"], out
    assert "- 相关:: [[c]]" in (vault / "nodes/组A/a.md").read_text("utf-8")
    assert "desc: 新摘要" in (vault / "nodes/组B/c.md").read_text("utf-8")
    assert (vault / out["backup"] / "nodes/组A/a.md").exists(), "备份没生成"
    index = c.get("/api/index").json()
    assert any(e["id"] == "a->c#相关" for e in index["edges"]), "索引没更新"
    assert index["stats"]["errors"] == 0, index["errors"]


@case
def 旧_revision_与外部改动都被拒():
    c, vault = client()
    rev = c.get("/api/index").json()["revision"]
    add = {"type": "add_edge", "source": "a", "relation": "相关", "target": "c"}
    assert c.post("/api/changes", json={"base_revision": rev, "dry_run": False,
                                        "changes": [add]}).status_code == 200
    stale = c.post("/api/changes", json={"base_revision": rev, "dry_run": True, "changes": [add]})
    assert stale.status_code == 409, f"旧 revision 应被拒，实际 {stale.status_code}"
    # 模拟 Obsidian 外部改动：索引还没刷新时写回要报冲突
    fresh = c.get("/api/index").json()["revision"]
    path = vault / "nodes/组A/a.md"
    path.write_text(path.read_text("utf-8") + "\n外部追加\n", encoding="utf-8")
    conflict = c.post("/api/changes", json={"base_revision": fresh, "dry_run": False, "changes": [
        {"type": "add_edge", "source": "a", "relation": "参考", "target": "b"}]})
    # 两道防线都会给 409：索引服务发现 md 变了先报 revision 冲突，
    # 指纹检测是索引没来得及重建时的兜底（core 自测里单独覆盖）
    assert conflict.status_code == 409, conflict.status_code
    detail = str(conflict.json()["detail"])
    assert "被改过" in detail or "索引已更新" in detail, detail


@case
def 非法变更被拒且不写盘():
    c, vault = client()
    digest = md_digest(vault)
    rev = c.get("/api/index").json()["revision"]
    bad = [
        ({"type": "update_frontmatter", "source": "a", "fields": {"x": 1}}, "布局字段"),
        ({"type": "update_frontmatter", "source": "a", "fields": {"id": "别的"}}, "改 id"),
        ({"type": "remove_edge", "source": "a", "relation": "部件", "target": "不存在"}, "删不存在的边"),
        ({"type": "add_edge", "source": "不存在的节点", "relation": "相关", "target": "c"}, "源节点不存在"),
    ]
    for change, why in bad:
        r = c.post("/api/changes", json={"base_revision": rev, "dry_run": False, "changes": [change]})
        assert r.status_code == 422, f"{why} 应被拒绝，实际 {r.status_code}"
    assert md_digest(vault) == digest, "被拒的变更改了 md"


# ---------------------------------------------------------------- 阶段 4：Inbox / 放置 / Digest / 复习

def with_inbox_node() -> tuple[TestClient, Path, dict]:
    """先让布局生成好，再往 vault 里塞一个新节点——它就落在 Inbox 里。"""
    c, vault = client()
    layout = get_layout(c)
    core.write(vault / "nodes/组A/d.md", node_md("D", rels="- 部件:: [[a]]", extra="learned: 2026-09-01\n"))
    index_service.invalidate()
    return c, vault, layout


@case
def inbox_列出未上画布的节点并给出建议分组():
    c, _, layout = with_inbox_node()
    data = c.get("/api/inbox").json()
    ids = [i["id"] for i in data["items"]]
    assert ids == ["d"], ids
    item = data["items"][0]
    group_of_a = layout["layout"]["nodes"]["a"]["group"]
    assert item["suggested_group"] == group_of_a, item          # 唯一的邻居 a 在组A
    assert item["suggested_group_name"] and item["name"] == "D"


@case
def place_放进建议分组且不与已有节点重叠():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    layout = get_layout(c)
    r = c.post("/api/place", json={"base_revision": layout["layout"]["revision"], "ids": ["d"]})
    assert r.status_code == 200, r.text
    placed = r.json()["placed"]
    assert len(placed) == 1 and placed[0]["state"] == "draft" and placed[0]["anchor"] == "a", placed

    nodes = get_layout(c)["layout"]["nodes"]
    assert nodes["d"]["group"] == nodes["a"]["group"] and nodes["d"]["placedAt"], nodes["d"]
    box = nodes["d"]
    for nid, other in nodes.items():
        if nid == "d" or other.get("group") != box["group"]:
            continue
        overlap = (box["x"] < other["x"] + other["w"] and other["x"] < box["x"] + box["w"]
                   and box["y"] < other["y"] + other["h"] and other["y"] < box["y"] + box["h"])
        assert not overlap, f"d 压在 {nid} 上"
    assert md_digest(vault) == before, "放置改了 md"
    assert c.get("/api/inbox").json()["items"] == [], "放完还留在 Inbox"


@case
def place_指定坐标与分组时按人给的来():
    c, _, layout = with_inbox_node()
    gid = layout["layout"]["nodes"]["c"]["group"]          # 故意放到没有邻居的组B
    r = c.post("/api/place", json={"base_revision": layout["layout"]["revision"], "ids": ["d"],
                                   "group": gid, "at": {"x": 12.0, "y": 34.0}, "state": "final"})
    assert r.status_code == 200, r.text
    node = get_layout(c)["layout"]["nodes"]["d"]
    assert (node["x"], node["y"], node["group"], node["state"]) == (12.0, 34.0, gid, "final"), node


@case
def place_重复放置与未知节点被跳过而不是报错():
    c, _, layout = with_inbox_node()
    r = c.post("/api/place", json={"base_revision": layout["layout"]["revision"],
                                   "ids": ["a", "查无此人", "d"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["id"] for p in body["placed"]] == ["d"], body
    assert {s["id"] for s in body["skipped"]} == {"a", "查无此人"}, body


@case
def place_旧revision与不存在的分组被拒():
    c, _, layout = with_inbox_node()
    rev = layout["layout"]["revision"]
    assert c.post("/api/place", json={"base_revision": rev - 1, "ids": ["d"]}).status_code == 409
    assert c.post("/api/place", json={"base_revision": rev, "ids": ["d"], "group": "没有这个组"}).status_code == 422
    assert c.post("/api/place", json={"base_revision": rev, "ids": ["a", "d"],
                                      "at": {"x": 0, "y": 0}}).status_code == 422
    assert "d" not in get_layout(c)["layout"]["nodes"], "被拒的请求写进了 layout"


@case
def digest_汇总草稿与跨分组桥与重复候选():
    c, _, layout = with_inbox_node()
    c.post("/api/place", json={"base_revision": layout["layout"]["revision"], "ids": ["d"]})
    d = c.get("/api/digest").json()
    assert d["counts"]["inbox"] == 0 and d["counts"]["drafts"] == 1, d["counts"]
    assert d["drafts"][0]["id"] == "d" and d["drafts"][0]["stale"] is False, d["drafts"]
    bridges = [(b["from_name"], b["to_name"], b["count"]) for b in d["bridges"]]
    assert bridges, "组A 到 组B 有边，桥不该是空的"
    assert d["counts"]["due"] >= 1, d["counts"]          # d 的 learned 是 2026-09-01，早就该复习了


@case
def review_复习一次后到期日按间隔推进():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    due_ids = [x["id"] for x in c.get("/api/review/due").json()["due"]]
    assert "d" in due_ids, due_ids

    r = c.post("/api/review/d")
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["reviews"] == 1 and first["next_due"], first
    assert "d" not in [x["id"] for x in c.get("/api/review/due").json()["due"]], "复习完还在到期列表里"

    second = c.post("/api/review/d").json()
    assert second["reviews"] == 2, second
    assert second["next_due"] > first["next_due"], (first, second)   # 间隔 1 天 → 2 天
    assert md_digest(vault) == before, "复习改了 md"
    assert (vault / ".knowrary" / "review-log.json").exists()


@case
def review_未知节点404():
    c, _, _ = with_inbox_node()
    assert c.post("/api/review/查无此人").status_code == 404


# ---------------------------------------------------------------- 阶段 9：测验与三档反馈

def stub_llm(payload: str):
    """把 quiz 用的 LLM 换成固定回答。出题链路要能离线测，不能每跑一次测试就烧一次模型。"""
    from server import quiz as quiz_mod
    original = quiz_mod.ask
    quiz_mod.ask = lambda vault, role, prompt, op="?": payload
    return original


def restore_llm(original) -> None:
    from server import quiz as quiz_mod
    quiz_mod.ask = original


def q(stem: str, points: list, qtype: str = "回忆题") -> dict:
    return {"question": {"type": qtype, "stem": stem, "answer": "标准答案", "points": points, "hint": ""},
            "grade": "记得"}


@case
def quiz_出题丢弃不存在的考点():
    c, vault, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": "A 是什么", "answer": "甲", "points": ["a"], "hint": "想想正文"},
        {"type": "关系题", "stem": "A 和幽灵", "answer": "乙", "points": ["a", "并不存在的节点"]},
        {"type": "回忆题", "stem": "全是假考点", "answer": "丙", "points": ["也不存在"]},
        {"type": "回忆题", "stem": "", "answer": "空题干要丢掉", "points": ["a"]},
    ]}, ensure_ascii=False))
    try:
        r = c.post("/api/quiz", json={"node_ids": ["a", "b"], "count": 3})
        assert r.status_code == 200, r.text
        data = r.json()
        stems = [x["stem"] for x in data["questions"]]
        assert stems == ["A 是什么", "A 和幽灵"], stems          # 空题干、全假考点的两道被丢掉
        assert data["questions"][1]["points"] == ["a"], data["questions"][1]   # 假考点被剔除，题还留着
        assert any("并不存在的节点" in w for w in data["warnings"]), data["warnings"]
        assert data["index_revision"] > 0, data
    finally:
        restore_llm(original)


@case
def quiz_LLM乱答时返回空题集而不是500():
    c, _, _ = with_inbox_node()
    original = stub_llm("抱歉，我今天不太想出题。")
    try:
        r = c.post("/api/quiz", json={"node_ids": ["a"]})
        assert r.status_code == 200, r.text
        assert r.json()["questions"] == [], r.text
    finally:
        restore_llm(original)


@case
def quiz_跳过stub与不存在的节点():
    c, _, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": []}))
    try:
        data = c.post("/api/quiz", json={"node_ids": ["查无此人"]}).json()
        assert data["questions"] == [] and data["warnings"], data
    finally:
        restore_llm(original)


@case
def quiz_交卷推进复习并记错题且不碰md():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    r = c.post("/api/quiz/grade", json={"answers": [
        {**q("A 是什么", ["a"]), "grade": "忘了"},
        {**q("B 是什么", ["b"]), "grade": "记得"},
    ]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["wrong"] == ["a"], data
    rows = {x["id"]: x for x in data["reviewed"]}
    assert rows["a"]["step"] == 0 and rows["a"]["lapses"] == 1, rows["a"]     # 忘了 → 序号归 0
    assert rows["b"]["step"] == 1, rows["b"]                                  # 记得 → 序号 +1

    assert (vault / ".knowrary" / "quiz-log.json").exists()
    wrong = [i for i in c.get("/api/coach/today").json()["items"] if i["kind"] == "wrong"]
    assert [w["id"] for w in wrong] == ["a"], wrong                           # 错题本只收答错的
    assert wrong[0]["name"] == "A" and "错过 1 次" in wrong[0]["detail"], wrong[0]
    assert md_digest(vault) == before, "交卷改了 md"


@case
def quiz_同一节点一轮多题只按最差档记一次():
    c, vault, _ = with_inbox_node()
    c.post("/api/quiz/grade", json={"answers": [
        {**q("第一问", ["a"]), "grade": "记得"},
        {**q("第二问", ["a"]), "grade": "忘了"},
        {**q("第三问", ["a"]), "grade": "记得"},
    ]})
    log = json.loads((vault / ".knowrary" / "review-log.json").read_text("utf-8"))
    entry = log["nodes"]["a"]
    assert len(entry["reviews"]) == 1, entry        # 三道题只推进一次调度，序号不许跳三级
    assert entry["step"] == 0 and entry["lapses"] == 1, entry
    answers = json.loads((vault / ".knowrary" / "quiz-log.json").read_text("utf-8"))["answers"]
    assert len(answers) == 3, answers               # 但三道题的明细都要留档


@case
def quiz_整轮比对给出漏掉与记错():
    c, _, _ = with_inbox_node()
    original = stub_llm(json.dumps({"items": [
        {"n": 1, "missed": ["位置编码"], "wrong": ["把 A 说成了 B"],
         "suggested_grade": "模糊", "comment": "机制说到了，细节漏了"},
        {"n": 9, "missed": [], "suggested_grade": "记得"},       # 对不上号：丢弃
        {"n": "x", "suggested_grade": "记得"},                    # 编号非法：丢弃
    ]}, ensure_ascii=False))
    try:
        r = c.post("/api/quiz/diagnose", json={"answers": [
            {**q("A 是什么", ["a"]), "my_answer": "我记得它是……"},
            {**q("B 是什么", ["b"]), "my_answer": ""},
        ]})
        assert r.status_code == 200, r.text
        data = r.json()
        assert [x["n"] for x in data["items"]] == [1], data
        assert data["items"][0]["missed"] == ["位置编码"], data["items"][0]
        assert data["items"][0]["wrong"] == ["把 A 说成了 B"], data["items"][0]
        assert any("对不上号" in w for w in data["warnings"]), data["warnings"]
        assert any("只诊断了 1 / 2" in w for w in data["warnings"]), data["warnings"]
    finally:
        restore_llm(original)


@case
def quiz_诊断乱答时返回空而不是500():
    c, _, _ = with_inbox_node()
    original = stub_llm("今天不想批改。")
    try:
        r = c.post("/api/quiz/diagnose", json={"answers": [q("A 是什么", ["a"])]})
        assert r.status_code == 200 and r.json()["items"] == [], r.text
    finally:
        restore_llm(original)


@case
def quiz_我的作答与诊断一起留档():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    c.post("/api/quiz/grade", json={"answers": [
        {**q("A 是什么", ["a"]), "grade": "模糊", "my_answer": "只记得一半",
         "missed": ["位置编码"], "wrong_points": ["把 A 说成了 B"]},
    ]})
    rec = json.loads((vault / ".knowrary" / "quiz-log.json").read_text("utf-8"))["answers"][0]
    assert rec["my_answer"] == "只记得一半", rec
    assert rec["missed"] == ["位置编码"] and rec["wrong"] == ["把 A 说成了 B"], rec
    assert rec["answer"] == "标准答案", rec          # 标准答案与我的作答各存各的
    assert md_digest(vault) == before, "留档改了 md"


@case
def review_三档反馈走body():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    first = c.post("/api/review/d", json={"grade": "记得"}).json()
    assert first["step"] == 1, first
    fuzzy = c.post("/api/review/d", json={"grade": "模糊"}).json()
    assert fuzzy["step"] == 1, fuzzy                # 模糊：序号不动
    forgot = c.post("/api/review/d", json={"grade": "忘了"}).json()
    assert forgot["step"] == 0 and forgot["lapses"] == 1, forgot
    assert c.post("/api/review/d", json={"grade": "半懂"}).status_code == 422
    assert md_digest(vault) == before, "三档反馈改了 md"


# ---------------------------------------------------------------- 阶段 10：学习计划

def one_plan(points):
    return {"名字暂定": {"name": "大模型方向", "goal": "吃透 Transformer",
                         "stages": [{"name": "第一阶段", "points": points}]}}


@case
def plans_计划里的点可以指向还不存在的节点():
    c, vault, _ = with_inbox_node()
    r = c.put("/api/plans", json={"base_revision": 0, "plans": one_plan([
        {"id": "a", "name": "A", "why": "已经建好了"},
        {"id": "还没建的点", "name": "还没建的点", "why": "这正是计划的用途"},
    ])})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1, r.json()

    data = c.get("/api/plans").json()
    pts = data["progress"]["名字暂定"]["points"]
    assert pts["还没建的点"] == "未建", pts        # 图里没有 → 未建，而不是报错
    assert pts["a"] in ("学过", "已掌握", "待复习"), pts
    assert data["progress"]["名字暂定"]["total"] == 2, data["progress"]
    # 待建的点绝不能在 vault 里凭空生出空壳来
    assert not (vault / "nodes" / "_stubs" / "还没建的点.md").exists()
    assert not [p for p in vault.rglob("*.md") if "还没建的点" in p.name]


@case
def plans_stub节点算只有壳():
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/空壳.md",
               "---\nname: 空壳\nfield: 测试\nstatus: stub\ndesc: 还没写\n---\n# 空壳\n")
    index_service.invalidate()
    c.put("/api/plans", json={"base_revision": 0, "plans": one_plan([{"id": "空壳", "name": "空壳"}])})
    pts = c.get("/api/plans").json()["progress"]["名字暂定"]["points"]
    assert pts["空壳"] == "只有壳", pts


@case
def plans_旧revision被拒且不写盘():
    c, vault, _ = with_inbox_node()
    c.put("/api/plans", json={"base_revision": 0, "plans": one_plan([{"id": "a"}])})
    before = (vault / ".knowrary" / "plans.json").read_text("utf-8")
    r = c.put("/api/plans", json={"base_revision": 0, "plans": one_plan([{"id": "b"}])})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["current_revision"] == 1, r.json()
    assert (vault / ".knowrary" / "plans.json").read_text("utf-8") == before, "冲突后还是写了盘"


@case
def plans_非法输入被拒():
    c, _, _ = with_inbox_node()
    # 同一个计划里重复的知识点
    r = c.put("/api/plans", json={"base_revision": 0,
                                  "plans": one_plan([{"id": "a"}, {"id": "a"}])})
    assert r.status_code == 422 and "两次" in r.json()["detail"], r.text
    # id 里有空格 / 斜杠（它将来是 md 的文件名）
    for bad in ("有 空格", "a/b"):
        assert c.put("/api/plans", json={"base_revision": 0,
                                         "plans": one_plan([{"id": bad}])}).status_code == 422, bad


@case
def plans_编排不碰md也不碰layout():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    lay = c.get("/api/layout").json()["layout"]["revision"]
    c.put("/api/plans", json={"base_revision": 0, "plans": one_plan([{"id": "a"}, {"id": "没建的"}])})
    assert md_digest(vault) == before, "编排计划改了 md"
    assert c.get("/api/layout").json()["layout"]["revision"] == lay, "编排计划改了 layout"


@case
def plans_没有文件时返回空而不是报错():
    c, vault, _ = with_inbox_node()
    assert not (vault / ".knowrary" / "plans.json").exists()
    data = c.get("/api/plans").json()
    assert data["doc"]["plans"] == {} and data["doc"]["revision"] == 0, data


def stub_plan_llm(payload: str):
    from server import plans as plans_mod
    original = plans_mod.ask
    plans_mod.ask = lambda vault, role, prompt, op="?": payload
    return original


def restore_plan_llm(original) -> None:
    from server import plans as plans_mod
    plans_mod.ask = original


# ---------------------------------------------------------------- 合并重复节点

def dup_vault():
    """a 和 甲 讲同一件事：a 有边和记录，甲 也有自己的边，还有人指着 甲。"""
    files = {
        "nodes/组A/a.md": node_md("A", rels="- 部件:: [[b]]", extra="learned: 2026-09-01\n"),
        # 正文补充要写在 `## 关系` **之前**，否则它属于关系段而不是正文
        "nodes/组A/甲.md": ("---\nname: 甲\nfield: 测试\ndesc: 甲 的摘要\n---\n# 甲\n\n"
                           "这是甲的正文补充。\n\n## 关系\n- 部件:: [[b]]\n- 依赖:: [[c]]\n"),
        "nodes/组A/b.md": node_md("B", rels="- 相关:: [[甲]]"),
        "nodes/组B/c.md": node_md("C", extra="year: 2020\n"),
    }
    c = TestClient(app)
    vault = make_vault(files)
    return c, vault


@case
def merge_边与引用全并到保留的那张():
    c, vault = dup_vault()
    rev = c.get("/api/index").json()["revision"]
    prev = c.post("/api/merge", json={"keep_id": "a", "drop_id": "甲", "base_revision": rev}).json()
    imp = prev["impact"]
    assert prev["applied"] is False and (vault / "nodes/组A/甲.md").exists(), "预览不许动盘"
    moved = {(e["type"], e["target"]) for e in imp["moved_edges"] if e["source"] == "甲"}
    assert moved == {("依赖", "c")}, imp["moved_edges"]          # 部件→b 保留的那张已经有了
    assert any("已经有同样的边" in d for d in imp["dropped_edges"]), imp["dropped_edges"]
    assert imp["links"] == 1 and imp["body_chars"] > 0, imp      # b 指着 甲；甲 有正文要并

    r = c.post("/api/merge", json={"keep_id": "a", "drop_id": "甲", "base_revision": rev,
                                   "dry_run": False})
    assert r.status_code == 200 and r.json()["applied"], r.text

    assert not (vault / "nodes/组A/甲.md").exists()
    kept = (vault / "nodes/组A/a.md").read_text("utf-8")
    assert "## 并入自 甲" in kept and "这是甲的正文补充" in kept, kept   # 内容一个字都不能丢
    assert "- 依赖:: [[c]]" in kept, kept
    assert kept.count("- 部件:: [[b]]") == 1, "重复的边不该并成两条"
    assert "[[a]]" in (vault / "nodes/组A/b.md").read_text("utf-8")      # 别人的引用改指过来

    edges = {(e["source"], e["type"], e["target"]) for e in c.get("/api/index").json()["edges"]}
    assert ("a", "依赖", "c") in edges, edges
    assert not [e for e in edges if "甲" in e], edges


@case
def merge_记录合并且间隔取保守的那个():
    c, vault = dup_vault()
    c.post("/api/review/a", json={"grade": "记得"})
    for _ in range(3):
        c.post("/api/review/甲", json={"grade": "记得"})
    c.post("/api/quiz/grade", json={"answers": [
        {**q("问甲", ["甲", "a"]), "grade": "忘了"}]})           # 两个都是考点，并完要去重
    rev = c.get("/api/index").json()["revision"]
    c.post("/api/merge", json={"keep_id": "a", "drop_id": "甲", "base_revision": rev,
                               "dry_run": False})

    log = json.loads((vault / ".knowrary/review-log.json").read_text("utf-8"))["nodes"]
    assert "甲" not in log, log
    # a：1 次手动 + 1 次交卷；甲：3 次手动 + 1 次交卷（那道题两个考点各记一次）
    assert len(log["a"]["reviews"]) == 6, log["a"]                # 两边的复习历史都留着
    assert log["a"]["lapses"] == 2, log["a"]                      # 忘记次数相加
    assert log["a"]["step"] == 0, log["a"]                        # a 刚被判"忘了"归 0，取小的那个
    ans = json.loads((vault / ".knowrary/quiz-log.json").read_text("utf-8"))["answers"][0]
    assert ans["points"] == ["a"], ans                            # 同一条题里两个考点并成一个


@case
def merge_非法与冲突一律拒绝且不动盘():
    c, vault = dup_vault()
    rev = c.get("/api/index").json()["revision"]
    before = md_digest(vault)
    for keep, drop in (("a", "a"), ("a", "查无此人"), ("查无此人", "a")):
        r = c.post("/api/merge", json={"keep_id": keep, "drop_id": drop,
                                       "base_revision": rev, "dry_run": False})
        assert r.status_code == 422, (keep, drop, r.status_code)
    assert c.post("/api/merge", json={"keep_id": "a", "drop_id": "甲", "base_revision": 0,
                                      "dry_run": False}).status_code == 409
    assert md_digest(vault) == before, "被拒的合并动了文件"


@case
def merge_落盘前备份两边():
    c, vault = dup_vault()
    rev = c.get("/api/index").json()["revision"]
    c.post("/api/merge", json={"keep_id": "a", "drop_id": "甲", "base_revision": rev,
                               "dry_run": False})
    dirs = list((vault / ".knowrary" / "backup").glob("rename-*"))
    assert dirs, "没有留备份"
    kept = {str(p.relative_to(dirs[0])) for p in dirs[0].rglob("*") if p.is_file()}
    assert "nodes/组A/甲.md" in kept and "nodes/组A/a.md" in kept, kept   # 被删的和被改的都要留档


# ---------------------------------------------------------------- 重命名（改 id）

@case
def rename_把四类引用一起迁走():
    c, vault, _ = with_inbox_node()
    lay = c.get("/api/layout").json()["layout"]
    c.patch("/api/layout", json={"base_revision": lay["revision"],
                                 "edges": {"a->b#部件": {"vertices": [{"x": 1, "y": 2}], "router": None}}})
    c.post("/api/quiz/grade", json={"answers": [{**q("A 是什么", ["a"]), "grade": "忘了"}]})
    c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "p", "stages": [{"name": "一", "points": [{"id": "a", "name": "a"}]}]}}})

    rev = c.get("/api/index").json()["revision"]
    prev = c.post("/api/rename", json={"old_id": "a", "new_id": "甲", "base_revision": rev})
    imp = prev.json()["impact"]
    assert prev.json()["applied"] is False, "dry_run 不该落盘"
    assert imp["layout"] and imp["layout_edges"] == 1 and imp["reviews"] == 1, imp
    assert imp["quiz"] == 1 and imp["plans"] == ["p"], imp
    assert (vault / "nodes/组A/a.md").exists(), "预览阶段不许动文件"

    r = c.post("/api/rename", json={"old_id": "a", "new_id": "甲", "base_revision": rev,
                                    "dry_run": False})
    assert r.status_code == 200 and r.json()["applied"], r.text

    assert not (vault / "nodes/组A/a.md").exists() and (vault / "nodes/组A/甲.md").exists()
    assert "[[甲]]" in (vault / "nodes/组A/d.md").read_text("utf-8")      # 别人的 [[链接]] 跟着改
    layout = c.get("/api/layout").json()["layout"]
    assert "甲" in layout["nodes"] and "a" not in layout["nodes"], layout["nodes"].keys()
    assert "甲->b#部件" in layout["edges"], layout["edges"].keys()         # 边样式的 key 也要改
    assert json.loads((vault / ".knowrary/review-log.json").read_text("utf-8"))["nodes"].get("甲")
    ans = json.loads((vault / ".knowrary/quiz-log.json").read_text("utf-8"))["answers"][0]
    assert ans["points"] == ["甲"], ans
    pt = c.get("/api/plans").json()["doc"]["plans"]["p"]["stages"][0]["points"][0]
    assert pt["id"] == "甲" and pt["name"] == "甲", pt
    # 迁完画布上不该留下指向旧 id 的孤立记录
    assert not [o for o in c.get("/api/layout").json()["orphans"] if o.get("id") == "a"]


@case
def rename_非法与冲突一律拒绝且不动盘():
    c, vault, _ = with_inbox_node()
    rev = c.get("/api/index").json()["revision"]
    before = md_digest(vault)
    bad = [("a", "有 空格"), ("a", "x/y"), ("a", "b"), ("查无此人", "x"), ("a", "a")]
    for old, new in bad:
        r = c.post("/api/rename", json={"old_id": old, "new_id": new, "base_revision": rev,
                                        "dry_run": False})
        assert r.status_code == 422, (old, new, r.status_code, r.text[:120])
    assert md_digest(vault) == before, "被拒的改名动了文件"


@case
def rename_旧索引revision被拒():
    c, _, _ = with_inbox_node()
    r = c.post("/api/rename", json={"old_id": "a", "new_id": "甲", "base_revision": 0,
                                    "dry_run": False})
    assert r.status_code == 409 and r.json()["detail"]["current_revision"] > 0, r.text


@case
def rename_落盘前整份备份():
    c, vault, _ = with_inbox_node()
    rev = c.get("/api/index").json()["revision"]
    c.post("/api/rename", json={"old_id": "a", "new_id": "甲", "base_revision": rev, "dry_run": False})
    dirs = list((vault / ".knowrary" / "backup").glob("rename-*"))
    assert dirs, "没有留备份"
    kept = {str(p.relative_to(dirs[0])) for p in dirs[0].rglob("*") if p.is_file()}
    assert "nodes/组A/a.md" in kept, kept                     # 原文件按原路径原名留档
    assert ".knowrary/layout.json" in kept, kept              # 机器数据也要带上，否则位置找不回来


@case
def index_挪动文件后路径跟着更新():
    """改名和移动都不改 mtime、也不改文件数——指纹只看这两样的话，索引会一直指向旧路径。"""
    c, vault, _ = with_inbox_node()
    before = c.get("/api/node/a").json()
    assert before["path"] == "nodes/组A/a.md", before["path"]

    (vault / "nodes" / "新域").mkdir(parents=True, exist_ok=True)
    os.rename(vault / "nodes/组A/a.md", vault / "nodes/新域/a.md")
    # 故意不调 invalidate()：这正是用户在 Obsidian 里拖一下文件的情形，服务端只能靠指纹自己发现

    after = c.get("/api/node/a").json()
    assert after["path"] == "nodes/新域/a.md", after["path"]
    assert (vault / after["path"]).exists()
    assert after["id"] == before["id"], "只是换了目录，id 不该跟着变"


@case
def index_改文件名等于改id():
    """文件名就是 id。改名之后旧 id 消失、新 id 出现，指向旧 id 的边退化成虚拟 stub。"""
    c, vault, _ = with_inbox_node()
    os.rename(vault / "nodes/组A/b.md", vault / "nodes/组A/乙.md")
    index = c.get("/api/index").json()
    ids = {n["id"] for n in index["nodes"]}
    assert "乙" in ids and "b" in ids, ids          # 新 id 在；旧 id 因为 a 还指着它，成了虚拟 stub
    ghost = next(n for n in index["nodes"] if n["id"] == "b")
    assert ghost.get("virtual") and ghost.get("stub"), ghost
    assert c.get("/api/node/b").status_code == 404   # 虚拟 stub 没有 md 可读


@case
def create_node_能顺手建出新目录():
    """开一个新领域时 vault 里还没有那个文件夹，不能逼着人先去 Obsidian 手动建一个空目录。"""
    c, vault, _ = with_inbox_node()
    assert not (vault / "nodes" / "深度学习").exists()
    r = c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                     "dry_run": False, "changes": [{
        "type": "create_node", "source": "神经网络与反向传播",
        "path": "nodes/深度学习/神经网络与反向传播.md",
        "fields": {"name": "神经网络与反向传播", "field": "深度学习", "desc": "梯度怎么回流"}}]})
    assert r.status_code == 200, r.text
    assert (vault / "nodes/深度学习/神经网络与反向传播.md").exists()


@case
def create_node_同一批里就能给新节点连边():
    """否则"建一个节点顺便连几条边"得拆成两次请求，中间那一刻图上多一个孤岛。"""
    c, vault, _ = with_inbox_node()
    rev = c.get("/api/index").json()["revision"]
    r = c.post("/api/changes", json={"base_revision": rev, "dry_run": False, "changes": [
        {"type": "create_node", "source": "丙丁", "path": "nodes/组A/丙丁.md",
         "fields": {"name": "丙丁", "field": "测试", "desc": "新来的"}},
        {"type": "add_edge", "source": "丙丁", "relation": "部件", "target": "a"},
        {"type": "add_edge", "source": "丙丁", "relation": "对比", "target": "b"},
    ]})
    assert r.status_code == 200, r.text
    assert len(r.json()["files"]) == 1, "新建与连边要合并成对同一个文件的一次改动"
    text = (vault / "nodes/组A/丙丁.md").read_text("utf-8")
    assert "- 部件:: [[a]]" in text and "- 对比:: [[b]]" in text, text
    # 「对比」是对称关系，索引按 id 排序定向，可能存成 b → 丙丁，所以按无向对来比
    pairs = {frozenset((e["source"], e["target"])) for e in c.get("/api/index").json()["edges"]}
    assert frozenset(("丙丁", "a")) in pairs and frozenset(("丙丁", "b")) in pairs, pairs


@case
def create_node_越界路径仍然被拒():
    c, vault, _ = with_inbox_node()
    rev = c.get("/api/index").json()["revision"]
    for bad in ("跑出去.md", "nodes/../跑出去.md", "doc/技术文档/x.md"):
        r = c.post("/api/changes", json={"base_revision": rev, "dry_run": False, "changes": [{
            "type": "create_node", "source": "x", "path": bad,
            "fields": {"name": "x", "field": "测试", "desc": "d"}}]})
        assert r.status_code == 422, (bad, r.status_code)
    assert not (vault / "跑出去.md").exists() and not (vault.parent / "跑出去.md").exists()


@case
def plans_计划带领域且拆解会建议一个():
    c, _, _ = with_inbox_node()
    original = stub_plan_llm(json.dumps({"field": "深度学习", "stages": [
        {"name": "一", "points": [{"id": "自注意力"}]}]}, ensure_ascii=False))
    try:
        assert c.post("/api/plans/propose", json={"goal": "x"}).json()["suggested_field"] == "深度学习"
    finally:
        restore_plan_llm(original)
    r = c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "大模型方向", "field": "深度学习", "stages": []}}})
    assert r.status_code == 200, r.text
    assert c.get("/api/plans").json()["doc"]["plans"]["p"]["field"] == "深度学习"


@case
def plans_拆解把时间预算发给模型并排出阶段截止日():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import plans as plans_mod
    original = plans_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"field": "深度学习", "stages": [
            {"name": "一", "points": [{"id": "自注意力", "load": "重"}, {"id": "位置编码"}]},
            {"name": "二", "points": [{"id": "多头注意力", "load": "轻"}]}]}, ensure_ascii=False)

    plans_mod.ask = spy
    try:
        r = c.post("/api/plans/propose", json={"goal": "吃透 Transformer",
                                               "target_date": "2099-01-01", "weekly_hours": 14})
    finally:
        plans_mod.ask = original
    assert "2099-01-01" in seen["plan-propose"] and "14 小时" in seen["plan-propose"], seen["plan-propose"][:400]

    data = r.json()
    pts = [p for st in data["stages"] for p in st["points"]]
    assert [p["load"] for p in pts] == ["重", "中", "轻"], pts      # 没给 load 的落到默认档，不是报错
    assert data["schedule"]["total_hours"] == 8.5, data["schedule"]
    # 阶段带着排好的截止日回来——前端「采纳」直接把日期带进计划，不再一律 null
    days = [st["deadline"] for st in data["stages"]]
    assert all(days) and days[0] < days[1], days
    assert data["schedule"]["verdict"] == "充裕", data["schedule"]


@case
def plans_速学模式砍点且被砍的留痕():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import plans as plans_mod
    original = plans_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [{"id": "自注意力", "load": "重"}]}],
                           "dropped": [{"id": "位置编码", "why": "这次先不学"}]}, ensure_ascii=False)

    plans_mod.ask = spy
    try:
        std = c.post("/api/plans/propose", json={"goal": "x"}).json()
        fast = c.post("/api/plans/propose", json={"goal": "x", "mode": "速学",
                                                  "target_date": "2026-09-20"}).json()
    finally:
        plans_mod.ask = original
    assert "速学版" in seen["plan-propose-fast"], seen["plan-propose-fast"][-500:]
    assert "速学版" not in seen["plan-propose"]                    # 标准模式不带压缩块
    assert [p["id"] for p in fast["dropped"]] == ["位置编码"], fast["dropped"]
    assert std["dropped"] == [], std["dropped"]                  # 标准模式不收 dropped
    # op 分得开，用量账本才算得清"赶工"烧了多少（这里 ask 被替换掉了，记账不走真实路径）
    assert set(seen) == {"plan-propose", "plan-propose-fast"}, list(seen)


@case
def plans_领域是第三种口径而不是第二个功能():
    """「初始化一个领域的所有点」并进学习计划：换模板换文案，数据与链路完全共用。"""
    c, _, _ = with_inbox_node()
    seen = {}
    from server import plans as plans_mod
    original = plans_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"field": "深度学习", "stages": [
            {"name": "注意力结构", "points": [{"id": "自注意力", "load": "重"}]}]}, ensure_ascii=False)

    plans_mod.ask = spy
    try:
        r = c.post("/api/plans/propose", json={"goal": "大模型推理", "kind": "领域"})
    finally:
        plans_mod.ask = original
    assert list(seen) == ["plan-map"], list(seen)                  # 单独记账，也用单独的模板
    assert "这是地图，不是学习路线" in seen["plan-map"], seen["plan-map"][:300]
    assert r.json()["stages"][0]["name"] == "注意力结构"

    # 存下来的仍然是同一份 plans.json，进度和时间账一个都不少
    body = {"base_revision": 0, "plans": {"m": {"name": "大模型地图", "kind": "领域", "stages": [
        {"name": "注意力结构", "points": [{"id": "自注意力", "load": "重"}, {"id": "a"}]}]}}}
    assert c.put("/api/plans", json=body).status_code == 200
    got = c.get("/api/plans").json()
    assert got["doc"]["plans"]["m"]["kind"] == "领域"
    assert got["progress"]["m"]["total"] == 2 and got["schedules"]["m"]["total_hours"] == 7.5, got


@case
def plans_拆解知道这份计划里已经有什么():
    """不喂已有的点，模型就会把同一个目标再拆一遍近义词——靠 id 去重是拦不住的。"""
    c, _, _ = with_inbox_node()
    seen = {}
    from server import plans as plans_mod
    original = plans_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [
            {"id": "自注意力"}, {"id": "位置编码"}]}]}, ensure_ascii=False)

    plans_mod.ask = spy
    try:
        r = c.post("/api/plans/propose", json={"goal": "x", "known_points": ["自注意力（自注意力机制）"]})
    finally:
        plans_mod.ask = original
    assert "自注意力（自注意力机制）" in seen["plan-propose"], seen["plan-propose"][:400]
    # 同名的仍然被标出来：面板上默认划掉，不用人一个个点
    assert r.json()["duplicates"] == ["自注意力"], r.json()["duplicates"]


@case
def plans_时间账随进度现算且不落盘():
    c, vault, _ = with_inbox_node()
    body = {"base_revision": 0, "plans": {"p": {
        "name": "大模型方向", "target_date": "2099-01-01", "weekly_hours": 7,
        "stages": [{"name": "一", "deadline": "2020-01-01",
                    "points": [{"id": "a", "load": "重"}, {"id": "没建的"}]}]}}}
    r = c.put("/api/plans", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()["schedules"]["p"]
    assert saved["total_hours"] == 7.5 and saved["remaining_hours"] == 2.5, saved   # a 已经建出来了
    assert saved["behind"] == 1, saved                            # 逾期阶段里「没建的」还欠着
    read = c.get("/api/plans").json()
    assert read["schedules"]["p"] == saved, (read["schedules"]["p"], saved)
    # 派生结果不落盘：plans.json 里只有编排，没有小时数也没有 behind
    raw = json.loads((vault / ".knowrary" / "plans.json").read_text("utf-8"))
    assert "schedules" not in raw and "behind" not in json.dumps(raw), raw


@case
def plans_面试与学习用两套拆解口径():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import plans as plans_mod
    original = plans_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"field": "Java面试", "stages": [
            {"name": "第一轮：八股必问", "points": [{"id": "垃圾回收器选择", "why": "几乎必问"}]}]},
            ensure_ascii=False)

    plans_mod.ask = spy
    try:
        c.post("/api/plans/propose", json={"goal": "吃透 Transformer", "coach": "大模型"})
        c.post("/api/plans/propose", json={"goal": "JD 原文", "kind": "面试", "coach": "Java 后端开发"})
    finally:
        plans_mod.ask = original

    assert set(seen) == {"plan-propose", "plan-interview"}, list(seen)   # op 分得开，账本才算得清
    assert "学习教练" in seen["plan-propose"] and "方向是大模型" in seen["plan-propose"]
    assert "面试官" in seen["plan-interview"] and "Java 后端开发" in seen["plan-interview"]
    assert "会被问" in seen["plan-interview"], "面试口径要问「为什么会被问」，不是「依赖顺序」"


@case
def plans_教练侧写与类型存得住():
    c, _, _ = with_inbox_node()
    r = c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "Java 面试", "kind": "面试", "coach": "Java 后端开发",
        "field": "Java面试", "goal": "JD 原文", "stages": []}}})
    assert r.status_code == 200, r.text
    got = c.get("/api/plans").json()["doc"]["plans"]["p"]
    assert got["kind"] == "面试" and got["coach"] == "Java 后端开发", got
    # 类型只有两种，别的一律拒
    assert c.put("/api/plans", json={"base_revision": 1, "plans": {"p": {
        "name": "x", "kind": "考研"}}}).status_code == 422


@case
def quiz_面试口径换题面与考法():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import quiz as quiz_mod
    original = quiz_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"questions": []})

    quiz_mod.ask = spy
    try:
        c.post("/api/quiz", json={"node_ids": ["a"]})
        c.post("/api/quiz", json={"node_ids": ["a"], "style": "面试", "coach": "Java 后端开发"})
    finally:
        quiz_mod.ask = original

    assert set(seen) == {"quiz", "quiz-interview"}, list(seen)
    assert "复习考官" in seen["quiz"] and "再考一次" in seen["quiz"]
    assert "面试官" in seen["quiz-interview"] and "Java 后端开发" in seen["quiz-interview"]
    assert "讲不讲得出来" in seen["quiz-interview"], "面试判据是能不能讲出来，不是记没记住"


@case
def plans_拆解走learn角色且只提议不落盘():
    c, vault, _ = with_inbox_node()
    seen = {}
    from server import plans as plans_mod
    original = plans_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen["role"] = role
        seen["prompt"] = prompt
        return json.dumps({"stages": [{"name": "第一阶段", "points": [
            {"id": "a", "name": "A", "why": "图里已经有"},
            {"id": "自注意力", "name": "自注意力", "why": "还得建"},
        ]}], "notes": "两个点"}, ensure_ascii=False)

    plans_mod.ask = spy
    try:
        before = md_digest(vault)
        r = c.post("/api/plans/propose", json={"goal": "吃透 Transformer"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert seen["role"] == "learn", seen["role"]          # 拆大纲是"生成"，不是 review 的审校
        assert "吃透 Transformer" in seen["prompt"], seen["prompt"][:200]
        assert "a" in seen["prompt"], "已有节点 id 要喂给模型，否则它会另起新名字"
        assert [p["id"] for p in data["stages"][0]["points"]] == ["a", "自注意力"], data
        assert data["existing"] == ["a"], data                # 标出哪些图里已经有
        # 只提议：既不写 plans.json，也不碰 md
        assert not (vault / ".knowrary" / "plans.json").exists()
        assert md_digest(vault) == before
    finally:
        plans_mod.ask = original


@case
def plans_拆解丢弃非法id与重复点():
    c, _, _ = with_inbox_node()
    original = stub_plan_llm(json.dumps({"stages": [
        {"name": "一", "points": [{"id": "自注意力"}, {"id": "有 空格"}, {"id": "a/b"}]},
        {"name": "二", "points": [{"id": "自注意力"}, {"id": "位置编码"}]},
        {"name": "三", "points": []},                          # 空阶段不生成
    ]}, ensure_ascii=False))
    try:
        data = c.post("/api/plans/propose", json={"goal": "x"}).json()
        got = [[p["id"] for p in st["points"]] for st in data["stages"]]
        assert got == [["自注意力"], ["位置编码"]], got
        assert sum("非法" in w for w in data["warnings"]) == 2, data["warnings"]
        assert any("重复" in w for w in data["warnings"]), data["warnings"]
    finally:
        restore_plan_llm(original)


@case
def plans_拆解乱答时返回空而不是500():
    c, _, _ = with_inbox_node()
    original = stub_plan_llm("我拒绝拆解。")
    try:
        r = c.post("/api/plans/propose", json={"goal": "x"})
        assert r.status_code == 200 and r.json()["stages"] == [], r.text
    finally:
        restore_plan_llm(original)


# ---------------------------------------------------------------- 模型用量账本

@case
def usage_成功的调用记进账本():
    c, vault, _ = with_inbox_node()
    from server import quiz as quiz_mod, llm_call
    original = quiz_mod.ask
    quiz_mod.ask = llm_call.ask                       # 走真正的记账口，只把底层 provider 换掉
    import llm_backend
    real = llm_backend.ask_detailed
    llm_backend.ask_detailed = lambda prompt, provider, m=None: (
        json.dumps({"questions": [{"stem": "x", "answer": "y", "points": ["a"]}]}),
        {"model": "假模型", "input_tokens": 120, "output_tokens": 30,
         "cache_read_tokens": 900, "cache_write_tokens": 10, "cost_usd": 0.0042})
    try:
        c.post("/api/quiz", json={"node_ids": ["a"]})
        u = c.get("/api/llm/usage").json()
        assert u["today"]["calls"] == 1 and u["today"]["errors"] == 0, u["today"]
        assert u["today"]["input_tokens"] == 120 and u["today"]["cost_usd"] == 0.0042, u["today"]
        assert u["today"]["cache_read_tokens"] == 900, u["today"]      # 缓存单列，成本大头常在这
        assert u["by_op"]["quiz"]["calls"] == 1, u["by_op"]
        assert u["recent"][0]["op"] == "quiz" and u["recent"][0]["model"] == "假模型", u["recent"][0]
        assert u["recent"][0]["ms"] >= 0, u["recent"][0]
    finally:
        llm_backend.ask_detailed = real
        quiz_mod.ask = original


@case
def usage_失败的调用也要记一笔():
    """调用失败照样烧了时间、也可能已经计费，账本上不能没有它。"""
    c, vault, _ = with_inbox_node()
    from server import quiz as quiz_mod, llm_call
    original = quiz_mod.ask
    quiz_mod.ask = llm_call.ask
    import llm_backend
    real = llm_backend.ask_detailed

    def boom(prompt, provider, m=None):
        raise SystemExit("LLM 连接失败")

    llm_backend.ask_detailed = boom
    try:
        raised = False
        try:
            c.post("/api/quiz", json={"node_ids": ["a"]})
        except SystemExit:
            raised = True
        u = c.get("/api/llm/usage").json()
        assert u["today"]["calls"] == 1 and u["today"]["errors"] == 1, (u["today"], raised)
        assert u["recent"][0]["ok"] is False and "连接失败" in u["recent"][0]["error"], u["recent"][0]
    finally:
        llm_backend.ask_detailed = real
        quiz_mod.ask = original


@case
def usage_没调用过时返回全零而不是报错():
    c, vault, _ = with_inbox_node()
    assert not (vault / ".knowrary" / "llm-usage.json").exists()
    u = c.get("/api/llm/usage").json()
    assert u["today"]["calls"] == 0 and u["totals"]["calls"] == 0 and u["recent"] == [], u
    assert u["roles"], "至少要报出当前角色用的是哪个 provider"


# ---------------------------------------------------------------- 阶段 10：今日清单

@case
def coach_空图也排得出清单():
    """计划不依赖图里先有节点——这正是它的用途，所以空计划 + 空复习也要有事可做。"""
    c, _, _ = with_inbox_node()
    c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "大模型方向", "daily_quota": 5,
        "stages": [{"name": "第一阶段", "points": [
            {"id": "自注意力", "why": "核心机制"}, {"id": "位置编码", "why": "顺序信息"}]}]}}})
    data = c.get("/api/coach/today").json()
    unbuilt = [i for i in data["items"] if i["kind"] == "unbuilt"]
    assert [i["id"] for i in unbuilt] == ["自注意力", "位置编码"], unbuilt
    assert unbuilt[0]["why"] == "核心机制" and unbuilt[0]["stage"] == "第一阶段", unbuilt[0]
    assert data["plans"][0]["stage"] == "第一阶段", data["plans"]
    assert data["plans"][0]["built"] == 0 and data["plans"][0]["total"] == 2, data["plans"]


@case
def coach_优先级错题在到期之前():
    c, vault, _ = with_inbox_node()
    c.post("/api/quiz/grade", json={"answers": [
        {**q("A 是什么", ["a"]), "grade": "忘了"}]})
    kinds = [i["kind"] for i in c.get("/api/coach/today").json()["items"]]
    assert kinds[0] == "wrong", kinds                  # 错题永远排最前
    assert "due" in kinds and kinds.index("wrong") < kinds.index("due"), kinds


@case
def coach_同一个点只出现一次():
    c, vault, _ = with_inbox_node()
    c.post("/api/quiz/grade", json={"answers": [{**q("A 是什么", ["a"]), "grade": "忘了"}]})
    # a 同时是错题、到期、还被写进计划——只能按最高优先级出现一次
    c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "p", "stages": [{"name": "一", "points": [{"id": "a"}, {"id": "没建的"}]}]}}})
    items = c.get("/api/coach/today").json()["items"]
    hits = [i for i in items if i["id"] == "a"]
    assert len(hits) == 1 and hits[0]["kind"] == "wrong", hits
    assert len({i["id"] for i in items}) == len(items), "清单里有重复的节点"


@case
def coach_只有壳排在未建之后且按配额截断():
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/空壳.md",
               "---\nname: 空壳\nfield: 测试\nstatus: stub\ndesc: 还没写\n---\n# 空壳\n")
    index_service.invalidate()
    c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "p", "daily_quota": 2,
        "stages": [{"name": "一", "points": [
            {"id": "空壳"}, {"id": "没建1"}, {"id": "没建2"}, {"id": "没建3"}]}]}}})
    picked = [(i["kind"], i["id"]) for i in c.get("/api/coach/today").json()["items"]
              if i["kind"] in ("unbuilt", "shell")]
    assert picked == [("unbuilt", "没建1"), ("unbuilt", "没建2")], picked   # 配额 2，未建优先


@case
def coach_建完的阶段就不再出现在清单里():
    c, vault, _ = with_inbox_node()
    c.put("/api/plans", json={"base_revision": 0, "plans": {"p": {
        "name": "p", "stages": [{"name": "一", "points": [{"id": "a"}, {"id": "b"}]}]}}})
    data = c.get("/api/coach/today").json()
    assert not [i for i in data["items"] if i["kind"] in ("unbuilt", "shell")], data["items"]
    assert data["plans"][0]["done"] is True and data["plans"][0]["stage"] == "", data["plans"]


@case
def coach_不写任何文件():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    lay = c.get("/api/layout").json()["layout"]["revision"]
    c.get("/api/coach/today")
    assert md_digest(vault) == before and c.get("/api/layout").json()["layout"]["revision"] == lay


# ---------------------------------------------------------------- 阶段 5：图片资源

PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                        "890000000a49444154789c6360000002000100fdff03fa0000000049454e44ae426082")


@case
def 图片上传后可列出可读取():
    c, vault = client()
    assert c.get("/api/assets").json()["items"] == []
    r = c.post("/api/asset/图-1.png", content=PNG_1PX)
    assert r.status_code == 200, r.text
    assert r.json()["file"] == "图-1.png" and r.json()["size"] == len(PNG_1PX), r.json()
    assert (vault / "assets" / "图-1.png").read_bytes() == PNG_1PX

    items = c.get("/api/assets").json()["items"]
    assert [i["file"] for i in items] == ["图-1.png"], items
    got = c.get(items[0]["url"])
    assert got.status_code == 200 and got.content == PNG_1PX
    assert got.headers["content-type"] == "image/png", got.headers


@case
def 图片重名与非图片与越界一律拒绝():
    c, vault = client()
    c.post("/api/asset/图-1.png", content=PNG_1PX)
    assert c.post("/api/asset/图-1.png", content=PNG_1PX).status_code == 422, "重名应被拒"
    assert c.post("/api/asset/图-1.png?overwrite=true", content=PNG_1PX).status_code == 200
    assert c.post("/api/asset/坏.txt", content=b"x").status_code == 422, "非图片应被拒"
    assert c.post("/api/asset/空.png", content=b"").status_code == 422, "空文件应被拒"
    for bad in ("../逃逸.png", "子目录/图.png", ".hidden.png"):
        try:
            server_assets.resolve(vault, bad)
            raise AssertionError(f"`{bad}` 应该被拒绝")
        except server_assets.AssetRejected:
            pass
    assert not list((vault / "assets").glob("*.txt")), "被拒的上传落了盘"


@case
def 图片位置进layout不碰md():
    c, vault = client()
    c.post("/api/asset/图-1.png", content=PNG_1PX)
    digest = md_digest(vault)
    layout = get_layout(c)["layout"]
    r = patch(c, {"base_revision": layout["revision"],
                  "images": [{"id": "im1", "file": "图-1.png", "x": 10, "y": 20, "w": 320, "h": 200}]})
    assert r.status_code == 200, r.text
    saved = get_layout(c)["layout"]["images"]
    assert saved[0]["file"] == "图-1.png" and saved[0]["w"] == 320, saved
    assert md_digest(vault) == digest, "贴图改了 md"


@case
def 边拐点存进layout并可删除():
    c, _ = client()
    layout = get_layout(c)["layout"]
    key = "a->b#部件"
    r = patch(c, {"base_revision": layout["revision"],
                  "edges": {key: {"vertices": [{"x": 100, "y": 200}], "router": "orth"}}})
    assert r.status_code == 200, r.text
    saved = get_layout(c)["layout"]["edges"]
    assert saved[key]["vertices"] == [{"x": 100.0, "y": 200.0}] and saved[key]["router"] == "orth", saved
    r = patch(c, {"base_revision": saved and get_layout(c)["layout"]["revision"], "edges": {key: None}})
    assert r.status_code == 200, r.text
    assert key not in get_layout(c)["layout"]["edges"], "删不掉手工拐点"


@case
def health_汇总可用():
    c, _ = client()
    h = c.get("/api/health").json()
    assert h["stats"]["nodes"] == 3 and h["layout_revision"] == 1, h
    assert h["index_revision"] >= 1


# ---------------------------------------------------------------- 阶段 12：对话式教练

def stub_chat(replies: list[str]):
    """按顺序吐回复的假模型。返回 (原函数, 收到的 messages 列表)。"""
    from server import chat as chat_mod
    original = chat_mod.llm_chat
    seen: list = []
    box = list(replies)

    def fake(vault, role, messages, op="chat", on_delta=None):
        seen.append([dict(m) for m in messages])
        text = box.pop(0) if box else "没话说了"
        if on_delta:
            on_delta(text)
        return text, {"input_tokens": 1, "output_tokens": 1}

    chat_mod.llm_chat = fake
    return original, seen


def restore_chat(original) -> None:
    from server import chat as chat_mod
    chat_mod.llm_chat = original


def tool_block(name: str, args: dict) -> str:
    return "我查一下。\n```knowrary\n" + json.dumps({"tool": name, "args": args}, ensure_ascii=False) + "\n```"


def sse_events(resp) -> list[dict]:
    return [json.loads(x[6:]) for x in resp.text.splitlines() if x.startswith("data: ")]


@case
def chat_工具结果回灌给模型且工具块不进人看的文本():
    c, _, _ = with_inbox_node()
    original, seen = stub_chat([tool_block("search_nodes", {"q": "A"}), "图里有 a 这个节点，它讲的是…"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "我图里有什么"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    kinds = [e["type"] for e in evs]
    assert kinds.count("tool") == 1 and kinds[-1] == "done", kinds
    assert [e for e in evs if e["type"] == "tool"][0]["name"] == "search_nodes"
    # 第二次调用时，工具结果已经作为一条 user 消息回灌进去了
    assert "[工具 search_nodes 的结果]" in seen[1][-1]["content"], seen[1][-1]
    done = evs[-1]
    assert "knowrary" not in done["text"] and "图里有 a" in done["text"], done["text"]


@case
def chat_复习判定只许降级():
    c, vault, _ = with_inbox_node()
    log_path = vault / ".knowrary" / "review-log.json"
    original, _ = stub_chat([tool_block("record_review", {"id": "a", "grade": "记得"}), "那你自己点一下"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "我懂了"}]})
    finally:
        restore_chat(original)
    assert not log_path.exists() or "a" not in log_path.read_text("utf-8"), "判「记得」竟然写了盘"

    original, _ = stub_chat([tool_block("record_review", {"id": "a", "grade": "忘了"}), "明天再考你"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "忘光了"}]})
    finally:
        restore_chat(original)
    assert [e for e in sse_events(r) if e["type"] == "review"], "判「忘了」没记上"
    assert json.loads(log_path.read_text("utf-8"))["nodes"]["a"]["lapses"] == 1


@case
def chat_入库只出卡不写md():
    c, vault, _ = with_inbox_node()
    before = {str(p): p.read_text("utf-8") for p in vault.rglob("*.md")}
    changes = [{"type": "add_edge", "source": "a", "relation": "相关", "target": "b"}]
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes}), "卡放你那儿了，点了才写"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "帮我存进去"}]})
    finally:
        restore_chat(original)
    cards = [e for e in sse_events(r) if e["type"] == "card"]
    assert len(cards) == 1 and cards[0]["card"]["files"], cards
    assert "相关" in json.dumps(cards[0]["card"], ensure_ascii=False)
    assert {str(p): p.read_text("utf-8") for p in vault.rglob("*.md")} == before, "对话竟然改了 md"


@case
def chat_留档一行一轮且不建索引():
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat(["a 是这样的…"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "讲讲 a"}]})
    finally:
        restore_chat(original)
    from server import chat as chat_mod
    path = chat_mod.chat_log_path(vault)
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
    assert [r["role"] for r in rows] == ["user", "assistant"], rows
    assert rows[1]["node_ids"] == ["a"], rows[1]            # 聊到哪些节点，按 id grep 回得来
    assert path.parent.name == "chat" and path.suffix == ".jsonl"


@case
def chat_连着调工具不会无限循环():
    c, _, _ = with_inbox_node()
    original, seen = stub_chat([tool_block("overview", {})] * 20)
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "看看"}]})
    finally:
        restore_chat(original)
    from server import chat as chat_mod
    assert len(seen) == chat_mod.MAX_STEPS, len(seen)
    assert "到此为止" in json.dumps(sse_events(r), ensure_ascii=False)


@case
def chat_同参工具不重复跑():
    """真实对话里模型会连着用一模一样的参数再搜一遍，每次都白烧一个来回。"""
    c, _, _ = with_inbox_node()
    calls = {"n": 0}
    from server import chat as chat_mod
    real = chat_mod.TOOLS["search_nodes"]

    def counted(vault, args):
        calls["n"] += 1
        return real(vault, args)

    chat_mod.TOOLS["search_nodes"] = counted
    original, _ = stub_chat([tool_block("search_nodes", {"q": "A"}),
                             tool_block("search_nodes", {"q": "A"}), "图里有 a"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "搜一下"}]})
    finally:
        restore_chat(original)
        chat_mod.TOOLS["search_nodes"] = real
    assert calls["n"] == 1, f"同样的参数跑了 {calls['n']} 遍"
    tools = [e for e in sse_events(r) if e["type"] == "tool"]
    assert len(tools) == 2 and "不再跑一遍" in tools[1]["summary"], tools


@case
def chat_最后一条必须是我说的话():
    c, _, _ = with_inbox_node()
    r = c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "在"}]})
    assert [e for e in sse_events(r) if e["type"] == "error"], r.text


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else ""
    picked = [c for c in CASES if keyword in c.__name__]
    failed = []
    for c in picked:
        name = c.__name__.replace("_", " ")
        try:
            c()
            print(f"  ✓ {name}")
        except Exception:
            failed.append(name)
            print(f"  ✗ {name}\n{traceback.format_exc()}")
    print(f"\n{len(picked) - len(failed)}/{len(picked)} 通过" + (f"，失败：{'、'.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
