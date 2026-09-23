#!/usr/bin/env python3
"""服务层自测：.venv/bin/python server/tests/run.py [关键字]

每个用例在临时目录里搭一个最小 vault，用 TestClient 打真实路由。
覆盖阶段 2 验收点：打开就有图、拖动后刷新/重启位置不变、layout 修改不碰 Markdown、
旧 revision 提交被拒绝。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# **第一件事：把用户级配置目录钉到临时位置。**
# 模型与密钥、复习开关现在都在 ~/.knowrary/（配置跟人不跟库），自测要是不隔离，
# 跑一遍测试就会把开发者自己的密钥和开关改掉——2026-09-22 真的发生过一次。
# 必须在 import server.* 之前设好：那边有模块在 import 期就解析路径。
_USER_HOME = tempfile.mkdtemp(prefix="knowrary-test-home-")
os.environ["KNOWRARY_HOME"] = _USER_HOME
os.environ.pop("KNOWRARY_LLM_CONFIG", None)

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


def node_md(name: str, *, rels: str = "", extra: str = "", field: str = "测试") -> str:
    body = f"---\nname: {name}\nfield: {field}\ndesc: {name} 的摘要\n{extra}---\n# {name}\n\n正文\n"
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
    (vault / "relation-types.json").write_text((REPO / "seed" / "relation-types.json").read_text("utf-8"), "utf-8")
    for rel, text in (files or DEFAULT_FILES).items():
        core.write(vault / rel, text)
    os.environ["KNOWRARY_VAULT"] = str(vault)
    # **每个用例一份干净的用户级目录**：模型密钥和复习开关都在那儿（配置跟人不跟库），
    # 共用一份的话，前一条关掉的复习会串到后一条——"今日"清单莫名其妙变空，查半天。
    os.environ["KNOWRARY_HOME"] = tempfile.mkdtemp(prefix="knowrary-home-")
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
def 节点详情的来源只有找得到原文时才给链接():
    """旧的 `source` 存的是文件名不是路径，所以能不能打开要找过一次才知道。

    两条都要有：找得到（认成原文、给 obsidian 链接）、找不到（外部来源，前端渲染成灰字而不是死链）。
    旧年代的原文散在 doc/ harness/ 里，第 5 步补旧账之前这条兜底还得在。
    """
    make_vault({
        "nodes/组A/有原文.md": node_md("有原文", extra="source: 某篇笔记.md\n"),
        "nodes/组A/图来的.md": node_md("图来的", extra="source: 一张图.jpg\n"),
        "nodes/组A/扔了的.md": node_md("扔了的", extra="source: 早就删了.md\n"),
        "doc/技术文档/某篇笔记.md": "# 某篇笔记\n\n正文\n",
    })
    c = TestClient(app)
    [got] = c.get("/api/node/有原文").json()["sources"]
    assert got["kind"] == "article" and got["path"] == "doc/技术文档/某篇笔记.md" and got["exists"], got
    assert got["uri"].startswith("obsidian://open?vault="), got

    # 不是 md：外部来源（那 61 个来自一张 jpg 的节点）
    [img] = c.get("/api/node/图来的").json()["sources"]
    assert img["kind"] == "external" and img["label"] == "一张图.jpg" and not img["uri"], img
    # 是 md 但哪儿都没有：也当外部来源，不能拼一个点不开的链接出来
    [gone] = c.get("/api/node/扔了的").json()["sources"]
    assert gone["kind"] == "external" and not gone["uri"], gone


@case
def sources_原文链接和外部来源混放_旧source合并进来_能反查():
    c, vault = client({
        "nodes/组A/甲.md": node_md("甲", extra='sources:\n  - "[[articles/BPE全景]]"\n  - 图灵《计算机器与智能》\n'),
        "nodes/组A/乙.md": node_md("乙", extra='sources: "[[articles/BPE全景#为什么是子词|全景]]"\nsource: 旧的.jpg\n'),
        "nodes/组A/丙.md": node_md("丙", extra='sources: ["[[articles/还没放进来]]"]\n'),
        "articles/BPE全景.md": "# BPE 全景\n\n原文\n",
    })
    nodes = {n["id"]: n for n in c.get("/api/index").json()["nodes"]}
    assert nodes["甲"]["sources"] == ["[[articles/BPE全景]]", "图灵《计算机器与智能》"], nodes["甲"]
    assert nodes["乙"]["sources"] == ["[[articles/BPE全景#为什么是子词|全景]]", "旧的.jpg"], "旧 source 没并进来"
    assert "source" not in nodes["乙"], "索引里只该有 sources 一份"

    art, ext = c.get("/api/node/甲").json()["sources"]
    assert art["kind"] == "article" and art["path"] == "articles/BPE全景.md" and art["exists"] and art["uri"], art
    assert ext["kind"] == "external" and ext["label"] == "图灵《计算机器与智能》", ext
    sec = c.get("/api/node/乙").json()["sources"][0]
    assert sec["section"] == "为什么是子词" and sec["label"] == "全景" and sec["path"] == "articles/BPE全景.md", sec
    dead = c.get("/api/node/丙").json()["sources"][0]
    assert dead["kind"] == "article" and not dead["exists"] and not dead["uri"], dead

    back = core.source_backlinks(vault, c.get("/api/index").json())
    assert sorted(back["articles/BPE全景.md"]) == ["乙", "甲"], back
    assert back["articles/还没放进来.md"] == ["丙"]
    # 原文不是节点：不上图
    assert "BPE全景" not in nodes


@case
def sources_写回收成列表_写了sources就撕掉旧source_不合法的拒():
    c, vault = client({"nodes/组A/甲.md": node_md("甲", extra="source: 旧文章.md\n")})
    r = write_changes(c, [{"type": "update_frontmatter", "source": "甲",
                           "fields": {"sources": [" [[articles/新]] ", "", "某本书", "某本书"]}}])
    assert r.status_code == 200 and r.json()["applied"], r.text
    fm, _ = core.split_frontmatter(core.read(vault / "nodes/组A/甲.md"))
    assert fm["sources"] == ["[[articles/新]]", "某本书"] and "source" not in fm, fm

    r = write_changes(c, [{"type": "create_node", "source": "乙", "path": "nodes/组A/乙.md",
                           "fields": {"name": "乙", "field": "测试", "desc": "摘要", "sources": "[[articles/新]]"}}])
    assert r.status_code == 200, r.text
    fm, _ = core.split_frontmatter(core.read(vault / "nodes/组A/乙.md"))
    assert fm["sources"] == ["[[articles/新]]"], fm

    r = write_changes(c, [{"type": "update_frontmatter", "source": "甲", "fields": {"sources": [{"x": 1}]}}])
    assert r.status_code == 422 and "字符串" in r.text, r.text


@case
def sources_链到不存在的原文_审核里提醒但不拦():
    c, vault = client({"nodes/组A/甲.md": node_md("甲"), "doc/老地方.md": "# 老\n",
                       "articles/在.md": "# 在\n"})
    r = write_changes(c, [{"type": "update_frontmatter", "source": "甲", "fields": {"sources": [
        "[[articles/不在]]", "[[doc/老地方]]", "[[articles/在]]", "某本书"]}}])
    assert r.status_code == 200 and r.json()["applied"], "只提醒不拦"
    codes = [(i["code"], i["message"]) for i in r.json()["audit"]["issues"]]
    assert [c_ for c_, _ in codes if c_ in ("dead_source", "source_outside")] == ["dead_source", "source_outside"], codes
    assert "articles/不在.md" in codes[[c_ for c_, _ in codes].index("dead_source")][1]


@case
def sources_同一来源的一整批孤点按每一项分组():
    c, _ = client({f"nodes/组A/{n}.md": node_md(n, extra='sources: ["[[articles/长文]]", "某本书"]\n')
                   for n in ("甲", "乙", "丙")})
    batches = {b["source"]: b for b in c.get("/api/digest").json()["lonely_batches"]}
    assert batches["[[articles/长文]]"]["lonely"] == 3 and batches["某本书"]["whole"], batches


@case
def vault_config_换原文目录时节点里的链接跟着改():
    """链接写的是完整路径（[[articles/…]]），搬家不改链接就全断了。"""
    c, vault = client({"nodes/组A/甲.md": node_md("甲", extra='sources: ["[[articles/长文]]", "某本书"]\n'),
                       "nodes/组A/乙.md": node_md("乙"),
                       "articles/长文.md": "# 长文\n"})
    before = core.read(vault / "nodes/组A/乙.md")
    r = c.put("/api/vault/config", json={"articles_dir": "原文", "move": True})
    assert r.status_code == 200, r.text
    fm, _ = core.split_frontmatter(core.read(vault / "nodes/组A/甲.md"))
    assert fm["sources"] == ["[[原文/长文]]", "某本书"], fm
    assert core.read(vault / "nodes/组A/乙.md") == before, "没链接的节点被碰了"
    snap = vault / r.json()["backup"]
    assert "[[articles/长文]]" in (snap / "nodes/组A/甲.md").read_text("utf-8"), "改链接之前没快照"
    [ref] = [x for x in c.get("/api/node/甲").json()["sources"] if x["kind"] == "article"]
    assert ref["exists"] and ref["path"] == "原文/长文.md", ref


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
    assert "links" in d and d["counts"]["links"] == len(d["links"]), d["counts"]


@case
def regroup_点名挪一个连定稿的也挪并现开一条道():
    """批量扫描只动草稿（程序不动已定稿的东西），但**人点了具体某个点**是另一回事：
    那条纪律管的是背着人的批量行为。道不存在时现开一条，只往下长，不动已有的框。
    """
    # 复现实盘那个顺序：**先有布局，再改 field**。布局按 field 建，所以改完之后
    # 节点还待在原来那个域里，而 md 说它属于另一个域。
    c, vault = client({"nodes/x/a.md": node_md("A", field="甲域"),
                       "nodes/y/b.md": node_md("B", field="乙域")})
    rev = get_layout(c)["layout"]["revision"]              # 先把布局生成出来
    # 给「乙域」下面腾点空：开新道只往下长，紧贴着的隔壁域会让它整体作废（下一条用例验那个）
    r = c.patch("/api/layout", json={"base_revision": rev, "groups": {"g-甲域": {"y": 1600}}})
    rev = r.json()["revision"]
    core.write(vault / "nodes/x/a.md", node_md("A", field="乙域", extra="layer: 理论\n"))
    index_service.invalidate()
    before = md_digest(vault)

    d = c.get("/api/digest").json()
    assert d["counts"]["misplaced"] >= 1, d["counts"]
    hit = next(m for m in d["misplaced"] if m["id"] == "a")

    r = c.post("/api/place/regroup", json={"base_revision": rev, "ids": ["a"], "create_lane": True})
    assert r.status_code == 200, r.text
    placed = r.json()["placed"]
    assert len(placed) == 1 and placed[0]["id"] == "a", r.json()
    assert placed[0]["state"] == "final", "挪一下不该把定稿改成草稿"
    assert c.get("/api/digest").json()["counts"]["misplaced"] == 0, "挪完还报不符"
    assert md_digest(vault) == before, "挪画布改了 md"


@case
def regroup_开道会压到隔壁的域就明说而不是硬挤():
    """宁可让人自己拖，也不能为了塞一条新道把旁边的域挤变形——但得说清楚为什么没挪。"""
    c, vault = client({"nodes/x/a.md": node_md("A", field="甲域"),
                       "nodes/y/b.md": node_md("B", field="乙域")})
    rev = get_layout(c)["layout"]["revision"]
    core.write(vault / "nodes/x/a.md", node_md("A", field="乙域", extra="layer: 理论\n"))
    index_service.invalidate()
    groups_before = set(get_layout(c)["layout"]["groups"])

    r = c.post("/api/place/regroup", json={"base_revision": rev, "ids": ["a"], "create_lane": True})
    assert r.status_code == 200, r.text
    assert not r.json()["placed"], r.json()
    assert "压到" in r.json()["skipped"][0]["reason"], r.json()["skipped"]
    assert set(get_layout(c)["layout"]["groups"]) == groups_before, "拒了还是建了半条道"


@case
def regroup_批量扫描不会凭空长出道来():
    """create_lane 默认关着：批量时凭空长出几条道，会把人手排的画布搅乱。"""
    c, vault = client({"nodes/x/a.md": node_md("A", field="甲域"),
                       "nodes/y/b.md": node_md("B", field="乙域")})
    rev = get_layout(c)["layout"]["revision"]
    core.write(vault / "nodes/x/a.md", node_md("A", field="乙域", extra="layer: 理论\n"))
    index_service.invalidate()
    groups_before = set(get_layout(c)["layout"]["groups"])
    r = c.post("/api/place/regroup", json={"base_revision": rev})
    assert r.status_code == 200, r.text
    assert set(get_layout(c)["layout"]["groups"]) == groups_before, "批量扫描长出了新分组"


@case
def digest_连边建议一路透到接口():
    """连边建议是前端「连边」按钮的唯一数据源，字段掉了按钮就没得点。"""
    c, _ = client({"nodes/组A/内存.md": node_md("内存"),
                   "nodes/组A/堆内存.md": node_md("堆内存")})
    links = c.get("/api/digest").json()["links"]
    hit = next((h for h in links if {h["source"], h["target"]} == {"内存", "堆内存"}), None)
    assert hit and hit["relation"] == "包含" and hit["source"] == "内存", links
    assert hit["lonely"] == 2 and hit["reason"], hit


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
    return {"question": {"type": qtype, "stem": stem, "ref_answer": "标准答案", "points": points, "hint": ""},
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
def quiz_没答完的卷子存得住并且交卷后清掉():
    c, vault, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": "A 是什么", "answer": "甲", "points": ["a"], "hint": ""},
    ]}, ensure_ascii=False))
    try:
        c.post("/api/quiz", json={"node_ids": ["a"], "level": "精通"})
    finally:
        restore_llm(original)
    # 出一次题是花了钱的：关掉对话框、刷新页面都不该让它蒸发
    saved = c.get("/api/quiz/open").json()["quiz"]
    assert saved and [x["stem"] for x in saved["questions"]] == ["A 是什么"], saved
    assert saved["level"] == "精通", saved

    c.post("/api/quiz/grade", json={"answers": [q("A 是什么", ["a"])]})
    assert c.get("/api/quiz/open").json()["quiz"] is None, "交完卷还留着没答完的卷子"


@case
def quiz_没答完的卷子能主动丢掉():
    c, vault, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": "A 是什么", "answer": "甲", "points": ["a"]}]}, ensure_ascii=False))
    try:
        c.post("/api/quiz", json={"node_ids": ["a"]})
    finally:
        restore_llm(original)
    assert c.get("/api/quiz/open").json()["quiz"]
    c.delete("/api/quiz/open")
    assert c.get("/api/quiz/open").json()["quiz"] is None


@case
def quiz_出不来题时留下模型原文而不是只说一句没出来():
    c, vault, _ = with_inbox_node()
    original = stub_llm("抱歉，我今天不太想出题。")
    try:
        data = c.post("/api/quiz", json={"node_ids": ["a"]}).json()
    finally:
        restore_llm(original)
    assert data["questions"] == []
    assert any("不太想出题" in w for w in data["warnings"]), data["warnings"]
    rows = [json.loads(x) for x in
            (vault / ".knowrary" / "issues.jsonl").read_text("utf-8").splitlines() if x.strip()]
    assert any("不太想出题" in json.dumps(r, ensure_ascii=False) for r in rows), rows
    assert c.get("/api/quiz/open").json()["quiz"] is None, "没出来题不该留一份空卷子"


@case
def quiz_难度档决定出题口径():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import quiz as quiz_mod
    original = quiz_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[len(seen)] = prompt
        return json.dumps({"questions": []})

    quiz_mod.ask = spy
    try:
        c.post("/api/quiz", json={"node_ids": ["a"], "level": "了解"})
        c.post("/api/quiz", json={"node_ids": ["a"], "level": "精通"})
        c.post("/api/quiz", json={"node_ids": ["a"]})          # 不填 = 默认档
    finally:
        quiz_mod.ask = original
    assert "只问定义、用途" in seen[0], seen[0][-600:]
    assert "经得起追问" in seen[1], seen[1][-600:]
    assert "讲得清机制" in seen[2], "不填难度时要落到默认档，而不是一段空白"


@case
def 模型在JSON前后多说了话也认():
    from server.llm_call import parse_json
    got = parse_json('好的，这是题目：\n```json\n{"questions": [{"stem": "带 { 的题面"}]}\n```\n还需要别的吗？')
    assert got["questions"][0]["stem"] == "带 { 的题面", got
    assert parse_json("完全不是 JSON") == {}


@case
def 题库_聊天问过的题攒起来再出题时直接用():
    c, vault, _ = with_inbox_node()
    # 聊天里教练问的那句检验题
    core.add_question(vault, "A 靠什么解决 X 问题？", ["a"], ref_answer="靠 Y", source="chat")
    core.add_question(vault, "a 靠什么解决 x 问题?", ["d"])        # 规整后同一道，只并考点
    pool = core.load_pool(vault)
    assert len(pool["questions"]) == 1, pool
    assert pool["questions"][0]["points"] == ["a", "d"], pool

    # 出题时直接用，压根不调模型
    from server import quiz as quiz_mod
    original = quiz_mod.ask
    quiz_mod.ask = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调模型"))
    try:
        data = c.post("/api/quiz", json={"node_ids": ["a"], "count": 1}).json()
    finally:
        quiz_mod.ask = original
    assert [q["stem"] for q in data["questions"]] == ["A 靠什么解决 X 问题？"], data
    assert any("没调模型" in w for w in data["warnings"]), data["warnings"]

    # 答对了打「学会」，答错了摘掉
    c.post("/api/quiz/grade", json={"answers": [
        {"question": {"type": "回忆题", "stem": "A 靠什么解决 X 问题？", "ref_answer": "靠 Y",
                      "points": ["a"], "hint": ""}, "grade": "记得"}]})
    assert core.load_pool(vault)["questions"][0]["learned"] is True
    c.post("/api/quiz/grade", json={"answers": [
        {"question": {"type": "回忆题", "stem": "A 靠什么解决 X 问题？", "ref_answer": "靠 Y",
                      "points": ["a"], "hint": ""}, "grade": "忘了"}]})
    row = core.load_pool(vault)["questions"][0]
    assert row["learned"] is False and row["asked"] == 2 and row["right"] == 1, row


@case
def 题库_没考点的题不收():
    c, vault, _ = with_inbox_node()
    assert core.add_question(vault, "一道没考点的题", []) is None
    assert core.load_pool(vault)["questions"] == []


@case
def chat_检验问题会被摘进题库且题干仍留在答案里():
    from server.chat import pull_checks
    text, checks = pull_checks(
        "核心差别是通用 vs 专用。\n\n```check\nNPU 牺牲了什么？\n考点: NPU, GPU\n```", ["兜底"])
    assert text.endswith("NPU 牺牲了什么？"), text      # 题干留在正文，摘掉的只是围栏
    assert "```" not in text
    assert checks == [{"stem": "NPU 牺牲了什么？", "points": ["NPU", "GPU"]}], checks
    # 没写考点就按这一轮提到过的点算
    _, c2 = pull_checks("```check\n只有题干\n```", ["a", "b"])
    assert c2[0]["points"] == ["a", "b"], c2


@case
def 归位_只动草稿并且只往已有泳道里挪():
    c, vault = client()
    # 造一个泳道式布局：领域「测试」下面两条道
    layout = {
        "schema_version": 1, "revision": 1,
        "groups": {
            "g-测试": {"name": "测试", "x": 0, "y": 0, "w": 300, "h": 400},
            "g-测试--硬件": {"name": "硬件", "parent": "g-测试", "x": 10, "y": 40, "w": 280, "h": 128},
            "g-测试--理论": {"name": "理论", "parent": "g-测试", "x": 10, "y": 168, "w": 280, "h": 128},
        },
        "nodes": {
            # 落在父框里的草稿：补了 layer，应该被挪进「硬件」
            "a": {"x": 20, "y": 320, "w": 168, "h": 52, "group": "g-测试", "state": "draft"},
            # 同样落在父框里，但是已定稿：不许动
            "b": {"x": 20, "y": 330, "w": 168, "h": 52, "group": "g-测试", "state": "final"},
        },
    }
    core.write_json_atomic(vault / ".knowrary" / "layout.json", layout)
    for nid, layer in (("a", "硬件"), ("b", "硬件")):
        core.write(vault / f"nodes/组A/{nid}.md",
                   f"---\nname: {nid}\nfield: 测试\nlayer: {layer}\ndesc: x\n---\n# {nid}\n\n## 关系\n")
    index_service.invalidate()

    rev = c.get("/api/layout").json()["layout"]["revision"]
    out = c.post("/api/place/regroup", json={"base_revision": rev}).json()
    assert [p["id"] for p in out["placed"]] == ["a"], out
    assert out["placed"][0]["group"] == "g-测试--硬件", out
    after = c.get("/api/layout").json()["layout"]
    assert after["nodes"]["b"]["group"] == "g-测试", "定稿的节点被挪了"
    assert after["nodes"]["a"]["state"] == "draft", "归位不该改状态"

    # 再来一次：已经各归各位，什么都不动
    again = c.post("/api/place/regroup", json={"base_revision": after["revision"]}).json()
    assert again["placed"] == [] and again["revision"] == after["revision"], again


@case
def 归位_想去的道还不存在时宁可别动它():
    """`by_field_and_layer` 找不到同名子框会退回领域大框；
    把一个已经待在某条道里的点拽回大框，比原地不动更糟。"""
    c, vault = client()
    core.write_json_atomic(vault / ".knowrary" / "layout.json", {
        "schema_version": 1, "revision": 1,
        "groups": {
            "g-测试": {"name": "测试", "x": 0, "y": 0, "w": 300, "h": 400},
            "g-测试--未分层": {"name": "未分层", "parent": "g-测试", "x": 10, "y": 40, "w": 280, "h": 128},
        },
        "nodes": {"a": {"x": 20, "y": 84, "w": 168, "h": 52, "group": "g-测试--未分层", "state": "draft"}},
    })
    core.write(vault / "nodes/组A/a.md",
               "---\nname: a\nfield: 测试\nlayer: 体系结构\ndesc: x\n---\n# a\n\n## 关系\n")
    index_service.invalidate()
    rev = c.get("/api/layout").json()["layout"]["revision"]
    out = c.post("/api/place/regroup", json={"base_revision": rev}).json()
    assert out["placed"] == [], out
    after = c.get("/api/layout").json()["layout"]
    assert after["nodes"]["a"]["group"] == "g-测试--未分层", "被拽回大框了"


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


def spy_llm() -> tuple[list, object]:
    """记下每次发给模型的 prompt，并固定返回空 JSON。返回 (收件箱, 原函数)。"""
    from server import quiz as quiz_mod
    original = quiz_mod.ask
    seen: list = []

    def spy(vault_, role, prompt, op="?"):
        seen.append(prompt)
        return "{}"

    quiz_mod.ask = spy
    return seen, original


@case
def quiz_批改按档位判分而不是一把尺子量到底():
    """出题认档位、批改不认的话，「了解就行」的点会被按「精通」的尺子判成模糊，白白多复习一轮。"""
    c, _, _ = with_inbox_node()
    seen, original = spy_llm()
    try:
        c.post("/api/quiz/diagnose", json={"answers": [q("A 是什么", ["a"])], "level": "了解"})
        c.post("/api/quiz/diagnose", json={"answers": [q("A 是什么", ["a"])], "level": "精通"})
    finally:
        restore_llm(original)
    assert "不要因为「没讲机制」" in seen[0], seen[0][:400]
    assert "经得起追问" in seen[1], seen[1][:400]


@case
def quiz_没传档位时按这份卷子出题用的那一档判():
    """前端不传也不能退回默认档：这份卷子按「了解」出的，就该按「了解」判。"""
    c, _, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": "A 是什么", "ref_answer": "甲", "points": ["a"]},
    ]}, ensure_ascii=False))
    try:
        c.post("/api/quiz", json={"node_ids": ["a"], "level": "了解"})   # 存下没交的卷子，带档位
    finally:
        restore_llm(original)

    seen, original = spy_llm()
    try:
        c.post("/api/quiz/diagnose", json={"answers": [q("A 是什么", ["a"])]})   # 不带 level
    finally:
        restore_llm(original)
    assert "不要因为「没讲机制」" in seen[0], "没传档位时要取卷子上的那一档，而不是退回默认档"


@case
def quiz_完整答案回灌题库但绝不碰标准答案():
    c, vault, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": "A 是什么", "ref_answer": "来自正文的标准答案", "points": ["a"]},
    ]}, ensure_ascii=False))
    try:
        c.post("/api/quiz", json={"node_ids": ["a"]})       # 出题：题连标准答案一起进池子
    finally:
        restore_llm(original)

    original = stub_llm(json.dumps({"items": [
        {"n": 1, "missed": [], "wrong": [], "suggested_grade": "记得", "comment": "没问题",
         "beyond": True, "next_gap": "按会用档还缺机制",
         "full_answer": "补全过的完整答案", "beyond_vault": ["笔记里没有的那个点"]},
    ]}, ensure_ascii=False))
    try:
        r = c.post("/api/quiz/diagnose", json={"answers": [
            {"question": {"type": "回忆题", "stem": "A 是什么", "ref_answer": "来自正文的标准答案",
                          "points": ["a"], "hint": ""},
             "grade": "记得", "my_answer": "我的作答"},
        ]})
        assert r.status_code == 200, r.text
        item = r.json()["items"][0]
        assert item["beyond"] is True and item["next_gap"], item
        assert item["full_answer"] == "补全过的完整答案", item
        assert item["beyond_vault"] == ["笔记里没有的那个点"], item
    finally:
        restore_llm(original)

    row = core.load_pool(vault)["questions"][0]
    assert row["ref_answer"] == "来自正文的标准答案", "标准答案来自 vault，批改绝不许覆盖它"
    assert row["full_answer"] == "补全过的完整答案", row
    assert row["beyond_vault"] == ["笔记里没有的那个点"], row
    assert row["full_src"] == "llm", "模型写的那一份要标出来，回看时得分得清是谁写的"


@case
def quiz_模型现编的题进题库下一轮不再调模型():
    """出题是花钱的：同一个节点考第二次还去现编，等于为同一件事付两次钱。"""
    c, vault, _ = with_inbox_node()
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": f"A 的第 {i} 问", "ref_answer": f"答案{i}", "points": ["a"]}
        for i in range(3)
    ]}, ensure_ascii=False))
    try:
        first = c.post("/api/quiz", json={"node_ids": ["a"], "count": 3}).json()
    finally:
        restore_llm(original)
    assert len(first["questions"]) == 3, first
    assert len(core.load_pool(vault)["questions"]) == 3, "现编的题要进池子"

    c.delete("/api/quiz/open")          # 丢掉没交的那份，模拟下一轮重新出题
    seen, original = spy_llm()
    try:
        again = c.post("/api/quiz", json={"node_ids": ["a"], "count": 3}).json()
    finally:
        restore_llm(original)
    assert seen == [], "池子里够数时不许再调模型"
    assert len(again["questions"]) == 3, again
    assert {x["stem"] for x in again["questions"]} == {f"A 的第 {i} 问" for i in range(3)}, again
    assert any("没调模型" in w for w in again["warnings"]), again["warnings"]


@case
def quiz_题库里已有的完整答案会带进下一轮批改():
    """不带的话，同一道题每轮被重写成另一个随机版本，答案永远长不好。"""
    c, vault, _ = with_inbox_node()
    core.add_question(vault, "A 是什么", ["a"], ref_answer="标准答案", source="quiz")
    core.enrich_questions(vault, [{"stem": "A 是什么", "full_answer": "上一轮攒下的完整答案",
                                   "beyond_vault": ["旧的点"]}])

    seen, original = spy_llm()
    try:
        c.post("/api/quiz/diagnose", json={"answers": [q("A 是什么", ["a"])]})
    finally:
        restore_llm(original)
    assert "上一轮攒下的完整答案" in seen[0], seen[0][-500:]


def q_no_answer(stem: str, points: list) -> dict:
    """聊天攒的题就长这样：只有题干和考点，没有标准答案。"""
    return {"question": {"type": "回忆题", "stem": stem, "ref_answer": "", "points": points, "hint": ""},
            "grade": "模糊", "my_answer": "我随便答了点"}


def with_fat_node() -> tuple[TestClient, Path]:
    """建一个正文有辨识度的节点，用来验证批改时到底带没带正文进去。"""
    c, vault = client()
    core.write(vault / "nodes/组A/e.md",
               "---\nname: E\nfield: 测试\ndesc: E 的摘要\n---\n# E\n\n拿破仑式的独门正文\n")
    index_service.invalidate()
    return c, vault


@case
def quiz_改名前的旧数据和旧键仍然认():
    """`answer` → `ref_answer` 这次改名不许让手上没答完的卷子作废、题库读不出来。"""
    c, vault = client()
    (vault / ".knowrary").mkdir(exist_ok=True)
    (vault / ".knowrary/quiz-open.json").write_text(json.dumps({
        "schema_version": 1, "questions": [
            {"type": "回忆题", "stem": "旧卷子里的题", "answer": "旧键存的标准答案",
             "points": ["a"], "hint": ""}],
        "index_revision": 1, "style": "复习", "level": "了解"}, ensure_ascii=False), "utf-8")
    (vault / ".knowrary/question-pool.json").write_text(json.dumps({
        "schema_version": 1, "questions": [
            {"id": "q1-x", "stem": "旧题库里的题", "answer": "旧键存的", "answer_src": "vault",
             "points": ["a"], "asked": 0, "learned": False}]}, ensure_ascii=False), "utf-8")

    got = c.get("/api/quiz/open").json()["quiz"]
    assert got["questions"][0]["ref_answer"] == "旧键存的标准答案", got
    assert "answer" not in got["questions"][0], "旧键要换掉，不能两个键并存"
    row = core.load_pool(vault)["questions"][0]
    assert row["ref_answer"] == "旧键存的" and row["ref_src"] == "vault", row

    # 模型偶尔会退回大众化的 `answer`，出题那头两个键都得认，否则白烧一次调用。
    # 换个没进过题库的节点问，否则会走「池子里够数就不调模型」那条短路，根本试不到解析。
    original = stub_llm(json.dumps({"questions": [
        {"type": "回忆题", "stem": "模型用了旧键", "answer": "照样要认", "points": ["b"]},
    ]}, ensure_ascii=False))
    try:
        c.delete("/api/quiz/open")
        data = c.post("/api/quiz", json={"node_ids": ["b"], "count": 1}).json()
    finally:
        restore_llm(original)
    assert data["questions"][0]["ref_answer"] == "照样要认", data


@case
def quiz_没有标准答案的题照笔记正文补一份():
    """聊天攒的题没有标准答案（pull_checks 只摘题干和考点）。判分总得有依据，
    而依据只能是我自己的笔记——所以批改时把正文带进去，让它照正文补出来。"""
    c, vault = with_fat_node()
    core.add_question(vault, "E 是什么", ["e"], source="chat")     # 聊天攒的：没有 answer
    assert core.load_pool(vault)["questions"][0]["ref_answer"] == ""

    seen, original = spy_llm()
    try:
        c.post("/api/quiz/diagnose", json={"answers": [q_no_answer("E 是什么", ["e"])]})
    finally:
        restore_llm(original)
    assert "## 考点的笔记正文" in seen[0], seen[0][:600]
    assert "拿破仑式的独门正文" in seen[0], "缺答案的题要把考点正文带进批改"
    assert "ref_answer" in seen[0]

    original = stub_llm(json.dumps({"items": [
        {"n": 1, "suggested_grade": "模糊", "comment": "差点意思",
         "ref_answer": "照正文补出来的标准答案", "full_answer": "模型自己发挥的完整答案"},
    ]}, ensure_ascii=False))
    try:
        item = c.post("/api/quiz/diagnose", json={
            "answers": [q_no_answer("E 是什么", ["e"])]}).json()["items"][0]
        assert item["ref_answer"] == "照正文补出来的标准答案", item
    finally:
        restore_llm(original)

    row = core.load_pool(vault)["questions"][0]
    assert row["ref_answer"] == "照正文补出来的标准答案", "补出来的那份要落进 ref_answer，下轮考才有答案可对"
    assert row["ref_src"] == "vault", "来源要标出来：它是照笔记正文补的，不是模型自由发挥的"
    assert row["full_answer"] == "模型自己发挥的完整答案", "两份各归各位，不许串"


@case
def quiz_已有标准答案的题批改不许覆盖():
    """answer 是判分依据。被模型顶掉一次，以后每轮都按模型的标准判我。"""
    c, vault = with_fat_node()
    core.add_question(vault, "E 是什么", ["e"], ref_answer="我笔记里原来那份", source="quiz")
    original = stub_llm(json.dumps({"items": [
        {"n": 1, "suggested_grade": "记得", "ref_answer": "模型想顶替的那份"},
    ]}, ensure_ascii=False))
    try:
        c.post("/api/quiz/diagnose", json={"answers": [
            {"question": {"type": "回忆题", "stem": "E 是什么", "ref_answer": "我笔记里原来那份",
                          "points": ["e"], "hint": ""},
             "grade": "记得", "my_answer": "答得不错"},
        ]})
    finally:
        restore_llm(original)
    assert core.load_pool(vault)["questions"][0]["ref_answer"] == "我笔记里原来那份"


@case
def quiz_考点没有md时补不出答案也不该崩():
    """stub 节点（只被别人链接过、还没建 md）没有正文可抄。补不出来最多是这题以后还没答案，
    不该因此连批改都做不成。"""
    c, vault = client()
    core.write(vault / "nodes/组A/f.md", node_md("F", rels="- 部件:: [[还没建的节点]]"))
    index_service.invalidate()
    original = stub_llm(json.dumps({"items": [
        {"n": 1, "suggested_grade": "忘了", "ref_answer": ""},
    ]}, ensure_ascii=False))
    try:
        r = c.post("/api/quiz/diagnose", json={
            "answers": [q_no_answer("那个还没建的节点是什么", ["还没建的节点"])]})
        assert r.status_code == 200, r.text
        assert r.json()["items"][0]["ref_answer"] == "", r.json()
    finally:
        restore_llm(original)


@case
def quiz_题都有标准答案时不把正文塞进批改():
    """正文在出题时已经读过一次了，再带一遍纯属白烧 token。"""
    c, vault = with_fat_node()
    seen, original = spy_llm()
    try:
        c.post("/api/quiz/diagnose", json={"answers": [q("E 是什么", ["e"])]})
    finally:
        restore_llm(original)
    assert "## 考点的笔记正文" not in seen[0], seen[0][:600]
    assert "拿破仑式的独门正文" not in seen[0]


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
    assert rec["ref_answer"] == "标准答案", rec      # 标准答案与我的作答各存各的
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

def one_project(points, **list_kw):
    """一个项目 + 一份清单。项目 id 只允许 ASCII（它会成为对话留档的目录名）。"""
    ls = {"kind": "学习", "name": "主线", "goal": "吃透 Transformer",
          "stages": [{"name": "第一阶段", "points": points}], **list_kw}
    return {"llm": {"name": "大模型方向", "lists": [ls]}}


@case
def projects_计划里的点可以指向还不存在的节点():
    c, vault, _ = with_inbox_node()
    r = c.put("/api/projects", json={"base_revision": 0, "projects": one_project([
        {"id": "a", "name": "A", "why": "已经建好了"},
        {"id": "还没建的点", "name": "还没建的点", "why": "这正是计划的用途"},
    ])})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1, r.json()

    data = c.get("/api/projects").json()
    pts = data["progress"]["llm"]["all"]["points"]
    assert pts["还没建的点"] == "未建", pts        # 图里没有 → 未建，而不是报错
    assert pts["a"] in ("学过", "已掌握", "待复习"), pts
    assert data["progress"]["llm"]["all"]["total"] == 2, data["progress"]
    # 待建的点绝不能在 vault 里凭空生出空壳来
    assert not (vault / "nodes" / "_stubs" / "还没建的点.md").exists()
    assert not [p for p in vault.rglob("*.md") if "还没建的点" in p.name]


@case
def projects_stub节点算只有壳():
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/空壳.md",
               "---\nname: 空壳\nfield: 测试\nstatus: stub\ndesc: 还没写\n---\n# 空壳\n")
    index_service.invalidate()
    c.put("/api/projects", json={"base_revision": 0, "projects": one_project([{"id": "空壳", "name": "空壳"}])})
    pts = c.get("/api/projects").json()["progress"]["llm"]["all"]["points"]
    assert pts["空壳"] == "只有壳", pts


@case
def projects_旧revision被拒且不写盘():
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": one_project([{"id": "a"}])})
    before = (vault / ".knowrary" / "projects.json").read_text("utf-8")
    r = c.put("/api/projects", json={"base_revision": 0, "projects": one_project([{"id": "b"}])})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["current_revision"] == 1, r.json()
    assert (vault / ".knowrary" / "projects.json").read_text("utf-8") == before, "冲突后还是写了盘"


@case
def projects_非法输入被拒():
    c, _, _ = with_inbox_node()
    # 同一个计划里重复的知识点
    r = c.put("/api/projects", json={"base_revision": 0,
                                  "projects": one_project([{"id": "a"}, {"id": "a"}])})
    assert r.status_code == 422 and "两次" in r.json()["detail"], r.text
    # id 里有空格 / 斜杠（它将来是 md 的文件名）
    for bad in ("有 空格", "a/b"):
        assert c.put("/api/projects", json={"base_revision": 0,
                                         "projects": one_project([{"id": bad}])}).status_code == 422, bad


@case
def projects_编排不碰md也不碰layout():
    c, vault, _ = with_inbox_node()
    before = md_digest(vault)
    lay = c.get("/api/layout").json()["layout"]["revision"]
    c.put("/api/projects", json={"base_revision": 0, "projects": one_project([{"id": "a"}, {"id": "没建的"}])})
    assert md_digest(vault) == before, "编排计划改了 md"
    assert c.get("/api/layout").json()["layout"]["revision"] == lay, "编排计划改了 layout"


@case
def projects_删掉项目时把它的画布挪进备份():
    """删项目原来只抹掉 projects.json 里的一行，`.knowrary/layouts/<项目>.json` 没人管。

    实盘上就这么留下了一个 `mha.json`（0 分组 0 节点），谁也想不起它是谁的。
    **挪走而不是删掉**：手工摆位是攒出来的成果，而删项目本来就可能是误点。
    """
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": one_project([{"id": "a"}])})
    canvas = vault / ".knowrary" / "layouts" / "llm.json"
    canvas.parent.mkdir(parents=True, exist_ok=True)
    canvas.write_text('{"schema_version": 2, "revision": 7}', encoding="utf-8")

    r = c.put("/api/projects", json={"base_revision": 1, "projects": {}})
    assert r.status_code == 200, r.text
    assert not canvas.exists(), "项目删了，画布还躺在 layouts/ 里"
    saved = list((vault / ".knowrary" / "backup").glob("*/layouts/llm.json"))
    assert len(saved) == 1 and '"revision": 7' in saved[0].read_text("utf-8"), saved


@case
def projects_改项目不会误伤自己的画布():
    """只有**消失的**项目才退役画布——改个名字、加个点不能把画布搬走。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": one_project([{"id": "a"}])})
    canvas = vault / ".knowrary" / "layouts" / "llm.json"
    canvas.parent.mkdir(parents=True, exist_ok=True)
    canvas.write_text('{"schema_version": 2, "revision": 7}', encoding="utf-8")

    r = c.put("/api/projects", json={"base_revision": 1,
                                     "projects": one_project([{"id": "a"}, {"id": "b"}])})
    assert r.status_code == 200, r.text
    assert canvas.exists(), "只是改了清单，画布不该被搬走"


@case
def projects_没有文件时返回空而不是报错():
    c, vault, _ = with_inbox_node()
    assert not (vault / ".knowrary" / "projects.json").exists()
    data = c.get("/api/projects").json()
    assert data["doc"]["projects"] == {} and data["doc"]["revision"] == 0, data


def stub_project_llm(payload: str):
    from server import projects as projects_mod
    original = projects_mod.ask
    projects_mod.ask = lambda vault, role, prompt, op="?": payload
    return original


def restore_project_llm(original) -> None:
    from server import projects as projects_mod
    projects_mod.ask = original


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
    dirs = list((vault / ".knowrary" / "backup").glob("merge-*"))
    assert dirs, "没有留备份"                                    # 目录名要认得出是哪种操作
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
    c.put("/api/projects", json={"base_revision": 0, "projects": {"p": {
        "name": "p", "lists": [{"stages": [{"name": "一", "points": [{"id": "a", "name": "a"}]}]}]}}})

    rev = c.get("/api/index").json()["revision"]
    prev = c.post("/api/rename", json={"old_id": "a", "new_id": "甲", "base_revision": rev})
    imp = prev.json()["impact"]
    assert prev.json()["applied"] is False, "dry_run 不该落盘"
    assert imp["layout"] and imp["layout_edges"] == 1 and imp["reviews"] == 1, imp
    assert imp["quiz"] == 1 and imp["projects"] == ["p"], imp
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
    pt = c.get("/api/projects").json()["doc"]["projects"]["p"]["lists"][0]["stages"][0]["points"][0]
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
def create_node_卡片上新文件给全文_正文不被截():
    """新文件的 diff 每行都是 +，按片段截 60 行会把正文后半截藏掉；
    正文现在按骨架写足，人要能在卡上整篇看完再点写入。"""
    c, vault, _ = with_inbox_node()
    body = "\n".join(f"第 {i} 行" for i in range(1, 91))
    r = c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                     "dry_run": True, "changes": [{
        "type": "create_node", "source": "长文", "path": "nodes/组A/长文.md",
        "fields": {"name": "长文", "field": "测试", "desc": "很长"}, "body": body}]})
    assert r.status_code == 200, r.text
    diff = r.json()["files"][0]["diff"]
    assert "第 90 行" in diff and diff.lstrip().startswith("---"), diff[:200]
    assert not (vault / "nodes/组A/长文.md").exists()


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
def projects_计划带领域且拆解会建议一个():
    c, _, _ = with_inbox_node()
    original = stub_project_llm(json.dumps({"field": "深度学习", "stages": [
        {"name": "一", "points": [{"id": "自注意力"}]}]}, ensure_ascii=False))
    try:
        assert c.post("/api/projects/propose", json={"goal": "x"}).json()["suggested_field"] == "深度学习"
    finally:
        restore_project_llm(original)
    r = c.put("/api/projects", json={"base_revision": 0, "projects": {"p": {
        "name": "大模型方向", "field": "深度学习", "lists": []}}})
    assert r.status_code == 200, r.text
    assert c.get("/api/projects").json()["doc"]["projects"]["p"]["field"] == "深度学习"


@case
def projects_拆解把时间预算发给模型并排出阶段截止日():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"field": "深度学习", "stages": [
            {"name": "一", "points": [{"id": "自注意力", "load": "重"}, {"id": "位置编码"}]},
            {"name": "二", "points": [{"id": "多头注意力", "load": "轻"}]}]}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        r = c.post("/api/projects/propose", json={"goal": "吃透 Transformer",
                                               "target_date": "2099-01-01", "weekly_hours": 14})
    finally:
        projects_mod.ask = original
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
def projects_速学模式砍点且被砍的留痕():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [{"id": "自注意力", "load": "重"}]}],
                           "dropped": [{"id": "位置编码", "why": "这次先不学"}]}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        std = c.post("/api/projects/propose", json={"goal": "x"}).json()
        fast = c.post("/api/projects/propose", json={"goal": "x", "mode": "速学",
                                                  "target_date": "2026-09-20"}).json()
    finally:
        projects_mod.ask = original
    assert "速学版" in seen["plan-propose-fast"], seen["plan-propose-fast"][-500:]
    assert "速学版" not in seen["plan-propose"]                    # 标准模式不带压缩块
    assert [p["id"] for p in fast["dropped"]] == ["位置编码"], fast["dropped"]
    assert std["dropped"] == [], std["dropped"]                  # 标准模式不收 dropped
    # op 分得开，用量账本才算得清"赶工"烧了多少（这里 ask 被替换掉了，记账不走真实路径）
    assert set(seen) == {"plan-propose", "plan-propose-fast"}, list(seen)


@case
def projects_领域是第三种口径而不是第二个功能():
    """「初始化一个领域的所有点」并进学习计划：换模板换文案，数据与链路完全共用。"""
    c, _, _ = with_inbox_node()
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"field": "深度学习", "stages": [
            {"name": "注意力结构", "points": [{"id": "自注意力", "load": "重"}]}]}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        r = c.post("/api/projects/propose", json={"goal": "大模型推理", "kind": "领域"})
    finally:
        projects_mod.ask = original
    assert list(seen) == ["plan-map"], list(seen)                  # 单独记账，也用单独的模板
    assert "这是地图，不是学习路线" in seen["plan-map"], seen["plan-map"][:300]
    assert r.json()["stages"][0]["name"] == "注意力结构"

    # 存下来的仍然是同一份 projects.json，进度和时间账一个都不少
    body = {"base_revision": 0, "projects": {"m": {"name": "大模型地图", "lists": [
        {"kind": "领域", "name": "全景",
         "stages": [{"name": "注意力结构", "points": [{"id": "自注意力", "load": "重"}, {"id": "a"}]}]}]}}}
    assert c.put("/api/projects", json=body).status_code == 200
    got = c.get("/api/projects").json()
    assert got["doc"]["projects"]["m"]["lists"][0]["kind"] == "领域"
    assert got["progress"]["m"]["all"]["total"] == 2, got["progress"]
    assert got["schedules"]["m"]["lists"][0]["total_hours"] == 7.5, got["schedules"]


@case
def projects_拆解知道别的项目已经列过哪些点():
    """建 MHA 项目时，「自注意力」大概率已经在 Transformer 项目里——
    不喂给模型就会每个项目各拆一遍近义词；标出来之后由人决定复用还是不列。"""
    c, _, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"transformer": {
        "name": "Transformer", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "自注意力"}, {"id": "位置编码"}]}]}]}}})
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [
            {"id": "自注意力"}, {"id": "多头注意力"}]}]}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        r = c.post("/api/projects/propose", json={"goal": "吃透多头注意力", "project": "mha"})
    finally:
        projects_mod.ask = original

    assert "别的项目已经列过的点" in seen["plan-propose"], seen["plan-propose"][:400]
    assert "自注意力（在Transformer里）" in seen["plan-propose"].replace(" ", ""), "没告诉模型哪个项目列过"
    # 提议回来时标出来——**只标不拦**，重叠是合法的
    got = r.json()["in_projects"]
    assert got == {"自注意力": ["Transformer"]}, got
    assert [p["id"] for st in r.json()["stages"] for p in st["points"]] == ["自注意力", "多头注意力"]

    # 拆给 transformer 自己时，不该把自己算成"别的项目"
    projects_mod.ask = spy
    try:
        r2 = c.post("/api/projects/propose", json={"goal": "x", "project": "transformer"})
    finally:
        projects_mod.ask = original
    assert r2.json()["in_projects"] == {}, r2.json()["in_projects"]


@case
def projects_拆解知道这份计划里已经有什么():
    """不喂已有的点，模型就会把同一个目标再拆一遍近义词——靠 id 去重是拦不住的。"""
    c, _, _ = with_inbox_node()
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [
            {"id": "自注意力"}, {"id": "位置编码"}]}]}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        r = c.post("/api/projects/propose", json={"goal": "x", "known_points": ["自注意力（自注意力机制）"]})
    finally:
        projects_mod.ask = original
    assert "自注意力（自注意力机制）" in seen["plan-propose"], seen["plan-propose"][:400]
    # 同名的仍然被标出来：面板上默认划掉，不用人一个个点
    assert r.json()["duplicates"] == ["自注意力"], r.json()["duplicates"]


@case
def projects_时间账随进度现算且不落盘():
    c, vault, _ = with_inbox_node()
    body = {"base_revision": 0, "projects": {"p": {
        "name": "大模型方向", "weekly_hours": 7, "lists": [
            {"kind": "学习", "name": "主线", "target_date": "2099-01-01",
             "stages": [{"name": "一", "deadline": "2020-01-01",
                         "points": [{"id": "a", "load": "重"}, {"id": "没建的"}]}]}]}}}
    r = c.put("/api/projects", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()["schedules"]["p"]["lists"][0]
    assert saved["total_hours"] == 7.5 and saved["remaining_hours"] == 2.5, saved   # a 已经建出来了
    assert saved["behind"] == 1, saved                            # 逾期阶段里「没建的」还欠着
    read = c.get("/api/projects").json()
    assert read["schedules"]["p"]["lists"][0] == saved, read["schedules"]["p"]
    # 派生结果不落盘：projects.json 里只有编排，没有小时数也没有 behind
    raw = json.loads((vault / ".knowrary" / "projects.json").read_text("utf-8"))
    assert "schedules" not in raw and "behind" not in json.dumps(raw), raw


@case
def projects_面试与学习用两套拆解口径():
    c, _, _ = with_inbox_node()
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"field": "Java面试", "stages": [
            {"name": "第一轮：八股必问", "points": [{"id": "垃圾回收器选择", "why": "几乎必问"}]}]},
            ensure_ascii=False)

    projects_mod.ask = spy
    try:
        c.post("/api/projects/propose", json={"goal": "吃透 Transformer", "coach": "大模型"})
        c.post("/api/projects/propose", json={"goal": "JD 原文", "kind": "面试", "coach": "Java 后端开发"})
    finally:
        projects_mod.ask = original

    assert set(seen) == {"plan-propose", "plan-interview"}, list(seen)   # op 分得开，账本才算得清
    assert "学习教练" in seen["plan-propose"] and "方向是大模型" in seen["plan-propose"]
    assert "面试官" in seen["plan-interview"] and "Java 后端开发" in seen["plan-interview"]
    assert "会被问" in seen["plan-interview"], "面试口径要问「为什么会被问」，不是「依赖顺序」"


@case
def projects_教练侧写与口径存在清单上():
    """kind 属于清单不属于项目：一个项目下可以同时有学习主线和面试清单。"""
    c, _, _ = with_inbox_node()
    r = c.put("/api/projects", json={"base_revision": 0, "projects": {"java": {
        "name": "Java 求职", "field": "Java", "lists": [
            {"kind": "学习", "name": "主线", "goal": "补并发"},
            {"kind": "面试", "name": "字节一面", "coach": "Java 后端开发",
             "field": "Java面试", "goal": "JD 原文"}]}}})
    assert r.status_code == 200, r.text
    got = c.get("/api/projects").json()["doc"]["projects"]["java"]
    assert [ls["kind"] for ls in got["lists"]] == ["学习", "面试"], got["lists"]
    assert got["lists"][1]["coach"] == "Java 后端开发"
    assert got["lists"][1]["field"] == "Java面试", "清单的领域要能和项目的分开"
    # 口径只有三种，别的一律拒
    assert c.put("/api/projects", json={"base_revision": 1, "projects": {"java": {
        "name": "x", "lists": [{"kind": "考研"}]}}}).status_code == 422
    # 项目 id 必须是 ASCII：它会成为对话留档的目录名
    assert c.put("/api/projects", json={"base_revision": 1, "projects": {"计组": {
        "name": "计组"}}}).status_code == 422


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
def projects_拆解走learn角色且只提议不落盘():
    c, vault, _ = with_inbox_node()
    seen = {}
    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen["role"] = role
        seen["prompt"] = prompt
        return json.dumps({"stages": [{"name": "第一阶段", "points": [
            {"id": "a", "name": "A", "why": "图里已经有"},
            {"id": "自注意力", "name": "自注意力", "why": "还得建"},
        ]}], "notes": "两个点"}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        before = md_digest(vault)
        r = c.post("/api/projects/propose", json={"goal": "吃透 Transformer"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert seen["role"] == "learn", seen["role"]          # 拆大纲是"生成"，不是 review 的审校
        assert "吃透 Transformer" in seen["prompt"], seen["prompt"][:200]
        assert "a" in seen["prompt"], "已有节点 id 要喂给模型，否则它会另起新名字"
        assert [p["id"] for p in data["stages"][0]["points"]] == ["a", "自注意力"], data
        assert data["existing"] == ["a"], data                # 标出哪些图里已经有
        # 只提议：既不写 plans.json，也不碰 md
        assert not (vault / ".knowrary" / "projects.json").exists()
        assert md_digest(vault) == before
    finally:
        projects_mod.ask = original


@case
def projects_拆解丢弃非法id与重复点():
    c, _, _ = with_inbox_node()
    original = stub_project_llm(json.dumps({"stages": [
        {"name": "一", "points": [{"id": "自注意力"}, {"id": "有 空格"}, {"id": "a/b"}]},
        {"name": "二", "points": [{"id": "自注意力"}, {"id": "位置编码"}]},
        {"name": "三", "points": []},                          # 空阶段不生成
    ]}, ensure_ascii=False))
    try:
        data = c.post("/api/projects/propose", json={"goal": "x"}).json()
        got = [[p["id"] for p in st["points"]] for st in data["stages"]]
        assert got == [["自注意力"], ["位置编码"]], got
        assert sum("非法" in w for w in data["warnings"]) == 2, data["warnings"]
        assert any("重复" in w for w in data["warnings"]), data["warnings"]
    finally:
        restore_project_llm(original)


@case
def projects_拆解顺手带回抽象层和年份_认不出的丢掉():
    """新建知识点时那两个下拉要预填，靠的就是这一次调用顺手多答的两个字段。

    单开一次调用去问 layer/year 也能做，但拆一份计划就要多烧 N 次；
    模型此刻正在逐个点地想"这是什么"，顺手答两个字段几乎不要钱。

    收得住才敢预填：层名不在七档里、年份写成 `1970s` 这种一律丢掉留空——
    填错的层会把点放进错的泳道，填错的年份会让它在历史视图上站错位置，
    两样都不报错，只会让图**悄悄**是错的。"""
    c, _, _ = with_inbox_node()
    seen = {}

    from server import projects as projects_mod
    original = projects_mod.ask

    def spy(vault, role, prompt, op="?"):
        seen["prompt"] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [
            {"id": "自注意力", "layer": "AI应用", "year": 2017},
            {"id": "反向传播", "layer": "AI應用", "year": "1986 年左右"},   # 层名拼错 + 年份不是数
            {"id": "位置编码", "layer": "", "year": None},                  # 模型自己说拿不准
        ]}]}, ensure_ascii=False)

    projects_mod.ask = spy
    try:
        points = c.post("/api/projects/propose", json={"goal": "x"}).json()["stages"][0]["points"]
    finally:
        projects_mod.ask = original

    got = {p["id"]: (p["layer"], p["year"]) for p in points}
    assert got["自注意力"] == ("AI应用", 2017), got
    assert got["反向传播"] == ("", None), got      # 认不出就留空，不硬塞
    assert got["位置编码"] == ("", None), got
    # 七档得写进提示词里，否则模型只能瞎猜一个层名，然后每一条都被上面那道校验丢掉
    for layer in core.LAYERS:
        assert layer in seen["prompt"], layer


@case
def suggest_顺手给抽象层和年份_已经填了的不再建议():
    """关系建议本来就要调一次模型，layer/year 搭这趟车走，不另开调用。

    它覆盖的是**不走计划建出来的点**（画布上右键新建的那些）——
    从计划进来的在拆解时就填好了。

    已经填了的一律不建议，而且**在服务端挡**：不指望模型看懂"已设置"四个字。
    放它过去的后果是检查器上永远挂着一条「建议改成你已经填的那个值」。"""
    c, vault, _ = with_inbox_node()
    from server import suggest as suggest_mod
    original = suggest_mod.ask
    answer = json.dumps({"edges": [], "duplicates": [],
                         "suggested_layer": "体系结构", "suggested_year": 1964}, ensure_ascii=False)
    suggest_mod.ask = lambda vault, role, prompt, op="?": answer
    try:
        got = c.post("/api/suggest", json={"node_id": "d"}).json()
        assert (got["suggested_layer"], got["suggested_year"]) == ("体系结构", 1964), got

        # 同一个点补上 layer / year 之后，同一份回答里这两条就该被吞掉
        core.write(vault / "nodes/组A/d.md",
                   node_md("D", rels="- 部件:: [[a]]", extra="learned: 2026-09-01\nlayer: 硬件\nyear: 1971\n"))
        index_service.invalidate()
        got = c.post("/api/suggest", json={"node_id": "d"}).json()
        assert got["suggested_layer"] is None and got["suggested_year"] is None, got
    finally:
        suggest_mod.ask = original


@case
def projects_拆解乱答时返回空而不是500():
    c, _, _ = with_inbox_node()
    original = stub_project_llm("我拒绝拆解。")
    try:
        r = c.post("/api/projects/propose", json={"goal": "x"})
        assert r.status_code == 200 and r.json()["stages"] == [], r.text
    finally:
        restore_project_llm(original)


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
        # 模型挂了要回 502 而不是让 SystemExit 逃到 ASGI 层——那样客户端只会看到
        # 连接莫名其妙断掉，服务端日志里横一段 asyncio 的 ExceptionGroup
        r = c.post("/api/quiz", json={"node_ids": ["a"]})
        assert r.status_code == 502, r.status_code
        assert "连接失败" in r.json()["detail"], r.json()
        u = c.get("/api/llm/usage").json()
        assert u["today"]["calls"] == 1 and u["today"]["errors"] == 1, u["today"]
        assert u["recent"][0]["ok"] is False and "连接失败" in u["recent"][0]["error"], u["recent"][0]
    finally:
        llm_backend.ask_detailed = real
        quiz_mod.ask = original


@case
def llm_挂掉时每条路由都回502而不是断连接():
    """`llm_backend` 是先有 CLI 后有服务的，报错一路用 `SystemExit`——命令行里那是对的。

    但 `SystemExit` 是 `BaseException`，Starlette 的异常中间件只接 `Exception`，
    于是它会**一路穿过请求处理层**：客户端拿到的不是"模型挂了"，而是连接莫名其妙断掉。
    所有调模型的路由共用 llm_call 这一个收口，在那里换成 `LLMFailed` 就够了。
    """
    import llm_backend
    c, _ = client({"nodes/组A/a.md": node_md("A")})
    real_ask, real_chat = llm_backend.ask_detailed, llm_backend.chat

    def boom(*a, **kw):
        raise SystemExit("LLM 连接失败")

    llm_backend.ask_detailed = boom
    llm_backend.chat = boom
    try:
        for path, body in (("/api/quiz", {"node_ids": ["a"]}),
                           ("/api/suggest", {"node_id": "a", "index_revision": 0}),
                           ("/api/years/propose", {}),
                           ("/api/quiz/diagnose", {"questions": [], "answers": []})):
            r = c.post(path, json=body)
            assert r.status_code in (422, 502), f"{path} -> {r.status_code}"
            if r.status_code == 502:
                assert "连接失败" in r.json()["detail"], (path, r.json())

        # 对话不走这条：SSE 已经开始往外吐字节了，只能在流里发一个 error 事件
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}]})
        assert [e for e in sse_events(r) if e["type"] == "error"], sse_events(r)
    finally:
        llm_backend.ask_detailed, llm_backend.chat = real_ask, real_chat


@case
def llm_报错里带着是哪个角色哪个provider():
    """配了多个 provider 时，"到底是谁炸了"应该一眼看见，而不是从 url 去猜。"""
    c, vault, _ = with_inbox_node()
    import llm_backend
    real = llm_backend.chat

    def boom(messages, provider, model_override=None, on_delta=None, tools=None, session=None):
        raise SystemExit("LLM 请求失败 HTTP 503（https://api.example.com/v1/chat/completions）："
                         "{\"error\":{\"code\":\"model_not_found\"}}")

    llm_backend.chat = boom
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}]})
    finally:
        llm_backend.chat = real
    err = [e for e in sse_events(r) if e["type"] == "error"]
    assert err, [e["type"] for e in sse_events(r)]
    msg = err[0]["message"]
    assert "learn 角色" in msg and "provider" in msg, msg
    assert "model_not_found" in msg, msg          # provider 原话要留着，别包掉
    # 失败也要记进用量账本：它一样烧了时间、也可能已经计费
    assert c.get("/api/llm/usage").json()["today"]["errors"] >= 1


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
    c.put("/api/projects", json={"base_revision": 0, "projects": {"p": {
        "name": "大模型方向", "daily_quota": 5, "lists": [{"kind": "学习", "name": "主线",
        "stages": [{"name": "第一阶段", "points": [
            {"id": "自注意力", "why": "核心机制"}, {"id": "位置编码", "why": "顺序信息"}]}]}]}}})
    data = c.get("/api/coach/today").json()
    unbuilt = [i for i in data["items"] if i["kind"] == "unbuilt"]
    assert [i["id"] for i in unbuilt] == ["自注意力", "位置编码"], unbuilt
    assert unbuilt[0]["why"] == "核心机制" and unbuilt[0]["stage"] == "第一阶段", unbuilt[0]
    assert data["projects"][0]["stage"] == "第一阶段", data["projects"]
    assert data["projects"][0]["built"] == 0 and data["projects"][0]["total"] == 2, data["projects"]


@case
def import_预览不落盘_落盘三种产物各归其位():
    """/api/import：导入方案 → 新建节点 / 补充老节点 / 待审边。默认只给 diff，dry_run=false 才写。"""
    c, vault = client()
    rev = c.get("/api/index").json()["revision"]
    plan = {"nodes": [{"id": "新点", "name": "新点", "desc": "d", "body": "## 描述\n正文",
                       "relations": [{"type": "依赖", "target": "a", "confidence": 0.9},
                                     {"type": "对比", "target": "b", "confidence": 0.3}]}],
            "enrich": [{"existing": "a", "content": "补给 A 的一段。"}], "summary": "s"}
    body = {"plan": plan, "field": "测试", "source": "某文章", "base_revision": rev}

    r = c.post("/api/import", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["applied"] is False and d["index_revision"] == rev
    assert sorted(f["path"] for f in d["files"]) == ["nodes/测试/新点.md", "nodes/组A/a.md"], d["files"]
    assert d["counts"] == {"nodes": 1, "stubs": 0, "enrich": 1, "edges": 1, "pending": 1}, d["counts"]
    assert [(p["relation"], p["target"]) for p in d["pending"]] == [("对比", "b")], d["pending"]
    a_diff = next(f["diff"] for f in d["files"] if f["path"].endswith("a.md"))
    assert "+> 补充自《某文章》" in a_diff and "+补给 A 的一段。" in a_diff, a_diff
    assert not (vault / "nodes/测试/新点.md").exists(), "预览不能落盘"
    assert not (vault / ".knowrary/pending.json").exists(), "预览不能记待审"

    r = c.post("/api/import", json={**body, "dry_run": False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["applied"] is True and d["backup"] and d["log"] and d["index_revision"] != rev, d
    assert (vault / "nodes/测试/新点.md").exists() and (vault / d["log"]).exists()
    a_text = (vault / "nodes/组A/a.md").read_text("utf-8")
    assert "补给 A 的一段。" in a_text.split("## 关系")[0] and "- 部件:: [[b]]" in a_text, a_text
    pend = json.loads((vault / ".knowrary/pending.json").read_text("utf-8"))
    assert [e["id"] for e in pend["edges"]] == ["新点->b#对比"] and pend["edges"][0]["confidence"] == 0.3, pend
    assert "新点" in {n["id"] for n in c.get("/api/index").json()["nodes"]}, "落盘后索引该看见新点"


@case
def import_propose_认领幽灵_近似撞名_孤立判定():
    """/api/import/propose：一次 LLM 出方案，服务端算三级匹配——明确 claims 的换成清单 id，
    名字很像但没认领的报近似撞名，一条边都没连到已有节点的标孤立。"""
    from server import importing as imp_mod
    c, vault = client()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"demo": {
        "name": "演示", "field": "测试", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "a"}, {"id": "RNN与长程依赖", "why": "痛点"},
                                      {"id": "注意力机制", "name": "注意力机制", "why": "前身"}]}]}]}}})
    payload = json.dumps({"nodes": [
        {"id": "注意力", "claims": "注意力机制", "name": "注意力", "desc": "d", "body": "## 描述\n见 [[RNN]]",
         "relations": [{"type": "依赖", "target": "a", "confidence": 0.9}, {"type": "部件", "target": "RNN"}]},
        {"id": "RNN", "name": "RNN", "desc": "循环网络", "relations": []},
        {"id": "孤零零", "name": "孤零零", "desc": "x", "relations": []},
    ], "suggest_home": {"isolated": ["孤零零"], "kind": "field", "name": "AI", "why": "讲的是模型"},
        "summary": "s"}, ensure_ascii=False)
    seen = {}
    original = imp_mod.ask
    def spy(vault, role, prompt, op="?"):
        seen.update(role=role, op=op, prompt=prompt)
        return payload
    imp_mod.ask = spy
    try:
        r = c.post("/api/import/propose", json={"text": "一篇讲注意力的文章", "source": "文章", "field": "测试",
                                                "project": "demo"})
    finally:
        imp_mod.ask = original
    assert r.status_code == 200, r.text
    d = r.json()
    assert seen["role"] == "learn" and seen["op"] == "import"
    assert "RNN与长程依赖｜RNN与长程依赖｜痛点" in seen["prompt"] and "注意力机制｜注意力机制｜前身" in seen["prompt"], "待认领的点要进提示词"
    assert "- a｜" not in seen["prompt"].split("## 当前项目里还没建的点")[1].split("## 文章")[0], "已建的点不是待认领"
    ids = [n["id"] for n in d["plan"]["nodes"]]
    assert ids == ["注意力机制", "RNN", "孤零零"], ids
    assert [(x["node_id"], x["point_id"]) for x in d["claims"]] == [("注意力机制", "注意力机制")], d["claims"]
    assert "claims" not in d["plan"]["nodes"][0]
    # 认领改 id 要连带：另一个节点指向它的关系、正文链接
    assert d["plan"]["nodes"][0]["relations"][1]["target"] == "RNN", "没指向它的关系不能被误改"
    assert [(x["node_id"], x["point_id"]) for x in d["near_misses"]] == [("RNN", "RNN与长程依赖")], d["near_misses"]
    # RNN 自己没连已有节点，但同篇的「注意力机制」连了 a 又认领了清单点，整块不算孤立；真孤立的只有孤零零
    assert d["isolated"] == ["孤零零"], d["isolated"]
    assert d["suggest_home"]["name"] == "AI" and d["project_points"] == 2
    assert d["preview"]["applied"] is False and any(f["path"].endswith("注意力机制.md") for f in d["preview"]["files"])

    # 卡上点「改用清单里的 id」+「这条待审直接写」：服务端改方案再翻译
    plan = d["plan"]
    plan["nodes"][1]["relations"] = [{"type": "对比", "target": "b", "confidence": 0.2}]
    r = c.post("/api/import", json={"plan": plan, "field": "测试", "source": "文章",
                                    "renames": {"RNN": "RNN与长程依赖"}, "promote": ["RNN->b#对比"]})
    assert r.status_code == 200, r.text
    d2 = r.json()
    paths = sorted(f["path"] for f in d2["files"])
    assert "nodes/测试/RNN与长程依赖.md" in paths and not any(p.endswith("/RNN.md") for p in paths), paths
    zy = next(f for f in d2["files"] if f["path"].endswith("注意力机制.md"))
    assert "[[RNN与长程依赖]]" in zy["diff"] and "- 部件:: [[RNN与长程依赖]]" in zy["diff"], zy["diff"]
    assert d2["pending"] == [], "被提升的待审边该直接写"
    rnn = next(f for f in d2["files"] if f["path"].endswith("RNN与长程依赖.md"))
    assert "- 对比:: [[b]]" in rnn["diff"], rnn["diff"]


@case
def import_素材源列表与读取_挡住越界路径():
    c, vault = client()
    core.write(vault / "doc/笔记/一篇.md", "# 一篇\n正文")
    core.write(vault / "day-info/摘要.txt", "摘要")
    core.write(vault / "web/dist/x.md", "不该列")
    files = c.get("/api/import/sources").json()["files"]
    paths = {f["path"] for f in files}
    assert {"doc/笔记/一篇.md", "day-info/摘要.txt"} <= paths, paths
    assert not any(p.startswith(("nodes/", "web/", ".knowrary/")) for p in paths), paths
    assert c.get("/api/import/source", params={"path": "doc/笔记/一篇.md"}).json()["text"].startswith("# 一篇")
    for bad in ("../etc/passwd", "nodes/组A/a.md", "web/dist/x.md", "doc/没有.md"):
        assert c.get("/api/import/source", params={"path": bad}).status_code == 422, bad
    # propose 用 file 而不是 text
    from server import importing as imp_mod
    original = imp_mod.ask
    imp_mod.ask = lambda *a, **k: json.dumps({"nodes": [{"id": "n", "name": "n", "desc": "d", "relations": []}]})
    try:
        r = c.post("/api/import/propose", json={"file": "doc/笔记/一篇.md", "source": "一篇", "field": "测试"})
        assert r.status_code == 200 and r.json()["isolated"] == ["n"], r.text
        r = c.post("/api/import/propose", json={"source": "x", "field": "测试"})
        assert r.status_code == 422, "没文章该 422"
    finally:
        imp_mod.ask = original


@case
def inbox_零边与缺域框标记_一键建域框放进去():
    """Inbox 条目带两个新标记：一条边都没有（degree 0）、有 field 但画布上没同名顶层框。
    后者给 create_field_group：先在整张图最下面开一个同名框，再把节点放进去；同一批第二个同领域的复用那个框。"""
    c, vault = client()
    layout = get_layout(c)
    core.write(vault / "nodes/新域/孤点.md", node_md("孤点", field="量子计算"))
    core.write(vault / "nodes/新域/孤点二.md", node_md("孤点二", field="量子计算"))
    core.write(vault / "nodes/组A/有边.md", node_md("有边", rels="- 依赖:: [[a]]"))
    index_service.invalidate()
    items = {i["id"]: i for i in c.get("/api/inbox").json()["items"]}
    assert items["孤点"]["degree"] == 0 and items["孤点"]["field_group_missing"] is True, items["孤点"]
    assert items["孤点"]["suggested_group"] is None
    assert items["有边"]["field_group_missing"] is False and items["有边"]["suggested_group"], items["有边"]

    bottom_before = max(g["y"] + g["h"] for g in layout["layout"]["groups"].values() if not g.get("parent"))
    r = c.post("/api/place", json={"base_revision": layout["layout"]["revision"], "ids": ["孤点", "孤点二"],
                                   "create_field_group": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["placed"]) == 2 and d["created_groups"] == ["g-量子计算"], d
    assert d["grown_groups"] == [], "新开的框不算「长了一行」"
    lay = get_layout(c)["layout"]
    box = lay["groups"]["g-量子计算"]
    assert box["parent"] is None and box["name"] == "量子计算" and box["y"] >= bottom_before, box
    assert all(lay["nodes"][i]["group"] == "g-量子计算" and lay["nodes"][i]["state"] == "draft" for i in ("孤点", "孤点二"))
    for i in ("孤点", "孤点二"):
        n = lay["nodes"][i]
        assert box["x"] <= n["x"] and n["x"] + n["w"] <= box["x"] + box["w"], "节点要在框里"
    # 不给 create_field_group：照旧留在 Inbox
    core.write(vault / "nodes/新域/孤点三.md", node_md("孤点三", field="生物"))
    index_service.invalidate()
    r = c.post("/api/place", json={"base_revision": lay["revision"], "ids": ["孤点三"]}).json()
    # **"放不下"和"判不出分组"必须分开说**：前者要人去挪框，后者要人去填 layer / 建域框。
    # 混成一句的话，人只能反复点「放进去」然后反复失败（实盘上撞过两次）。
    reason = r["skipped"][0]["reason"]
    assert r["placed"] == [] and "判不出" in reason, r
    assert "layer" in reason or "建域框" in reason, f"只说失败不给下一步：{reason}"


@case
def import_落盘记归属建议_Inbox显示_放上画布后不再显示():
    c, vault = client()
    get_layout(c)                       # 先让布局生成好，之后导进来的点才会落 Inbox
    plan = {"nodes": [{"id": "孤零零", "name": "孤零零", "desc": "d", "relations": []}],
            "suggest_home": {"isolated": ["孤零零", "没建的"], "kind": "field", "name": "AI", "why": "讲的是模型"}}
    r = c.post("/api/import", json={"plan": plan, "field": "测试", "source": "文章", "dry_run": False})
    assert r.status_code == 200, r.text
    pend = json.loads((vault / ".knowrary/pending.json").read_text("utf-8"))
    assert pend["homes"] == [{**pend["homes"][0], "node_ids": ["孤零零"], "kind": "field", "name": "AI"}], pend["homes"]
    assert "没建的" not in pend["homes"][0]["node_ids"], "只记这次真建出来的"
    item = next(i for i in c.get("/api/inbox").json()["items"] if i["id"] == "孤零零")
    assert item["home"]["name"] == "AI" and item["home"]["why"] == "讲的是模型" and item["home"]["origin"]["source"] == "文章", item
    # 放上画布后它不在 Inbox 里，建议自然不显示；再导一篇同一批节点的建议会覆盖旧的
    rev = get_layout(c)["layout"]["revision"]
    c.post("/api/place", json={"base_revision": rev, "ids": ["孤零零"], "create_field_group": True})
    assert "孤零零" not in {i["id"] for i in c.get("/api/inbox").json()["items"]}


@case
def summarize_起草概括节点_子节点节选进提示词_关系段被截():
    """/api/summarize：给几个点，模型起草上位节点；正文节选、已有关系进提示词；回答里混进 `## 关系` 被截掉。"""
    from server import summarize as sm
    c, _ = client()
    seen = {}
    original = sm.ask
    sm.ask = lambda vault, role, prompt, op="?": (seen.update(role=role, op=op, prompt=prompt) or json.dumps({
        "name": "A与B", "desc": "A 和 B 合起来讲", "layer": "硬件", "year": "1990",
        "body": "## 描述\n整体\n\n## 核心内容\n[[a]] 是地基，[[b]] 是部件。\n\n## 关系\n- 包含:: [[a]]"}, ensure_ascii=False))
    try:
        r = c.post("/api/summarize", json={"node_ids": ["a", "b", "不存在"], "name": "组A"})
    finally:
        sm.ask = original
    assert r.status_code == 200, r.text
    d = r.json()
    assert seen["role"] == "learn" and seen["op"] == "summarize"
    assert "- a｜A 的摘要｜正文｜部件→b; 演化为→c" in seen["prompt"], seen["prompt"][-600:]
    assert "组A" in seen["prompt"]
    assert d["children"] == ["a", "b"], d["children"]
    assert d["name"] == "A与B" and d["layer"] == "硬件" and d["year"] == 1990
    assert "## 关系" not in d["body"] and d["body"].endswith("是部件。"), d["body"]
    assert c.post("/api/summarize", json={"node_ids": ["a", "不存在"]}).status_code == 422, "只剩一个真节点不能概括"


@case
def import_旧revision拒绝_写不进去的方案422():
    c, _ = client()
    rev = c.get("/api/index").json()["revision"]
    plan = {"nodes": [{"id": "x", "name": "x", "desc": "d"}]}
    assert c.post("/api/import", json={"plan": plan, "field": "测试", "source": "s", "base_revision": rev + 99}).status_code == 409
    # 已存在的节点不建、也不报错——它成了 warnings；正文里带 `## 关系` 也被截掉——都不该 422
    r = c.post("/api/import", json={"plan": {"nodes": [{"id": "a", "name": "A", "desc": "d"}]}, "field": "测试", "source": "s"})
    assert r.status_code == 200 and any("已存在" in w for w in r.json()["warnings"]), r.text
    # 真写不进去的：layer 不在已知抽象层里
    bad = {"nodes": [{"id": "y", "name": "y", "desc": "d", "layer": "不存在的层"}]}
    assert c.post("/api/import", json={"plan": bad, "field": "测试", "source": "s"}).status_code == 422


@case
def layout_项目画布按阶段成列不画框():
    """去掉父框后阶段信息只剩"列的先后"：已建的点先学的在左、后学的在右，同一阶段竖排；
    还没建的幽灵不混进列里，集中停在右下角当待学区（2026-09-18 用户要求：一眼看清还差什么）。"""
    c, _, _ = with_inbox_node()
    ghosts = [{"id": f"p{i}"} for i in range(5)]
    c.put("/api/projects", json={"base_revision": 0, "projects": {"demo": {
        "name": "演示", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "a"}, {"id": "b"}, {"id": "p9"}]},
            {"name": "二", "points": [{"id": "c"}] + ghosts}]}]}}})
    nodes = c.get("/api/layout?layout=demo").json()["layout"]["nodes"]
    assert nodes["a"]["x"] == nodes["b"]["x"] and nodes["a"]["y"] < nodes["b"]["y"], "同一阶段该竖排"
    assert nodes["c"]["x"] > nodes["a"]["x"] and nodes["c"]["y"] == nodes["a"]["y"], "后一阶段该在右边、顶上对齐"
    ghost_ids = ["p9"] + [g["id"] for g in ghosts]
    assert all(nodes[g]["state"] == "ghost" for g in ghost_ids)
    assert min(nodes[g]["x"] for g in ghost_ids) > nodes["c"]["x"], "幽灵该在已建区右边"
    assert min(nodes[g]["y"] for g in ghost_ids) == nodes["b"]["y"], "幽灵区顶边与已建区最后一行对齐（右下角）"
    assert nodes["p9"]["x"] < nodes["p0"]["x"], "幽灵按清单里出现的先后排"
    assert len({(n["x"], n["y"]) for n in nodes.values()}) == len(nodes), "没有两个点叠在一起"


@case
def layout_项目画布与全局图互不影响():
    """项目画布是工作台，全局图是成品图：**在项目画布上拖节点，全局 layout 的 revision 不变。**"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"demo": {
        "name": "演示", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "a"}, {"id": "还没建的"}]}]}]}}})

    before = c.get("/api/layout").json()["layout"]["revision"]
    proj = c.get("/api/layout?layout=demo").json()
    assert proj["generated"] is True, "项目画布没有自动生成"
    assert set(proj["layout"]["nodes"]) == {"a", "还没建的"}, proj["layout"]["nodes"]
    # 还没建的点是幽灵占位，**不算孤立记录**——那正是它的含义
    assert proj["layout"]["nodes"]["还没建的"]["state"] == "ghost"
    assert proj["orphans"] == [], proj["orphans"]
    assert proj["layout"]["groups"] == {}, "项目画布不该有父框（2026-09-18 去掉）"
    assert all(n["group"] is None for n in proj["layout"]["nodes"].values()), proj["layout"]["nodes"]

    r = c.patch("/api/layout?layout=demo", json={"base_revision": proj["layout"]["revision"],
                                                 "nodes": {"a": {"x": 999, "y": 888}}})
    assert r.status_code == 200, r.text
    assert c.get("/api/layout").json()["layout"]["revision"] == before, "动项目画布把全局图的 revision 碰了"
    assert c.get("/api/layout?layout=demo").json()["layout"]["nodes"]["a"]["x"] == 999
    # 文件真的分开了
    assert (vault / ".knowrary" / "layouts" / "demo.json").exists()
    assert "999" not in (vault / ".knowrary" / "layout.json").read_text("utf-8")

    # 认不出的 layout 名一律拒，绝不让它拼出路径
    assert c.get("/api/layout?layout=../../etc").status_code == 422
    assert c.get("/api/layout?layout=nosuch").status_code == 404


@case
def projects_同步到全局只放该放的且不搬坐标():
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"demo": {
        "name": "演示", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "a"}, {"id": "d"}, {"id": "还没建的"}]}]}]}}})
    # 在项目画布上把 a 拖到一个很远的位置
    proj = c.get("/api/layout?layout=demo").json()["layout"]
    c.patch("/api/layout?layout=demo", json={"base_revision": proj["revision"],
                                             "nodes": {"a": {"x": 4321, "y": 1234}}})

    rev = c.get("/api/layout").json()["layout"]["revision"]
    out = c.post("/api/projects/demo/sync", json={"base_revision": rev}).json()
    reasons = {x["id"]: x["reason"] for x in out["skipped"]}
    assert "a" in reasons and "已经在全局图上了" in reasons["a"], out["skipped"]
    assert "还没建的" in reasons and "还没建出来" in reasons["还没建的"], out["skipped"]
    ids = [p["id"] for p in out["placed"]]
    assert ids == ["d"], out                                   # 只放建好了、还没上图的那个（d 在 Inbox 里）
    assert all(p["state"] == "draft" for p in out["placed"]), out["placed"]

    # **坐标不搬**：项目画布里的排版是你为了想清楚而摆的，全局图有自己的结构
    glob = c.get("/api/layout").json()["layout"]["nodes"]
    assert glob["a"]["x"] != 4321, "把项目画布的坐标搬到全局图了"


@case
def projects_同步前先把重复候选摆出来():
    """去重要发生在写入之前：只按 id 比对拦不住近义词，而"回头去欠账里清"最容易不做。"""
    c, vault, _ = with_inbox_node()
    # 两个名字高度相似的节点：一个已经在图上，一个在项目里等着同步
    for nid, name in (("自注意力", "自注意力"), ("自注意力机制", "自注意力机制")):
        core.write(vault / f"nodes/组A/{nid}.md",
                   f"---\nname: {name}\nfield: 测试\ndesc: 说明\n---\n# {name}\n\n正文\n")
    index_service.invalidate()
    rev0 = c.get("/api/layout").json()["layout"]["revision"]
    c.post("/api/place", json={"base_revision": rev0, "ids": ["自注意力"]})   # 先让一个上图
    c.put("/api/projects", json={"base_revision": 0, "projects": {"demo": {
        "name": "演示", "lists": [{"kind": "学习", "name": "主线",
        "stages": [{"name": "一", "points": [{"id": "自注意力机制"}]}]}]}}})
    rev = c.get("/api/layout").json()["layout"]["revision"]
    out = c.post("/api/projects/demo/sync", json={"base_revision": rev}).json()
    hit = [d for d in out["duplicates"] if d["id"] == "自注意力机制"]
    assert hit and hit[0]["candidates"], out
    assert hit[0]["candidates"][0]["id"] == "自注意力", hit
    assert not [p for p in out["placed"] if p["id"] == "自注意力机制"], "命中重复候选还是直接放上去了"


@case
def projects_重叠_同一个点在两个项目里只出现一次且掌握度共用():
    """项目是视角不是容器：NLP ⊃ Transformer 不需要任何父子字段，重叠自动成立。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {
        "transformer": {"name": "Transformer", "lists": [{"stages": [
            {"name": "一", "points": [{"id": "a"}, {"id": "没建的"}]}]}]},
        "nlp": {"name": "NLP", "lists": [{"stages": [
            {"name": "一", "points": [{"id": "a"}, {"id": "没建的"}, {"id": "另一个没建的"}]}]}]}}})
    data = c.get("/api/projects").json()
    # 两个项目各自算各自的总数，但同一个点的掌握度是同一个——同一个大脑
    assert data["progress"]["transformer"]["all"]["total"] == 2
    assert data["progress"]["nlp"]["all"]["total"] == 3
    assert (data["progress"]["transformer"]["all"]["points"]["a"]
            == data["progress"]["nlp"]["all"]["points"]["a"])

    today = c.get("/api/coach/today").json()
    ids = [i["id"] for i in today["items"]]
    assert len(ids) == len(set(ids)), f"同一个点出现了不止一次：{ids}"
    assert len(today["projects"]) == 2, today["projects"]

    # 按项目过滤：**整屏都只看这个项目**，包括到期复习
    only = c.get("/api/coach/today?project=nlp").json()
    assert {i["project"] for i in only["items"] if i["kind"] in ("unbuilt", "shell")} == {"nlp"}
    mine = {"a", "没建的", "另一个没建的"}
    assert all(i["id"] in mine for i in only["items"]), only["items"]
    assert "本项目" in only["pools"] and "本项目" not in today["pools"], only["pools"]


@case
def projects_在一个项目里复习另一个项目也看得见():
    """在 A 项目里复习了某个点，B 项目里它也是"已复习"。这是对的——同一个大脑。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {
        "a1": {"name": "项目一", "lists": [{"stages": [{"name": "一", "points": [{"id": "a"}]}]}]},
        "b2": {"name": "项目二", "lists": [{"stages": [{"name": "一", "points": [{"id": "a"}]}]}]}}})
    before = c.get("/api/projects").json()["progress"]
    assert before["a1"]["all"]["points"]["a"] == before["b2"]["all"]["points"]["a"]

    for _ in range(4):                      # 连记 4 次「记得」，间隔序号推到「已掌握」那一档
        c.post("/api/review/a", json={"grade": "记得"})
    after = c.get("/api/projects").json()["progress"]
    assert after["a1"]["all"]["points"]["a"] == "已掌握", after["a1"]["all"]["points"]
    assert after["b2"]["all"]["points"]["a"] == "已掌握", "在 a1 里复习的，b2 里没看见"
    assert before["b2"]["all"]["points"]["a"] == "学过", before["b2"]["all"]["points"]


@case
def coach_项目视角下只摆这个项目的_但别的欠账要如实报():
    """过滤可以，藏起来不行——藏起来的复习等于没有复习。"""
    c, vault, _ = with_inbox_node()
    c.post("/api/quiz/grade", json={"answers": [{**q("A 是什么", ["a"]), "grade": "忘了"}]})
    c.put("/api/projects", json={"base_revision": 0, "projects": {
        "p1": {"name": "项目一", "lists": [{"stages": [{"name": "一", "points": [{"id": "b"}]}]}]}}})

    glob = c.get("/api/coach/today").json()
    assert any(i["id"] == "a" for i in glob["items"]), glob["items"]     # 全局下 a 在
    assert glob["elsewhere"] == {"wrong": 0, "due": 0}, glob["elsewhere"]   # 全局没有"别处"

    only = c.get("/api/coach/today?project=p1").json()
    assert not any(i["id"] == "a" for i in only["items"]), "别的项目的错题还摆在这一屏"
    assert only["elsewhere"]["wrong"] >= 1, only["elsewhere"]            # 但要如实报出来
    # 复习调度本身没变：过滤的只是"今天摆谁"，next_due 仍然只有一份
    assert c.get("/api/review/due").json()["count"] >= 1


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
    c.put("/api/projects", json={"base_revision": 0, "projects": {"p": {
        "name": "p", "lists": [{"stages": [{"name": "一", "points": [{"id": "a"}, {"id": "没建的"}]}]}]}}})
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
    c.put("/api/projects", json={"base_revision": 0, "projects": {"p": {
        "name": "p", "daily_quota": 2, "lists": [{"stages": [{"name": "一", "points": [
            {"id": "空壳"}, {"id": "没建1"}, {"id": "没建2"}, {"id": "没建3"}]}]}]}}})
    picked = [(i["kind"], i["id"]) for i in c.get("/api/coach/today").json()["items"]
              if i["kind"] in ("unbuilt", "shell")]
    assert picked == [("unbuilt", "没建1"), ("unbuilt", "没建2")], picked   # 配额 2，未建优先


@case
def coach_建完的阶段就不再出现在清单里():
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"p": {
        "name": "p", "lists": [{"stages": [{"name": "一", "points": [{"id": "a"}, {"id": "b"}]}]}]}}})
    data = c.get("/api/coach/today").json()
    assert not [i for i in data["items"] if i["kind"] in ("unbuilt", "shell")], data["items"]
    assert data["projects"][0]["done"] is True and data["projects"][0]["stage"] == "", data["projects"]


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
def calendar_纯读且区间可控():
    c, vault, _ = with_inbox_node()
    c.post("/api/quiz/grade", json={"answers": [{**q("A 是什么", ["a"]), "grade": "忘了"}]})
    before = {str(p): p.read_text("utf-8") for p in vault.rglob("*.md")}

    data = c.get("/api/calendar?days=30").json()
    assert data["from"] < data["to"], data
    today = dt.date.today().isoformat()
    assert data["days"][today]["answers"] == 1, data["days"].get(today)
    assert data["days"][today]["reviews"] >= 1, data["days"].get(today)
    assert data["streak"] >= 1, data

    assert {str(p): p.read_text("utf-8") for p in vault.rglob("*.md")} == before, "日历改了 md"
    assert c.get("/api/calendar?to=昨天").status_code == 422      # 看不懂的日期要拒，不是 500


@case
def health_汇总可用():
    c, _ = client()
    h = c.get("/api/health").json()
    assert h["stats"]["nodes"] == 3 and h["layout_revision"] == 1, h
    assert h["index_revision"] >= 1


# ---------------------------------------------------------------- 阶段 12：对话式教练

def stub_chat(replies: list):
    """按顺序吐回复的假模型。返回 (原函数, 收到的 messages 列表)。

    一条回复要么是一段话（纯文本回合），要么是 `tool_block(...)` 给的
    `{"text", "calls"}`——工具调用是**结构化字段**，不再藏在正文的围栏里。

    `seen` 上挂两份旁证：`tools`（每次调用拿到的工具 schema——白名单收走一个工具时
    schema 里也必须真的少一个，留着再叮嘱一句"别用"那是压制不是关闭）、
    `sessions`（每次调用带的会话钥匙，claude-cli 靠它 `--resume` 只发新增的几条）。
    """
    from server import chat as chat_mod
    original = chat_mod.llm_chat

    class Seen(list):
        tools: list = []
        sessions: list = []

    seen = Seen()
    seen.tools = []
    seen.sessions = []
    box = list(replies)

    def fake(vault, role, messages, tools=None, op="chat", on_delta=None, session=None):
        seen.append([dict(m) for m in messages])
        seen.tools.append(tools)
        seen.sessions.append(session)
        reply = box.pop(0) if box else "没话说了"
        if isinstance(reply, dict):
            text, calls = reply.get("text") or "", reply.get("calls") or []
        else:
            text, calls = reply, []
        if on_delta and text:
            on_delta(text)
        return text, calls, {"input_tokens": 1, "output_tokens": 1}

    chat_mod.llm_chat = fake
    return original, seen


def restore_chat(original) -> None:
    from server import chat as chat_mod
    chat_mod.llm_chat = original


_CALL_N = [0]


def tool_block(name: str, args: dict, *, text: str = "我查一下。") -> dict:
    """一步：说一句话 + 调一个工具。id 每次都不一样，和真实协议一致。"""
    _CALL_N[0] += 1
    return {"text": text, "calls": [{"id": f"call_{_CALL_N[0]}", "name": name, "args": args}]}


def tool_blocks(*pairs, text: str = "我一起查。") -> dict:
    """一步里并行调好几个工具——原生协议允许，文本围栏时代做不到。"""
    out = []
    for name, args in pairs:
        _CALL_N[0] += 1
        out.append({"id": f"call_{_CALL_N[0]}", "name": name, "args": args})
    return {"text": text, "calls": out}


def sse_events(resp) -> list[dict]:
    return [json.loads(x[6:]) for x in resp.text.splitlines() if x.startswith("data: ")]


@case
def 模型久不吐字要发心跳而不是干等():
    """2026-09-21 那次：上游等了 63 秒才断，这 63 秒里 SSE 一个字节都没往外发。

    后果有两层：页面上"想一下…"和"已经挂了"长得一模一样；而且但凡中间隔一层反代，
    空闲超时（nginx 默认 60s）会把连接掐掉，症状和模型挂掉难以分辨。
    """
    from server import chat as chat_mod

    c, _ = client({"nodes/组A/a.md": node_md("A")})
    original, beat = chat_mod.llm_chat, chat_mod.HEARTBEAT_SEC
    chat_mod.HEARTBEAT_SEC = 0.05

    def slow(vault, role, messages, tools=None, op="chat", on_delta=None, session=None):
        time.sleep(0.2)                       # 模型在这儿想事情，一个字都没吐
        if on_delta:
            on_delta("想好了")
        return "想好了", [], {}

    chat_mod.llm_chat = slow
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}]})
        evs = sse_events(r)
    finally:
        chat_mod.llm_chat, chat_mod.HEARTBEAT_SEC = original, beat

    pings = [e for e in evs if e["type"] == "ping"]
    assert pings, [e["type"] for e in evs]
    assert all("waited" in e for e in pings), pings          # 界面要拿它显示"已等 N 秒"
    assert [e for e in evs if e["type"] == "done"], [e["type"] for e in evs]


@case
def 没答成的那一轮读回来要带着记号():
    """留档里那行"（这一轮没答成：…）"是个记号，不是模型说过的话。

    刷新页面会把留档读回前端接着聊——认不出这个记号，它就成了模型眼里的上一轮回答，
    下一轮被当上下文原样发回去。前端靠这个 `failed` 标把它摘掉（`forModel`）。
    """
    from server import chat as chat_mod

    c, vault = client({"nodes/组A/a.md": node_md("A")})
    original = chat_mod.llm_chat

    def boom(vault, role, messages, tools=None, op="chat", on_delta=None, session=None):
        raise SystemExit("[learn 角色 · provider `x`] LLM 连接失败（http://上游/v1）："
                         "RemoteDisconnected: Remote end closed connection without response")

    chat_mod.llm_chat = boom
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}],
                                      "session": "s1"})
        assert [e for e in sse_events(r) if e["type"] == "error"], sse_events(r)
    finally:
        chat_mod.llm_chat = original

    rows = c.get("/api/chat/history", params={"session": "s1"}).json()["messages"]
    assert rows[-1]["failed"] is True, rows[-1]
    assert rows[0]["failed"] is False, rows[0]                # 我说的那句不能跟着被摘掉
    # 出错流水上这一条要能回答"是谁断的"——只剩一句英文的话，事后连 provider 都猜不出来
    issue = core.load_issues(vault)[-1]
    assert issue["kind"] == "llm" and "provider" in issue["message"], issue
    assert "上游" in issue["message"], issue


@case
def web_构建产物带no_cache头且能走304():
    """产物文件名**不带 content hash**（见 web/vite.config.js）：改一行源码只有 index.js 变，
    git 只存那一个 delta，而不是 57 个新文件。代价是"文件换了"没法靠名字告诉浏览器——
    只能靠这个头。丢了它，改完前端刷新页面看到的还是旧界面。

    no-cache 不是不缓存：浏览器照旧存着，只是每次拿 ETag 问一句，没变就 304。
    """
    from server.app import app as real_app
    from fastapi.testclient import TestClient as TC
    web_dist = Path(__file__).resolve().parents[2] / "web" / "dist" / "assets" / "index.js"
    if not web_dist.exists():
        return                                   # 没构建过就跳过，别让自测依赖构建产物
    c = TC(real_app)
    r = c.get("/assets/index.js")
    assert r.status_code == 200, r.status_code
    assert r.headers.get("cache-control") == "no-cache", dict(r.headers)
    etag = r.headers.get("etag")
    assert etag, "没有 ETag 就没法 304，no-cache 会退化成每次全量重下"
    assert c.get("/assets/index.js", headers={"If-None-Match": etag}).status_code == 304
    assert c.get("/").headers.get("cache-control") == "no-cache", "index.html 是那张清单，更不能缓存"


def stub_years_llm(payload: str):
    from server import years as years_mod
    original = years_mod.ask
    years_mod.ask = lambda vault, role, prompt, op="?": payload
    return original


@case
def years_只认问过的节点且拿不准的不填():
    """批量补 year 最怕的是**往 md 里写没核对过的东西**。

    模型偶尔会顺手给一个没问过的节点，甚至编一个不存在的 id——放它过去就等于
    凭一句话改 frontmatter。而错的 year 比空的 year 难发现：它会把节点摆到时间轴上
    一个看起来很正常的位置，没人会回头核。
    """
    from server import years as years_mod
    c, _ = client({"nodes/组A/a.md": node_md("A"), "nodes/组A/b.md": node_md("B")})
    original = stub_years_llm(json.dumps({"years": [
        {"id": "a", "year": 1980, "confidence": 0.9, "why": "某论文 1980 年发表"},
        {"id": "不存在的点", "year": 1999, "confidence": 0.9},     # 编出来的 id
        {"id": "b", "year": "1970年代", "confidence": 0.9},        # 不是四位数年份
    ]}, ensure_ascii=False))
    try:
        r = c.post("/api/years/propose", json={})
    finally:
        years_mod.ask = original

    data = r.json()
    assert [s["id"] for s in data["suggestions"]] == ["a"], data["suggestions"]
    assert data["suggestions"][0]["year"] == 1980 and data["suggestions"][0]["picked"] is True
    assert "b" in data["skipped"], data["skipped"]          # 问过、没给 → 如实报出来
    assert "不存在的点" not in data["skipped"], data["skipped"]


@case
def years_把握低的列出来但默认不勾():
    """低把握的不能直接丢掉（人可能一眼就知道对），但也不能默认勾上。"""
    from server import years as years_mod
    c, _ = client({"nodes/组A/a.md": node_md("A")})
    original = stub_years_llm(json.dumps({"years": [
        {"id": "a", "year": 2017, "confidence": 0.3, "why": "不太确定"}]}, ensure_ascii=False))
    try:
        data = c.post("/api/years/propose", json={}).json()
    finally:
        years_mod.ask = original
    assert len(data["suggestions"]) == 1 and data["suggestions"][0]["picked"] is False, data


@case
def years_已经填了year的不再问():
    c, _ = client({"nodes/组A/a.md": node_md("A", extra="year: 1980\n"),
                   "nodes/组A/b.md": node_md("B")})
    missing = c.get("/api/years/missing").json()
    assert [x["id"] for x in missing["items"]] == ["b"], missing
    assert missing["count"] == 1, missing


COMPARE_FILES = {
    "fields/对比组/compare-fenci.md": node_md(
        "分词技术对比", field="AI",
        extra="type: 对比组\ndimensions: [年份, 核心方法]\n",
        rels="- 包含:: [[tok]]\n- 包含:: [[jieba]]"),
    "nodes/组A/jieba.md": node_md("jieba", field="AI", extra="year: 2012\n")
    + "\n## 速查\n- 核心方法:: 词典 + HMM\n- 实现方式:: 双数组 Trie\n",
    "nodes/组A/tok.md": node_md("tok", field="AI", extra="year: 2016\n")
    + "\n## 速查\n- 核心方法:: 子词切分\n- 实现方式:: BPE\n",
}


@case
def 对比_目录和表两个只读接口():
    c, _ = client(COMPARE_FILES)
    d = c.get("/api/compare").json()
    assert d["count"] == 1 and d["items"][0]["id"] == "compare-fenci", d
    assert d["items"][0]["members"] == 2 and d["items"][0]["gaps"] == 0, d["items"][0]

    t = c.get("/api/compare/compare-fenci").json()
    assert t["columns"] == ["年份", "核心方法"], t["columns"]
    # 行序跟着 md 里 `- 包含::` 的书写顺序，不是 id 字典序
    assert [r["id"] for r in t["rows"]] == ["tok", "jieba"], [r["id"] for r in t["rows"]]
    assert t["rows"][0]["cells"]["年份"]["source"] == "frontmatter", t["rows"][0]
    assert t["promote"] == [{"key": "实现方式", "count": 2, "members": ["tok", "jieba"]}], t["promote"]
    assert c.get("/api/compare/jieba").status_code == 404, "普通节点不是对比组"


@case
def 对比_有自己的一张画布而且不碰全局图():
    """`?layout=<对比组>` 第一次打开时现生成一份，成员顺序和表的行序一致。"""
    c, vault = client(COMPARE_FILES)
    r = c.get("/api/layout?layout=compare-fenci")
    assert r.status_code == 200, r.text
    doc = r.json()["layout"]
    assert r.json()["generated"] is True, "第一次打开该生成一份"
    assert set(doc["nodes"]) == {"tok", "jieba"} and not doc["groups"], doc
    assert doc["nodes"]["tok"]["x"] < doc["nodes"]["jieba"]["x"], "顺序该和表的行序一致"
    assert (vault / ".knowrary" / "layouts" / "compare-fenci.json").exists()
    # 全局图一点没变：对比组既不在上面，也没被塞进 Inbox
    g = c.get("/api/layout").json()["layout"]
    assert "compare-fenci" not in g["nodes"], g["nodes"].keys()
    assert "compare-fenci" not in c.get("/api/inbox").json()["items"], "对比组不该催你放上画布"


@case
def 对比_画布可写且拒绝拿id拼路径():
    c, _ = client(COMPARE_FILES)
    doc = c.get("/api/layout?layout=compare-fenci").json()["layout"]
    saved = c.patch("/api/layout?layout=compare-fenci",
                    json={"base_revision": doc["revision"], "nodes": {"tok": {"x": 999, "y": 5}}})
    assert saved.status_code == 200, saved.text
    again = c.get("/api/layout?layout=compare-fenci").json()["layout"]
    assert again["nodes"]["tok"]["x"] == 999, again["nodes"]["tok"]

    # id 直接当文件名用：形状不对的一律拒，查无此 id 的 404——两道闸都要在
    assert c.get("/api/layout?layout=../../etc/passwd").status_code == 422
    assert c.get("/api/layout?layout=.hidden").status_code == 422
    assert c.get("/api/layout?layout=jieba").status_code == 404, "普通节点没有自己的画布"


def stub_compare_llm(payload: str):
    from server import compare as compare_mod
    original = compare_mod.ask
    compare_mod.ask = lambda vault, role, prompt, op="?": payload
    return original


@case
def 对比_补一轮只认问过的格子():
    """一次调用补整张表。模型顺手填一个没问的格、或自造一个维度名，都不许放过去——
    放过去就等于凭一句话往 md 里写字段，而自造的维度名根本进不了这张表。"""
    from server import compare as compare_mod
    files = dict(COMPARE_FILES)
    files["nodes/组A/jieba.md"] = node_md("jieba", field="AI", extra="year: 2012\n")
    c, vault = client(files)
    original = stub_compare_llm(json.dumps({"cells": [
        {"id": "jieba", "key": "核心方法", "value": "词典 + HMM", "confidence": 0.9, "why": "笔记里写了"},
        {"id": "jieba", "key": "实现方式", "value": "自造的维度", "confidence": 0.9},   # 不在 dimensions 里
        {"id": "不存在", "key": "核心方法", "value": "编的", "confidence": 0.9},          # 没问过的节点
        {"id": "tok", "key": "核心方法", "value": "已经填过了", "confidence": 0.9},        # 这格不空，没问
    ]}, ensure_ascii=False))
    try:
        data = c.post("/api/compare/compare-fenci/propose", json={}).json()
    finally:
        compare_mod.ask = original
    assert [(s["id"], s["key"]) for s in data["suggestions"]] == [("jieba", "核心方法")], data["suggestions"]
    assert data["asked"] == 1 and data["skipped"] == [], data
    # 只是提议：md 一个字没动
    assert "核心方法" not in (vault / "nodes/组A/jieba.md").read_text("utf-8")


@case
def 对比_把握低的列出来但默认不勾():
    from server import compare as compare_mod
    files = dict(COMPARE_FILES)
    files["nodes/组A/jieba.md"] = node_md("jieba", field="AI", extra="year: 2012\n")
    c, _ = client(files)
    original = stub_compare_llm(json.dumps({"cells": [
        {"id": "jieba", "key": "核心方法", "value": "拿不太准", "confidence": 0.3}]}, ensure_ascii=False))
    try:
        data = c.post("/api/compare/compare-fenci/propose", json={}).json()
    finally:
        compare_mod.ask = original
    assert len(data["suggestions"]) == 1 and data["suggestions"][0]["picked"] is False, data


@case
def 对比_模型跳过的格子要如实报出来():
    """模型说"拿不准"不是失败——提示词就这么要求的。但不能让人以为补齐了。"""
    from server import compare as compare_mod
    files = dict(COMPARE_FILES)
    files["nodes/组A/jieba.md"] = node_md("jieba", field="AI", extra="year: 2012\n")
    c, _ = client(files)
    original = stub_compare_llm(json.dumps({"cells": []}))
    try:
        data = c.post("/api/compare/compare-fenci/propose", json={}).json()
    finally:
        compare_mod.ask = original
    assert data["skipped"] == ["jieba·核心方法"], data
    assert c.post("/api/compare/不存在/propose", json={}).status_code == 404


@case
def 对比_写回走changes改一行不碰正文():
    """采纳一格 = 一条 `set_fact`，走 /api/changes 那唯一入口（diff 预览 + 备份 + 指纹校验）。"""
    files = dict(COMPARE_FILES)
    files["nodes/组A/jieba.md"] = (node_md("jieba", field="AI", extra="year: 2012\n")
                                   + "\n很长的一段正文，写了半年。\n\n## 速查\n- 核心方法:: 老做法\n")
    c, vault = client(files)
    rev = c.get("/api/index").json()["revision"]
    body = {"base_revision": rev, "dry_run": True,
            "changes": [{"type": "set_fact", "source": "jieba", "key": "核心方法", "value": "词典 + HMM"}]}
    preview = c.post("/api/changes", json=body).json()
    assert preview["applied"] is False and "词典 + HMM" in preview["files"][0]["diff"], preview
    assert "很长的一段正文" not in (vault / "nodes/组A/jieba.md").read_text("utf-8").split("## 速查")[1]

    saved = c.post("/api/changes", json={**body, "dry_run": False}).json()
    assert saved["applied"] is True and saved["backup"], saved
    text = (vault / "nodes/组A/jieba.md").read_text("utf-8")
    assert "- 核心方法:: 词典 + HMM" in text and "很长的一段正文，写了半年。" in text, text
    # 表格里立刻就能看到，而且这一格标着来自速查
    cell = c.get("/api/compare/compare-fenci").json()["rows"][1]["cells"]["核心方法"]
    assert cell == {"value": "词典 + HMM", "source": "速查"}, cell


@case
def 对比_空得过头的组进欠账():
    files = dict(COMPARE_FILES)
    files["nodes/组A/jieba.md"] = node_md("jieba", field="AI")
    files["nodes/组A/tok.md"] = node_md("tok", field="AI")
    c, _ = client(files)
    digest = c.get("/api/digest").json()
    assert digest["counts"]["compare_gaps"] == 1, digest["counts"]
    assert digest["compare_gaps"][0]["id"] == "compare-fenci", digest["compare_gaps"]


@case
def years_聚合文档不进补year的队列():
    """一张对比表没有"诞生年份"，让模型去猜只会猜出一个假的，而错的 year 比空的更难发现。

    这条和 `core.no_year` 是两份独立的过滤（面板走服务端这一份），漏一处就等于没改。
    """
    c, _ = client({"nodes/组A/a.md": node_md("A"),
                   "fields/对比组/compare-x.md": node_md(
                       "X 对比", extra="type: 对比组\ndimensions: [核心方法]\n",
                       rels="- 包含:: [[a]]\n- 包含:: [[b]]"),
                   "nodes/组A/b.md": node_md("B")})
    missing = c.get("/api/years/missing").json()
    assert [x["id"] for x in missing["items"]] == ["a", "b"], missing


@case
def years_提议不碰md():
    """propose 只读。真写回走 /api/changes 的 update_frontmatter，那条路才有备份和 diff。"""
    from server import years as years_mod
    c, vault = client({"nodes/组A/a.md": node_md("A")})
    before = md_digest(vault)
    original = stub_years_llm(json.dumps({"years": [{"id": "a", "year": 1980, "confidence": 0.9}]}))
    try:
        c.post("/api/years/propose", json={})
    finally:
        years_mod.ask = original
    assert md_digest(vault) == before, "提议阶段改了 md"

    r = c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                     "dry_run": False,
                                     "changes": [{"type": "update_frontmatter", "source": "a",
                                                  "fields": {"year": 1980}}]})
    assert r.status_code == 200, r.text
    assert "year: 1980" in (vault / "nodes/组A/a.md").read_text("utf-8")
    assert r.json()["backup"], "写回没留备份"


def _fake_anthropic(messages, model="claude-haiku-4-5-20251001", fail_first=None, tools=None):
    """替掉 _post_json 跑一次 _chat_anthropic，返回每次实际发出去的 payload。"""
    import llm_backend as backend
    sent: list = []

    def fake_post(url, headers, payload):
        sent.append(payload)
        if fail_first and len(sent) == 1:
            raise SystemExit(fail_first)
        return {"content": [{"type": "text", "text": "好"}], "usage": {"input_tokens": 1}}

    original = backend._post_json
    backend._post_json = fake_post
    try:
        backend._chat_anthropic(messages, {"api_key": "k"}, model, None, tools)
    finally:
        backend._post_json = original
    return sent


@case
def llm_会变的那块不进顶层system而是挂在队尾():
    """顶层 system **整体排在所有 messages 之前**，会变的东西放进去，它一变整段对话全作废。

    第一版把它拆成第二条顶层 system、断点打在两者之间：静态那块保住了，messages 照样全丢。
    所以现在它是 mid-conversation system message，坐在历史之后，变了只作废它自己。
    """
    payload = _fake_anthropic([{"role": "system", "content": "静态指令" * 20},
                               {"role": "user", "content": "在么"},
                               {"role": "system", "content": "84 个节点、44 条关系"}])[0]

    blocks = payload["system"]
    assert len(blocks) == 1, blocks                       # 顶层只剩不会变的那段
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}, blocks[0]
    assert "84 个节点" not in blocks[0]["text"], blocks[0]["text"][-60:]

    msgs = payload["messages"]
    assert [m["role"] for m in msgs] == ["user", "system"], msgs
    assert msgs[1]["content"] == "84 个节点、44 条关系", msgs[1]
    # 断点打在最后一条**非 system** 上：会变的那条留在断点之后，它怎么变都不动前面的缓存
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral"}, msgs[0]
    assert "cache_control" not in msgs[1], msgs[1]


@case
def llm_模型不认中途system就折进user重发():
    """Sonnet 5 不支持 mid-conversation system message（400），Opus 5 支持。

    与其维护一张"哪个模型行"的表（一定会过期），不如撞上 400 再退一步。
    退化的是安全性和缓存，不是对话本身。
    """
    sent = _fake_anthropic([{"role": "system", "content": "静态指令" * 20},
                            {"role": "user", "content": "在么"},
                            {"role": "system", "content": "84 个节点"}],
                           fail_first="LLM 请求失败 HTTP 400：role 'system' is not supported on this model")
    assert len(sent) == 2, "没重发"
    assert [m["role"] for m in sent[1]["messages"]] == ["user"], sent[1]["messages"]
    assert "<system-reminder>" in str(sent[1]["messages"][0]["content"]), sent[1]["messages"][0]
    assert "84 个节点" in str(sent[1]["messages"][0]["content"]), sent[1]["messages"][0]


@case
def llm_嵌套着跑claude_cli要提前报警():
    """`claude -p` 不能嵌套，而它是零配置的默认 provider——不提前说就要等第一句话才炸。

    但**只在真有角色指向 claude-cli 时才吭声**：配了 anthropic / openai 的人
    嵌套着跑没有任何问题，对他们报警就是狼来了。
    """
    import llm_backend as backend
    cli = {"providers": {"claude-cli": {"type": "claude-cli"}},
           "roles": {"learn": "claude-cli", "review": "claude-cli"}}
    api = {"providers": {"gpt": {"type": "openai", "base_url": "https://x/v1"}},
           "roles": {"learn": "gpt", "review": "gpt"}}
    mixed = {"providers": {"claude-cli": {"type": "claude-cli"},
                           "gpt": {"type": "openai", "base_url": "https://x/v1"}},
             "roles": {"learn": "gpt", "review": "claude-cli"}}
    old = os.environ.get("CLAUDECODE")
    try:
        os.environ.pop("CLAUDECODE", None)
        assert backend.nested_cli_warning(cli) == "", "不在 Claude Code 里也报警"
        os.environ["CLAUDECODE"] = "1"
        assert "learn、review" in backend.nested_cli_warning(cli)
        assert backend.nested_cli_warning(api) == "", "没用 claude-cli 还报警 = 狼来了"
        one = backend.nested_cli_warning(mixed)
        assert "review" in one and "learn、" not in one, one     # 只点名真中招的那个角色
    finally:
        os.environ.pop("CLAUDECODE", None)
        if old is not None:
            os.environ["CLAUDECODE"] = old


@case
def llm_别的400不重发():
    """只在报错确实是这件事时才退一步，别把所有 400 都当成它。"""
    try:
        _fake_anthropic([{"role": "system", "content": "静态" * 20},
                         {"role": "user", "content": "在么"},
                         {"role": "system", "content": "84 个节点"}],
                        fail_first="LLM 请求失败 HTTP 400：credit balance is too low")
    except SystemExit as exc:
        assert "credit balance" in str(exc), exc
    else:
        raise AssertionError("余额不足也重发了一次，白花钱")


@case
def chat_图谱现状挂在队尾而不是混在静态指令里():
    """节点数一变就会让它**前面**的一切作废，所以它必须排在最后一条。

    放顶层 system（哪怕单独成块）也不行：顶层 system 整体排在 messages 之前，
    它一变整段对话的缓存跟着全丢。只有挂在 messages 队尾才只作废它自己。
    """
    c, vault, _ = with_inbox_node()
    original, seen = stub_chat(["好"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "在么"}]})
    finally:
        restore_chat(original)
    roles = [m["role"] for m in seen[0]]
    assert roles == ["system", "user", "system"], roles      # 静态在头、会变的在尾
    assert "图谱现在是什么样" in seen[0][-1]["content"], seen[0][-1]["content"][:80]
    assert "个节点" in seen[0][-1]["content"], seen[0][-1]["content"][:80]
    # 静态那条里**提到**这一节是可以的（口径提示词要给模型指路），
    # 不能有的是**渲染出来的数据本身**——断言盯的是数据，不是措辞
    assert "（建新项目前先看这里）" not in seen[0][0]["content"], "项目列表会变，不该留在静态那条"
    assert "条关系，其中" not in seen[0][0]["content"], "节点数/边数会变，不该留在静态那条"


@case
def chat_第二轮的前缀和第一轮逐字节一样():
    """**缓存命中的全部前提就这一条**，这里钉的是它，不是"断点打在哪"。

    断点位置对、前缀却每轮都变，一样是零命中——而且不报错、答案全对，只有账单在涨
    （2026-09-16 烧掉 $8，2026-09-17 把图谱快照挪到队尾又复发过一次）。
    这条用例就是那两次的护栏：**第二轮必须原样包含第一轮的全部内容，位置都不许动。**

    线上"真的命中了"由账本的读写比回答（core.usage.cache_health），测试够不着真实 API。
    """
    import llm_backend as backend
    c, vault, _ = with_inbox_node()

    def 发一轮(msgs):
        original, seen = stub_chat(["好"])
        try:
            c.post("/api/chat", json={"messages": msgs, "session": "s1", "stance": "教练"})
        finally:
            restore_chat(original)
        return seen[0]      # 不再 _hoist_system：只增不改现在由 chat._assemble 保证（复盘 §11.2）

    第一轮 = 发一轮([{"role": "user", "content": "第一句"}])
    第二轮 = 发一轮([{"role": "user", "content": "第一句"},
                  {"role": "assistant", "content": "好"},
                  {"role": "user", "content": "第二句"}])

    n = len(第一轮)
    assert len(第二轮) > n, "第二轮反而没变长，测试自己搭错了"
    for i, (a, b) in enumerate(zip(第一轮, 第二轮)):
        assert a["role"] == b["role"], f"第 {i} 条角色变了：{a['role']} → {b['role']}"
        assert a["content"] == b["content"], (
            f"第 {i} 条（{a['role']}）内容变了，前缀断在这里 —— "
            f"要么系统提示里混进了会变的东西，要么有块被挤到了别的位置。\n"
            f"  第一轮：{a['content'][:120]}\n  第二轮：{b['content'][:120]}")

    # 两轮的顶层 system 也必须逐字节一样：它排在所有 messages 之前，一变整段全作废
    assert backend._split_system(第一轮)[0] == backend._split_system(第二轮)[0], "顶层 system 变了"


@case
def chat_第二轮接着上一轮的完整上下文往下发():
    """**这一条是 §11.2 的护栏。**

    前端回传的是 `strip_tools` 之后的可见轮次，服务端内部那份还夹着工具往返。
    以前每轮都拿前端那份重建上下文，于是**只要上一轮调过工具，前缀必然对不上**，
    claude-cli 的 `--resume` 就永远续不上（67 段真实会话里 54 段只有一次往返 =
    每次都整段重发、全额重写缓存）。现在改成从服务端缓存续，工具往返留在上下文里。
    """
    c, vault, _ = with_inbox_node()

    original, _ = stub_chat([tool_block("overview", {}), "看完了"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "图里有啥"}],
                                  "session": "s1"})
    finally:
        restore_chat(original)

    original, seen = stub_chat(["接着说"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "图里有啥"},
                                               {"role": "assistant", "content": "看完了"},
                                               {"role": "user", "content": "再说说"}],
                                  "session": "s1"})
    finally:
        restore_chat(original)

    sent = seen[0]
    assert any(m.get("role") == "tool" and m.get("name") == "overview" for m in sent), \
        "上一轮的工具往返没带过来——那就是又整段重建了一次，--resume 必然续不上"
    assert sent[-1]["content"] == "再说说", [m["role"] for m in sent]
    # 只增不改：新的一句必须接在后面，前面那截一个字都不许动
    assert sent[1]["content"] == "图里有啥", sent[1]["content"][:60]


@case
def chat_历史被改过就退回整段重建():
    """续接只在**能证明这一段没被改过**时才走。

    对不上就老老实实重建——续错一段（模型看着别人的上下文答题）比多花那点钱糟得多。
    """
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([tool_block("overview", {}), "看完了"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "图里有啥"}],
                                  "session": "s1"})
    finally:
        restore_chat(original)

    original, seen = stub_chat(["重来"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "图里有啥"},
                                               {"role": "assistant", "content": "我改过这句"},
                                               {"role": "user", "content": "再说说"}],
                                  "session": "s1"})
    finally:
        restore_chat(original)

    sent = seen[0]
    assert not any("[工具" in (m.get("content") or "") for m in sent), "改过历史还在续，危险"
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user", "system"], \
        [m["role"] for m in sent]


@case
def chat_图谱没变就不重复贴快照():
    """贴一条就动一次前缀。**「建一个节点就换一次前缀」正是这么来的**——

    快照原来每轮都重贴一份，位置还会随历史长度漂。现在只在内容真变了时才追加。
    """
    c, vault, _ = with_inbox_node()

    def 发一轮(msgs):
        original, seen = stub_chat(["好"])
        try:
            c.post("/api/chat", json={"messages": msgs, "session": "s1"})
        finally:
            restore_chat(original)
        return seen[0]

    from server.chat import SNAP_HEAD
    快照数 = lambda sent: sum(1 for m in sent if (m.get("content") or "").startswith(SNAP_HEAD))

    assert 快照数(发一轮([{"role": "user", "content": "一"}])) == 1
    二 = 发一轮([{"role": "user", "content": "一"}, {"role": "assistant", "content": "好"},
                {"role": "user", "content": "二"}])
    assert 快照数(二) == 1, "图谱没变还多贴了一份快照，前缀白断"

    core.write(vault / "nodes/组A/e.md", node_md("E"))
    index_service.invalidate()
    三 = 发一轮([{"role": "user", "content": "一"}, {"role": "assistant", "content": "好"},
                {"role": "user", "content": "二"}, {"role": "assistant", "content": "好"},
                {"role": "user", "content": "三"}])
    assert 快照数(三) == 2, "建了节点却没补新快照，模型看到的还是旧数字"
    # 老那份必须原地不动：它已经在缓存前缀里了
    assert 三[2]["content"] == 二[2]["content"], "旧快照被改写了，前缀断在这里"


@case
def llm_单轮功能的低比值不该报警():
    """出题 / 关系建议那类一问一答每次都是新前缀，比值天然贴着 0。

    把它们算进告警，等于天天在响——**一个天天响的告警等于没有告警**，
    真正该看的多轮对话反而被淹了。
    """
    import datetime as _dt
    from core import usage as usage_mod

    # **窗口是今天、数据取自 `recent`**（`by_day` 没有 op 维度）。
    # 这条用例一度还在喂 `by_op`，于是 `worst` 恒为 None——断言全部空转，
    # 谁也没发现监控已经不看它了。造数据必须走和线上同一条路。
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def rows(op: str, n: int, read: int, write: int) -> list[dict]:
        return [{"op": op, "ts": ts, "ok": True,
                 "cache_read_tokens": read, "cache_write_tokens": write} for _ in range(n)]

    log = {"recent": rows("suggest", 9, 0, 1000) + rows("chat-教练", 9, 10000, 1000)}
    health = usage_mod.cache_health(log)
    assert health["ok"], health                      # 单轮的 0 比值不算数
    assert health["worst"]["op"] == "chat-教练", health

    log = {"recent": rows("suggest", 9, 0, 1000) + rows("chat-教练", 9, 1000, 1000)}
    assert not usage_mod.cache_health(log)["ok"], "多轮比值掉到 1 了还说健康"

    log = {"recent": rows("suggest", 9, 0, 1000) + rows("chat-教练", 2, 1000, 1000)}
    assert usage_mod.cache_health(log)["ok"], "才 2 次调用就报警，噪声"


@case
def llm_换了provider之后缓存那盏灯不能跟着熄():
    """**指标随 provider 口径换**：只报命中数的那一路（OpenAI 兼容）没有"写"这一列。

    读写比写死在页面上的后果是 2026-09-21 那天：换到千问之后整天显示 `—`、`ok` 恒为真，
    既不会红也不会绿。一盏不会动的灯等于没有灯——而缓存失效恰恰是**唯一**不报错的故障。
    """
    import datetime as _dt
    from core import usage as usage_mod
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def rows(op: str, n: int, read: int, inp: int) -> list[dict]:
        """OpenAI 兼容口径：`cache_write_tokens` 恒为 0，输入全额记在 `input_tokens` 里。"""
        return [{"op": op, "ts": ts, "ok": True, "input_tokens": inp,
                 "cache_read_tokens": read, "cache_write_tokens": 0} for _ in range(n)]

    good = usage_mod.cache_health({"recent": rows("chat-教练", 9, 9000, 10000)})
    assert good["kind"] == "hit_rate", good        # kind 就是字段名，界面直接拿它取值
    assert good["ratio"] is None, good             # 这一路永远除不出读写比——原来就卡在这
    assert good["hit_rate"] == 0.9 and good["ok"], good

    bad = usage_mod.cache_health({"recent": rows("chat-教练", 9, 3000, 10000)})
    assert not bad["ok"], "命中率掉到 30% 了还说健康"
    assert bad["worst"]["op"] == "chat-教练" and bad["worst"]["hit_rate"] == 0.3, bad["worst"]
    assert bad["worst"]["ratio"] is None, bad["worst"]   # 两套口径不混排名

    quiet = usage_mod.cache_health({"recent": rows("chat-教练", 2, 0, 10000)})
    assert quiet["ok"], "才 2 次调用就报警，噪声"

    # 报写入的那一路照旧看读写比：别为了新口径把老的挤掉（混着用的那天两个数都在）
    cli = usage_mod.cache_health({"recent": [
        {"op": "chat-教练", "ts": ts, "ok": True, "input_tokens": 2,
         "cache_read_tokens": 10000, "cache_write_tokens": 1000} for _ in range(9)]})
    assert cli["kind"] == "ratio" and cli["ratio"] == 10.0, cli
    assert cli["hit_rate"] is None, cli            # 5000× 那种假数不许出现在契约里


@case
def usage_不报价的provider不该摆出一个0元():
    """`cost_known` **只看当前角色指向谁**，不看账本里有没有历史金额。

    原来是 `bool(totals.cost_usd) or 报价能力`：换到千问之后，前几天 claude-cli 烧的那 $23
    会把开关顶成真，于是页面显示"今天 $0.00""每张落地的卡 ≈ $0.00"——
    那不是今天没花钱，是这个 provider 压根不报价。
    """
    c, vault, _ = with_inbox_node()
    home = llm_home()      # 模型配置是用户级的，不在库里
    core.write(home / "llm.local.json", json.dumps({
        "providers": {"bailian": {"type": "openai", "base_url": "http://x/v1", "model": "qwen-plus"}},
        "roles": {"learn": "bailian", "review": "bailian"}}, ensure_ascii=False))
    core.record_usage(vault, {"op": "chat", "ok": True, "ms": 10, "cost_usd": 23.0,
                              "input_tokens": 2, "cache_read_tokens": 100,
                              "cache_write_tokens": 50})
    u = c.get("/api/llm/usage").json()
    assert u["totals"]["cost_usd"] == 23.0, u["totals"]     # 历史金额还在，不许抹
    assert not u["cost_known"], "不报价的 provider 还在摆 $"

    core.write(home / "llm.local.json", json.dumps({
        "providers": {"cli": {"type": "claude-cli"}},
        "roles": {"learn": "cli", "review": "cli"}}, ensure_ascii=False))
    assert c.get("/api/llm/usage").json()["cost_known"], "claude-cli 会报价，这时该显示"


@case
def chat_换了口径或项目就不是同一段会话():
    """口径换了系统提示词就换了，项目换了图的范围也变了——续同一段会让模型看着别人的上下文答题。"""
    from server import chat as chat_mod
    from server.contracts import ChatRequest
    msg = [{"role": "user", "content": "在么"}]
    keys = {chat_mod.llm_session_key(ChatRequest(messages=msg, session="s1", project=p, stance=st))
            for p, st in (("llm", "教练"), ("llm", "面试"), ("ai", "教练"))}
    assert len(keys) == 3, keys
    solo = chat_mod.llm_session_key(ChatRequest(messages=msg))
    assert solo == "_scratch|_|教练", solo


@case
def llm_anthropic给系统提示和对话末尾打缓存断点():
    """system 是整条链路上最大也最稳定的一段，不标 cache_control 就是每轮原价重买一次。

    末尾那个断点管的是工具往返：一轮里三四次调用之间只差几百个字，
    断点打在末尾，后面几次就都是缓存命中。
    """
    import llm_backend as backend
    seen: dict = {}

    def fake_post(url, headers, payload):
        seen["payload"] = payload
        return {"content": [{"type": "text", "text": "好"}], "usage": {"input_tokens": 1}}

    original = backend._post_json
    backend._post_json = fake_post
    try:
        backend._chat_anthropic([{"role": "system", "content": "S" * 50},
                                 {"role": "user", "content": "A"},
                                 {"role": "assistant", "content": "B"},
                                 {"role": "user", "content": "C"}],
                                {"api_key": "k"}, "claude-haiku-4-5-20251001", None, None)
    finally:
        backend._post_json = original

    payload = seen["payload"]
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}, payload["system"]
    msgs = payload["messages"]
    assert msgs[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}, msgs[-1]
    assert msgs[0]["content"] == "A", msgs[0]          # 只在末尾打一个，中间的照旧


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
    last = seen[1][-1]
    assert last["role"] == "tool" and last["name"] == "search_nodes", last
    assert "a" in last["content"], last
    # 调用那一条也要在：tool_result 得能找回它的 tool_use，否则整段上下文是坏的
    assert seen[1][-2]["tool_calls"][0]["id"] == last["tool_call_id"], seen[1][-2:]
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
def vault_config_原文目录默认articles_坏文件回落默认():
    """原文目录配在库里（.knowrary/vault.json，跟着 git 走）。文件坏了不能让库打不开。"""
    c, vault = client()
    got = c.get("/api/vault/config").json()
    assert got["articles_dir"] == "articles" and got["articles"] == 0, got
    (vault / ".knowrary").mkdir(exist_ok=True)
    (vault / ".knowrary" / "vault.json").write_text("{坏的", "utf-8")
    assert c.get("/api/vault/config").json()["articles_dir"] == "articles"
    (vault / ".knowrary" / "vault.json").write_text(json.dumps({"articles_dir": "nodes/偷渡"}), "utf-8")
    assert c.get("/api/vault/config").json()["articles_dir"] == "articles", "不合法的值该回落默认"


@case
def vault_config_只收库内相对路径_不许进节点层():
    c, vault = client()
    for bad, word in (("", "空"), ("/tmp/x", "相对路径"), ("~/文章", "相对路径"), ("../外面", ".."),
                      ("nodes/长文", "节点"), ("fields", "节点"), (".knowrary/x", "程序"), ("assets", "程序"),
                      ("C:/x", "相对路径")):
        r = c.put("/api/vault/config", json={"articles_dir": bad})
        assert r.status_code == 422 and word in r.json()["detail"]["message"], (bad, r.text)
    (vault / "一个文件.md").write_text("x", "utf-8")
    r = c.put("/api/vault/config", json={"articles_dir": "一个文件.md"})
    assert r.status_code == 422 and "文件" in r.json()["detail"]["message"], r.text
    assert not (vault / ".knowrary" / "vault.json").exists(), "拒绝了还写盘"

    r = c.put("/api/vault/config", json={"articles_dir": "day-info/长文", "dry_run": True})
    assert r.status_code == 200 and r.json()["articles_dir"] == "day-info/长文", r.text
    assert not (vault / "day-info").exists() and not (vault / ".knowrary" / "vault.json").exists(), "dry_run 写了盘"
    r = c.put("/api/vault/config", json={"articles_dir": " day-info\\长文/ "})
    assert r.status_code == 200 and r.json()["articles_dir"] == "day-info/长文", r.text
    assert (vault / "day-info" / "长文").is_dir(), "保存时该把目录建出来"
    doc = json.loads((vault / ".knowrary" / "vault.json").read_text("utf-8"))
    assert doc == {"schema_version": 1, "articles_dir": "day-info/长文"}, doc
    assert "day-info" in c.get("/api/vault/config").json()["candidates"]
    assert not any(x.split("/")[0] in ("nodes", "fields", "assets") for x in
                   c.get("/api/vault/config").json()["candidates"]), "候选里混进了保留目录"


@case
def vault_config_旧目录有文章要明说搬_搬前先备份_撞名一篇不动():
    c, vault = client()
    (vault / "articles" / "2026").mkdir(parents=True)
    (vault / "articles" / "BPE全景.md").write_text("# BPE 全景\n\n原文", "utf-8")
    (vault / "articles" / "2026" / "注意力.md").write_text("# 注意力\n", "utf-8")
    assert c.get("/api/vault/config").json()["articles"] == 2

    r = c.put("/api/vault/config", json={"articles_dir": "长文", "dry_run": True})
    assert r.status_code == 409, "边打边问时就该知道要不要搬"
    r = c.put("/api/vault/config", json={"articles_dir": "长文"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["articles"] == 2 and r.json()["detail"]["from"] == "articles"
    assert c.get("/api/vault/config").json()["articles_dir"] == "articles", "没说搬就改了指向"

    (vault / "长文").mkdir()
    (vault / "长文" / "BPE全景.md").write_text("另一篇", "utf-8")
    r = c.put("/api/vault/config", json={"articles_dir": "长文", "move": True})
    assert r.status_code == 422 and "同名" in r.json()["detail"]["message"], r.text
    assert (vault / "articles" / "BPE全景.md").exists() and (vault / "articles/2026/注意力.md").exists(), \
        "撞名时整批都不该动"
    (vault / "长文" / "BPE全景.md").unlink()

    r = c.put("/api/vault/config", json={"articles_dir": "articles/子目录", "move": True})
    assert r.status_code == 422 and "套着" in r.json()["detail"]["message"], r.text

    r = c.put("/api/vault/config", json={"articles_dir": "长文", "move": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert sorted(data["moved"]) == ["长文/2026/注意力.md", "长文/BPE全景.md"], data
    assert data["articles_dir"] == "长文" and data["articles"] == 2
    assert (vault / "长文" / "BPE全景.md").read_text("utf-8").endswith("原文")
    assert not core.list_articles(vault / "articles"), "搬完旧目录里还有"
    assert (vault / data["backup"] / "articles" / "BPE全景.md").exists(), data["backup"]


@case
def chat_卡片跟着留档回来_点过的标已点_撞车的标过期():
    """2026-09-23：在 AI 史里点了一张改 Transformer 清单的卡，页面跳去了 Transformer，
    再切回来，同一轮里还没点的三张变更卡全没了——卡片只活在前端内存里，留档只记正文。

    现在卡跟着 assistant 那一行留档；读回时点没点从 cards.jsonl 现算，
    没点的变更卡按**现在的文件**重算 diff，算不过去的标 `expired`。
    """
    c, vault, _ = with_inbox_node()
    born = {"type": "create_node", "source": "x", "path": "nodes/组A/x.md",
            "fields": {"name": "x", "field": "测试", "desc": "新点"}}
    original, _ = stub_chat([
        tool_blocks(("propose_changes", {"changes": [born]}),
                    ("propose_changes", {"changes": [{**born, "fields": {**born["fields"], "desc": "另一版"}}]}),
                    ("propose_changes", {"changes": [
                        {"type": "add_edge", "source": "a", "relation": "相关", "target": "b"}]})),
        "三张卡给你了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "存进去"}],
                                      "session": "s1"})
    finally:
        restore_chat(original)
    live = [e["card"] for e in sse_events(r) if e["type"] == "card"]
    assert len(live) == 3, [e["type"] for e in sse_events(r)]

    def cards_back():
        msgs = c.get("/api/chat/history", params={"session": "s1"}).json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"], msgs
        assert msgs[0]["cards"] == [], "卡挂到了我那一行上"
        return [ev["card"] for ev in msgs[1]["cards"]]

    back = cards_back()
    assert [b["card_id"] for b in back] == [x["card_id"] for x in live]
    assert not any(b["applied"] for b in back) and all(b["files"] for b in back), back

    first = live[0]
    w = c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                     "dry_run": False, "changes": first["changes"],
                                     "card": first["card_id"]})
    assert w.status_code == 200 and w.json()["applied"], w.text

    back = cards_back()
    assert back[0]["applied"] and not back[0].get("expired"), back[0]
    # 第二张也要建 x：x 已经在了，照原样写不进去——卡还摆着，但标上为什么
    assert not back[1]["applied"] and back[1].get("expired"), back[1]
    # 第三张和 x 无关：diff 按现在的文件重算，base_revision 跟着换成现在的
    assert not back[2]["applied"] and not back[2].get("expired"), back[2]
    assert back[2]["base_revision"] == c.get("/api/index").json()["revision"]


@case
def cards_流水裁剪时采纳事件一条不裁():
    """点没点是从流水现算的：老的 applied 被裁掉，几个月前点过的卡读回来就又成了"没点"。"""
    cards_mod = sys.modules["core.cards"]

    _, vault = client()
    keep = cards_mod.KEEP
    cards_mod.KEEP = 5
    try:
        core.card_proposed(vault, "old", "changes")
        core.card_applied(vault, "old")
        for k in range(12):
            core.card_proposed(vault, f"n{k}", "changes")
    finally:
        cards_mod.KEEP = keep
    rows = core.load_cards(vault)
    assert {"id": "old", "event": "applied"}.items() <= next(
        r for r in rows if r["id"] == "old").items(), rows
    assert not any(r["id"] == "old" and r["event"] == "proposed" for r in rows), "该裁的没裁"


@case
def chat_断在半路的那一轮也留下已经摆出来的卡():
    """卡片是边跑边推给前端的：模型出完卡之后才挂，屏幕上那几张照样能点，读回来也得在。"""
    from server import chat as chat_mod

    c, vault, _ = with_inbox_node()
    original = chat_mod.llm_chat
    step = [0]

    def flaky(vault, role, messages, tools=None, op="chat", on_delta=None, session=None):
        step[0] += 1
        if step[0] > 1:
            raise RuntimeError("上游断了")
        blk = tool_block("propose_changes", {"changes": [
            {"type": "add_edge", "source": "a", "relation": "相关", "target": "b"}]})
        return blk["text"], blk["calls"], {}

    chat_mod.llm_chat = flaky
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "存"}], "session": "s2"})
    finally:
        chat_mod.llm_chat = original
    assert [e for e in sse_events(r) if e["type"] == "card"], [e["type"] for e in sse_events(r)]
    msgs = c.get("/api/chat/history", params={"session": "s2"}).json()["messages"]
    assert msgs[-1]["failed"] and len(msgs[-1]["cards"]) == 1, msgs[-1]


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
    # 没绑项目的对话落进 _scratch：「对话」是默认入口，冷启动时一个项目都还没有
    assert path.parent.name == chat_mod.SCRATCH and path.suffix == ".jsonl", path


@case
def chat_留档按项目分目录():
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat(["主线的事"])
    try:
        c.post("/api/chat", json={"project": "llm", "messages": [{"role": "user", "content": "讲讲 a"}]})
    finally:
        restore_chat(original)
    from server import chat as chat_mod
    assert chat_mod.chat_log_path(vault, "llm").exists(), "没按项目分目录"
    assert not chat_mod.chat_log_path(vault).exists(), "绑了项目还往 _scratch 里写"
    # 认不出的项目名一律归 _scratch，绝不让它拼出路径
    assert chat_mod.chat_log_path(vault, "../../etc").parent.name == chat_mod.SCRATCH


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
def chat_刷新后能把最近几轮读回来():
    """会话状态在前端，刷一下就没了——但留档一直在，读回来就能接着聊。"""
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat(["自注意力是这样的…"])
    try:
        c.post("/api/chat", json={"project": "llm", "messages": [{"role": "user", "content": "讲讲 a"}]})
    finally:
        restore_chat(original)
    got = c.get("/api/chat/history?project=llm").json()["messages"]
    assert [m["role"] for m in got] == ["user", "assistant"], got
    assert got[0]["content"] == "讲讲 a" and "自注意力" in got[1]["content"], got
    # 项目之间不串：另一个项目读出来是空的
    assert c.get("/api/chat/history?project=other").json()["messages"] == []
    assert c.get("/api/chat/history").json()["messages"] == []      # 没绑项目的那条线也是独立的


@case
def chat_多段会话各自独立且列表是聚合出来的():
    """会话不是一张表：它只是留档行上的一个标签，列表从行里聚合。"""
    c, vault, _ = with_inbox_node()
    for sid, q in (("s1", "讲讲 a"), ("s1", "再说说 b"), ("s2", "模拟面试开始")):
        original, _ = stub_chat([f"回答 {q}"])
        try:
            c.post("/api/chat", json={"project": "llm", "session": sid,
                                      "messages": [{"role": "user", "content": q}]})
        finally:
            restore_chat(original)

    rows = c.get("/api/chat/sessions?project=llm").json()["sessions"]
    assert [r["id"] for r in rows] == ["s2", "s1"], rows        # 最近聊的排前面
    assert rows[1]["turns"] == 4 and rows[0]["turns"] == 2, rows
    assert rows[1]["title"] == "讲讲 a", rows[1]                 # 标题取第一句我说的话

    # 不给 session 就取最近那一段——绝大多数时候人想接着的就是它
    last = c.get("/api/chat/history?project=llm").json()["messages"]
    assert [m["content"] for m in last] == ["模拟面试开始", "回答 模拟面试开始"], last
    # 指定了就只读那一段
    s1 = c.get("/api/chat/history?project=llm&session=s1").json()["messages"]
    assert len(s1) == 4 and s1[0]["content"] == "讲讲 a", s1
    # 会话表不存在：留档里只有一行行带标签的记录
    from server import chat as chat_mod
    raw = chat_mod.chat_log_path(vault, "llm").read_text("utf-8")
    assert '"session": "s1"' in raw and "sessions" not in raw


@case
def chat_会话归档是贴纸删除是真删():
    """归档只是收起来：留档一行不动，列表上标 archived，默认接着聊的那段跳过它。
    删除是真删：这段的行从月档里剔掉，改名 / 游标 / 归档三张贴纸一并撕掉，别的会话不受影响。"""
    c, vault, _ = with_inbox_node()
    for sid, q in (("s1", "讲讲 a"), ("s2", "讲讲 b")):
        original, _ = stub_chat([f"回答 {q}"])
        try:
            c.post("/api/chat", json={"project": "llm", "session": sid,
                                      "messages": [{"role": "user", "content": q}]})
        finally:
            restore_chat(original)
    from server import chat as chat_mod
    log = chat_mod.chat_log_path(vault, "llm")
    before = log.read_text("utf-8")

    r = c.patch("/api/chat/sessions/s2", json={"project": "llm", "archived": True}).json()
    assert r["archived"] and "title" not in r, r
    assert log.read_text("utf-8") == before, "归档动了留档"
    rows = {s["id"]: s for s in c.get("/api/chat/sessions?project=llm").json()["sessions"]}
    assert rows["s2"]["archived"] and rows["s1"]["archived"] is None, rows
    # 刚收起来的那段不能在刷新后又自己弹回来
    last = c.get("/api/chat/history?project=llm").json()["messages"]
    assert last[0]["content"] == "讲讲 a", last
    # 点名要它还是读得到
    assert c.get("/api/chat/history?project=llm&session=s2").json()["messages"]
    c.patch("/api/chat/sessions/s2", json={"project": "llm", "archived": False})
    rows = {s["id"]: s for s in c.get("/api/chat/sessions?project=llm").json()["sessions"]}
    assert rows["s2"]["archived"] is None, rows

    # 删除：先贴上三张贴纸，删完都该撕干净
    c.patch("/api/chat/sessions/s1", json={"project": "llm", "title": "改过名"})
    c.patch("/api/chat/sessions/s1", json={"project": "llm", "archived": True})
    upto = c.get("/api/chat/history?project=llm&session=s1").json()["messages"][-1]["ts"]
    c.post("/api/chat/tidied", json={"session": "s1", "upto": upto, "project": "llm"})
    r = c.delete("/api/chat/sessions/s1?project=llm")
    assert r.status_code == 200 and r.json()["deleted"] == 2, r.text
    assert '"session": "s1"' not in log.read_text("utf-8")
    assert '"session": "s2"' in log.read_text("utf-8"), "删一段把别的也删了"
    for name in ("titles.json", "tidied.json", "archived.json"):
        assert "s1" not in core.load_json(log.parent / name), name
    assert [s["id"] for s in c.get("/api/chat/sessions?project=llm").json()["sessions"]] == ["s2"]
    assert c.delete("/api/chat/sessions/s1?project=llm").status_code == 404

    # 删到一个月档一行不剩，文件本身也不留
    c.delete("/api/chat/sessions/s2?project=llm")
    assert not log.exists()


@case
def chat_超过两个月的会话不会从列表里消失():
    """原来只往前翻两个月：老会话悄悄消失、切不回去，文件明明还在。现在收起靠手动归档。"""
    c, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    old = chat_mod.chat_log_path(vault, "llm", dt.date(2024, 1, 15))
    old.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": "2024-01-15T10:00:00+08:00", "role": "user", "text": "很久以前", "session": "old"},
            {"ts": "2024-01-15T10:00:05+08:00", "role": "assistant", "text": "嗯", "session": "old"}]
    old.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    ids = [s["id"] for s in c.get("/api/chat/sessions?project=llm").json()["sessions"]]
    assert ids == ["old"], ids
    assert len(c.get("/api/chat/history?project=llm&session=old").json()["messages"]) == 2


@case
def chat_回答里带回聊到的节点():
    """node_ids 从调试信息升级成了界面契约：「聊到哪、图上亮哪」靠它。"""
    c, _, _ = with_inbox_node()
    original, _ = stub_chat(["a 和 b 是这么回事"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "讲讲"}]})
    finally:
        restore_chat(original)
    done = [e for e in sse_events(r) if e["type"] == "done"][0]
    assert set(done["node_ids"]) == {"a", "b"}, done["node_ids"]


@case
def chat_口径决定提示词与工具白名单():
    """三档口径 ≠ 三个 agent：同一条链路，换的是提示词和能用哪几个工具。"""
    c, _, _ = with_inbox_node()
    seen = {}
    from server import chat as chat_mod
    original = chat_mod.llm_chat

    def spy(vault, role, messages, tools=None, op="chat", on_delta=None, session=None):
        seen[op] = messages[0]["content"]
        seen[op + "#tools"] = [t["name"] for t in tools or []]
        if on_delta:
            on_delta("知道了")
        return "知道了", [], {}

    chat_mod.llm_chat = spy
    try:
        for stance in ("教练", "面试", "聊天"):
            c.post("/api/chat", json={"stance": stance,
                                      "messages": [{"role": "user", "content": "在吗"}]})
    finally:
        chat_mod.llm_chat = original

    # 用量按口径分开记，否则算不清面试烧了多少
    assert {k for k in seen if not k.endswith("#tools")} == {"chat-教练", "chat-面试", "chat-聊天"}, list(seen)
    assert "学习教练" in seen["chat-教练"] and "面试官" in seen["chat-面试"], "提示词没换"
    # 工具表是从白名单渲染的：说明书和实际权限是同一份数据
    assert "propose_changes" in seen["chat-教练"]
    assert "propose_changes" not in seen["chat-面试"], "面试口径把入库工具写进说明书了"
    assert "today" not in seen["chat-聊天"], "聊天口径不该有调度类工具"
    assert "quiz" in seen["chat-面试"] and "quiz" not in seen["chat-聊天"]
    # **schema 和说明书是同一份数据**：白名单收走的工具，模型那边也必须真的看不到定义
    assert "propose_changes" not in seen["chat-面试#tools"], seen["chat-面试#tools"]
    assert "today" not in seen["chat-聊天#tools"], seen["chat-聊天#tools"]
    assert set(seen["chat-教练#tools"]) == set(chat_mod.TOOLS_SPEC), seen["chat-教练#tools"]


@case
def chat_面试口径调不动入库工具():
    """说明书里没有，实际也必须调不动——只写在提示词里等于没限制。"""
    c, vault, _ = with_inbox_node()
    before = {str(p): p.read_text("utf-8") for p in vault.rglob("*.md")}
    changes = [{"type": "add_edge", "source": "a", "relation": "相关", "target": "b"}]
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes}), "那我们继续面"])
    try:
        r = c.post("/api/chat", json={"stance": "面试",
                                      "messages": [{"role": "user", "content": "帮我记一下"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    assert not [e for e in evs if e["type"] == "card"], "面试口径真的出了变更卡"
    tool = [e for e in evs if e["type"] == "tool"][0]
    assert "这一档口径下没有" in tool["summary"], tool["summary"]
    assert {str(p): p.read_text("utf-8") for p in vault.rglob("*.md")} == before

    # 教练口径下同一个调用照常能用
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes}), "卡给你了"])
    try:
        r2 = c.post("/api/chat", json={"stance": "教练",
                                       "messages": [{"role": "user", "content": "帮我记一下"}]})
    finally:
        restore_chat(original)
    assert [e for e in sse_events(r2) if e["type"] == "card"], "教练口径也被挡了"


@case
def chat_能提议建项目但不落盘():
    """全局对话要能"帮我建个项目"——但仍然只提议，卡片点了才写（4.4）。"""
    c, vault, _ = with_inbox_node()
    args = {"id": "nlp", "name": "NLP 方向", "field": "AI", "weekly_hours": 9,
            "lists": [{"kind": "学习", "name": "主线", "goal": "吃透 Transformer",
                       "target_date": "2026-12-15"},
                      {"kind": "面试", "name": "字节一面", "goal": "JD"}]}
    original, _ = stub_chat([tool_block("propose_project", args), "卡片放你那儿了，点了才建"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "帮我建个 NLP 项目"}]})
    finally:
        restore_chat(original)
    cards = [e for e in sse_events(r) if e["type"] == "project"]
    assert len(cards) == 1, [e["type"] for e in sse_events(r)]
    card = cards[0]["project"]
    assert card["action"] == "create" and card["id"] == "nlp" and card["weekly_hours"] == 9, card
    assert [ls["kind"] for ls in card["lists"]] == ["学习", "面试"], card["lists"]
    assert card["lists"][0]["stages"] == [], "清单该是空的——拆点是另一步"
    # **没落盘**
    assert c.get("/api/projects").json()["doc"]["projects"] == {}, "提议就写进去了"

    # 中文 id 会成为文件名和目录名，一律退回去让模型改
    original, _ = stub_chat([tool_block("propose_project", {"id": "大模型", "name": "x"}), "好的"])
    try:
        r2 = c.post("/api/chat", json={"messages": [{"role": "user", "content": "建一个"}]})
    finally:
        restore_chat(original)
    assert not [e for e in sse_events(r2) if e["type"] == "project"], "中文 id 也放过去了"
    assert "ASCII" in [e for e in sse_events(r2) if e["type"] == "tool"][0]["summary"]


@case
def chat_建项目时会看见已有的项目():
    """"我有 Transformer 了，又建一个 MHA"——这件事模型得看得见，人也得在按下创建前看见。"""
    c, _, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"transformer": {
        "name": "Transformer", "lists": [{"kind": "学习", "name": "主线", "goal": "吃透 Transformer",
        "stages": [{"name": "一", "points": [{"id": "自注意力"}]}]}]}}})

    seen = {}
    from server import chat as chat_mod
    original = chat_mod.llm_chat

    def spy(vault, role, messages, tools=None, op="chat", on_delta=None, session=None):
        # 两条 system：静态指令 + 图谱现状（会变的那块单独排后面，见 _graph_snapshot）
        seen[op] = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if on_delta:
            on_delta("好")
        return "好", [], {}

    chat_mod.llm_chat = spy
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}]})
    finally:
        chat_mod.llm_chat = original
    brief = seen["chat-教练"]
    assert "我已经有的项目" in brief and "`transformer`" in brief, brief[-400:]
    assert "1 个点" in brief, brief[-400:]

    # 建一个名字接近的：服务端算出"跟已有的撞车"，卡片上带着，话术里也要提
    args = {"id": "transformer-mha", "name": "Transformer MHA", "lists": [{"kind": "学习"}]}
    original2, _ = stub_chat([tool_block("propose_project", args), "你已经有 Transformer 了，确定要另起吗"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "建个 MHA 项目"}]})
    finally:
        restore_chat(original2)
    card = [e for e in sse_events(r) if e["type"] == "project"][0]["project"]
    assert [n["id"] for n in card["near"]] == ["transformer"], card["near"]
    tool = [e for e in sse_events(r) if e["type"] == "tool"][0]
    assert "他已经有" in tool["summary"] or "注意" in tool["summary"], tool["summary"]


@case
def chat_能在对话里把清单拆成点():
    """"帮我建个项目"是一串动作：看已有 → 搜图 → 建项目 → 拆点。
    以前拆点只能去面板，对话里走到一半就断了。"""
    c, _, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"mha": {
        "name": "多头注意力", "weekly_hours": 10,
        "lists": [{"kind": "学习", "name": "主线", "goal": "吃透 MHA", "target_date": "2099-01-01"}]}}})

    from server import projects as projects_mod
    seen = {}
    real = projects_mod.ask

    def spy(vault_, role, prompt, op="?"):
        seen[op] = prompt
        return json.dumps({"stages": [{"name": "一", "points": [
            {"id": "自注意力", "load": "重"}, {"id": "多头注意力"}]}]}, ensure_ascii=False)

    projects_mod.ask = spy
    original, _ = stub_chat([tool_block("propose_points", {"project": "mha", "list": "主线"}),
                             "拆好了，看看卡片"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "帮我把主线拆一下"}]})
    finally:
        restore_chat(original)
        projects_mod.ask = real

    cards = [e for e in sse_events(r) if e["type"] == "points"]
    assert len(cards) == 1, [e["type"] for e in sse_events(r)]
    card = cards[0]["points"]
    assert card["project"] == "mha" and card["list"] == 0, card
    assert [p["id"] for st in card["stages"] for p in st["points"]] == ["自注意力", "多头注意力"]
    # 走的是面板同一条链路：时间账、负荷、阶段截止日一个不少
    assert card["stages"][0]["deadline"], card["stages"][0]
    assert card["schedule"]["total_hours"] == 7.5, card["schedule"]
    # 项目的每周投入被带进去了（10 小时，不是默认 7）
    assert "10 小时" in seen["plan-propose"], seen["plan-propose"][:300]
    # **没落盘**
    got = c.get("/api/projects").json()["doc"]["projects"]["mha"]["lists"][0]["stages"]
    assert got == [], "提议就写进清单了"


def _vault_with_list(c) -> None:
    """一个带清单的项目，给清单卡那几条用例做地基。"""
    c.put("/api/projects", json={"base_revision": 0, "projects": {"ai": {
        "name": "AI 发展史", "weekly_hours": 7,
        "lists": [{"kind": "学习", "name": "主线", "goal": "理清连接主义这条线",
                   "stages": [{"name": "一", "deadline": None, "points": [
                       {"id": "多头注意力", "name": "多头注意力", "load": "中", "why": ""},
                       {"id": "符号主义与连接主义", "name": "两个主义", "load": "中", "why": ""},
                       {"id": "MLA", "name": "MLA", "load": "轻", "why": ""}]}]}]}}})


def _long_note(chars: int = 400) -> str:
    """一篇有小标题的长笔记：三节正文 + 两节不该考的（线头 / 参考资料）。"""
    body = "\n\n".join([
        "# 长笔记\n\n开头这段说它是什么。",
        "## 甲节 讲机制\n" + "甲" * chars,
        "## 乙节 讲取舍\n" + "乙" * chars,
        "## 丙节 讲边界\n" + "丙" * chars,
        "## 线头\n" + "钩" * chars,
        "## 参考资料\n" + "链" * chars,
    ])
    return f"---\nname: 长笔记\nfield: 测试\ndesc: 一句话\n---\n{body}\n\n## 关系\n"


@case
def quiz_长笔记按复习轮换考不同的节():
    """**抽查本来就是采样。** 原来取前 1200 字，17k 那篇永远只考得到前 7%；
    而"每节均分"更糟——分完每节只剩几十字，问不出实质还让模型以为看全了，题会变水。
    所以挑一节给足，下次到期抽另一节，跨几轮覆盖全篇。

    轮换指针用的是 review-log 已有的 `step`（每复习一次 +1）：**不新记任何状态**。
    """
    from server import quiz as quiz_mod
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/long.md", _long_note())
    index_service.invalidate()
    meta = next(n for n in c.get("/api/index").json()["nodes"] if n["id"] == "long")

    picks = quiz_mod._quizable_sections(quiz_mod._read_body(vault, meta))
    titles = [h.title for h in picks]
    assert not any("线头" in t or "参考资料" in t for t in titles), titles   # 这两节不该拿来出题
    assert len(titles) == 3, titles

    seen = [quiz_mod._body_excerpt(vault, meta, quiz_mod.MAX_BODY, k).splitlines()[0] for k in range(3)]
    assert len({*seen}) == 3, seen                       # 三次复习拿到三节，不重样
    assert "只考这一节" in seen[0], seen[0]               # 告诉模型它没看全
    body0 = quiz_mod._body_excerpt(vault, meta, quiz_mod.MAX_BODY, 0)
    assert "甲" * 50 in body0 and "乙" not in body0, "给的不是完整一节"
    # 转一圈回到原处：指针是 step % 节数
    assert seen[0] == quiz_mod._body_excerpt(vault, meta, quiz_mod.MAX_BODY, 3).splitlines()[0]


@case
def quiz_短笔记整篇给行为不变():
    """94 篇里 88 篇在上限之内。**这条路一个字都不该动**——改摘录是为了救长笔记，
    不是为了给所有笔记换一套口径。"""
    from server import quiz as quiz_mod
    c, vault, _ = with_inbox_node()
    meta = next(n for n in c.get("/api/index").json()["nodes"] if n["id"] == "a")
    full = quiz_mod._read_body(vault, meta)
    assert len(full) < quiz_mod.MAX_BODY
    for step in (0, 1, 7):
        assert quiz_mod._body_excerpt(vault, meta, quiz_mod.MAX_BODY, step) == full, step


@case
def quiz_批改按题干挑那一节():
    """批改不能轮换 —— 它要给的是**这道题**的判分依据，挑错节等于没给。
    题干里通常直接带着那一节的词，算一次词重合就够，不用再问一次模型。"""
    from server import quiz as quiz_mod
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/long.md", _long_note())
    index_service.invalidate()
    meta = next(n for n in c.get("/api/index").json()["nodes"] if n["id"] == "long")

    got = quiz_mod._section_for(vault, meta, "乙节讲的那个取舍是什么？", quiz_mod.MAX_SOURCE)
    assert "乙节 讲取舍" in got.splitlines()[0], got.splitlines()[0]
    got = quiz_mod._section_for(vault, meta, "丙节的边界条件有哪些？", quiz_mod.MAX_SOURCE)
    assert "丙节 讲边界" in got.splitlines()[0], got.splitlines()[0]


@case
def cards_摆了几张点了几张算得出来():
    """账本知道花了多少，md 知道最后写进去什么，**中间那一段原来没人记**——
    摆出来几张、点了几张、几张被丢了。采纳率是这套教练唯一真正的产出指标。
    """
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([tool_block("propose_changes", {"changes": [
        {"type": "update_frontmatter", "source": "a", "fields": {"desc": "改过的摘要"}}]}),
        "卡摆好了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "改一下 a"}],
                                      "session": "s1"})
    finally:
        restore_chat(original)
    card = next(e for e in sse_events(r) if e["type"] == "card")["card"]
    assert card["card_id"], card                      # 卡片带 id，采纳才认得回来

    st = core.card_stats(vault)
    assert (st["proposed"], st["applied"]) == (1, 0), st       # 摆出来了，还没点

    # dry_run 不算采纳：卡上改一次摘要就重算一次 diff，带上去会让采纳率虚高
    c.post("/api/changes", json={"base_revision": card["base_revision"], "dry_run": True,
                                 "changes": card["changes"], "card": card["card_id"]})
    assert core.card_stats(vault)["applied"] == 0, "dry_run 被算成采纳了"

    ok = c.post("/api/changes", json={"base_revision": card["base_revision"], "dry_run": False,
                                      "changes": card["changes"], "card": card["card_id"]})
    assert ok.status_code == 200, ok.text
    st = core.card_stats(vault)
    assert (st["proposed"], st["applied"]) == (1, 1), st

    # 面板上手动改的不带 card，不该进分母
    c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                 "dry_run": False,
                                 "changes": [{"type": "update_frontmatter", "source": "b",
                                              "fields": {"desc": "手动改的"}}]})
    assert core.card_stats(vault)["proposed"] == 1, "面板操作混进卡片流水了"

    # 接口上花费和产出拼在一起（两份互不相干的流水，这里才第一次凑到）
    got = c.get("/api/llm/usage").json()
    assert got["cards"]["applied"] == 1 and got["cards"]["total_proposed"] == 1, got["cards"]


@case
def cards_项目那三张卡的采纳也认得回来():
    """项目卡 / 拆点卡 / 清单卡走的是 PUT /api/projects，和变更卡不是同一条路。"""
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([tool_block("propose_project", {
        "id": "nlp", "name": "NLP", "field": "AI",
        "lists": [{"kind": "学习", "name": "主线", "goal": "吃透"}]}), "卡摆好了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "建个 NLP 项目"}]})
    finally:
        restore_chat(original)
    card = next(e for e in sse_events(r) if e["type"] == "project")["project"]
    assert card["card_id"] and card["id"] == "nlp", card     # card_id 和项目 id 是两回事，别撞

    assert core.card_stats(vault)["applied"] == 0
    saved = c.put("/api/projects", json={"base_revision": 0, "card": card["card_id"], "projects": {
        "nlp": {"name": "NLP", "lists": card["lists"]}}})
    assert saved.status_code == 200, saved.text
    assert core.card_stats(vault)["applied"] == 1, core.card_stats(vault)


@case
def chat_能提议改清单里已有的条目():
    """复盘 §11.4：以前模型只能往清单里**加**，撞上"同一个概念两个 id""拆完旧条目还躺着"
    只能说一句"那条得你自己去面板删"——而它的提示词里明写着不许把选择题丢回来。
    """
    c, _, _ = with_inbox_node()
    _vault_with_list(c)
    original, _ = stub_chat([tool_block("propose_list_edit", {"project": "ai", "list": "主线", "edits": [
        {"op": "rename", "id": "多头注意力", "to": "MHA"},
        {"op": "drop", "id": "符号主义与连接主义"},
        {"op": "set", "id": "MLA", "load": "重"}]}), "卡摆好了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "清单里那几条不对"}]})
    finally:
        restore_chat(original)

    cards = [e for e in sse_events(r) if e["type"] == "list_edit"]
    assert len(cards) == 1, [e["type"] for e in sse_events(r)]
    card = cards[0]["list_edit"]
    assert card["project"] == "ai" and card["list"] == 0, card
    assert [(e["op"], e["id"]) for e in card["edits"]] == [
        ("rename", "多头注意力"), ("drop", "符号主义与连接主义"), ("set", "MLA")], card["edits"]
    assert card["edits"][2]["before"] == {"load": "轻"}, card["edits"][2]   # 卡上看得见改之前是什么
    assert card["left"] == 2 and card["empties"] is False, card
    # **没落盘**：和别的卡一个规矩，人点了才算
    got = c.get("/api/projects").json()["doc"]["projects"]["ai"]["lists"][0]["stages"][0]["points"]
    assert [p["id"] for p in got] == ["多头注意力", "符号主义与连接主义", "MLA"], got


@case
def chat_改清单认不出的会退回去而不是硬改():
    """退回时要说清楚**为什么**，模型才改得对；尤其"新 id 已经有了"那条——
    那说明这俩是重复，该删一条，不是改名。"""
    c, _, _ = with_inbox_node()
    _vault_with_list(c)
    original, _ = stub_chat([tool_block("propose_list_edit", {"project": "ai", "list": "主线", "edits": [
        {"op": "rename", "id": "多头注意力", "to": "MLA"},      # 撞上已有的 → 这是重复
        {"op": "drop", "id": "查无此点"},
        {"op": "set", "id": "MLA", "load": "超重"},             # 负荷只有三档
        {"op": "炸了", "id": "MLA"}]}), "都没立住"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "改一下"}]})
    finally:
        restore_chat(original)

    assert not [e for e in sse_events(r) if e["type"] == "list_edit"], "一条都不该立住"
    said = next(e for e in sse_events(r) if e["type"] == "tool")["summary"]
    assert "重复" in said, said
    assert "查无此点" in said, said
    assert "三档" in said, said


@case
def chat_清单删空了要在卡上喊出来():
    """清单没了，项目进度和今日清单跟着全空——这事不能悄悄发生。"""
    c, _, _ = with_inbox_node()
    _vault_with_list(c)
    original, _ = stub_chat([tool_block("propose_list_edit", {"project": "ai", "list": "主线", "edits": [
        {"op": "drop", "id": "多头注意力"}, {"op": "drop", "id": "符号主义与连接主义"},
        {"op": "drop", "id": "MLA"}]}), "清空了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "全删了吧"}]})
    finally:
        restore_chat(original)
    card = next(e for e in sse_events(r) if e["type"] == "list_edit")["list_edit"]
    assert card["empties"] is True and card["left"] == 0, card
    said = next(e for e in sse_events(r) if e["type"] == "tool")["summary"]
    assert "空清单" in said, said


@case
def chat_面试口径改不动清单():
    """面试那一档只读 + 出题：边考边改清单和边考边改图谱是同一件事。"""
    c, _, _ = with_inbox_node()
    _vault_with_list(c)
    original, _ = stub_chat([tool_block("propose_list_edit", {"project": "ai", "edits": [
        {"op": "drop", "id": "MLA"}]}), "改不了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "删了它"}],
                                      "stance": "面试"})
    finally:
        restore_chat(original)
    assert not [e for e in sse_events(r) if e["type"] == "list_edit"], "面试口径居然能改清单"
    assert "没有 `propose_list_edit` 这个工具" in \
        next(e for e in sse_events(r) if e["type"] == "tool")["summary"]


# ---------------------------------------------------------------- 写入审核（三层闸）

AUDIT_LONG = "这一段写得足够长，够过 THIN_BODY 那道线。" * 6


def stub_audit(payload: str):
    """把审核那一次调用换成固定回答：审核链路要能离线测。"""
    from server import audit as audit_mod
    original = audit_mod.ask_meta
    audit_mod.ask_meta = lambda vault, role, prompt, op="?": (payload, {"model": "假审校", "ms": 7})
    return original


def restore_audit(original) -> None:
    from server import audit as audit_mod
    audit_mod.ask_meta = original


def new_node_changes(nid: str = "新点", *, body: str = AUDIT_LONG, edge: bool = True) -> list[dict]:
    out = [{"type": "create_node", "source": nid, "body": body,
            "fields": {"name": nid, "field": "测试", "desc": "摘要"}}]
    if edge:
        out.append({"type": "add_edge", "source": nid, "relation": "依赖", "target": "a"})
    return out


def write_changes(c, changes, **extra):
    rev = c.get("/api/index").json()["revision"]
    return c.post("/api/changes", json={"base_revision": rev, "changes": changes,
                                        "dry_run": False, **extra})


@case
def audit_卡上先审过_写入时不再问第二遍_改过就重审():
    """2026-09-23：点「写入」要等半分钟，界面上什么都不说。现在审核是卡上单独一步，
    结论挂在卡上；接着点写入，改法一个字没动就直接用那份结论。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    called = []
    from server import audit as audit_mod
    original = audit_mod.ask_meta
    audit_mod.ask_meta = lambda *a, **k: (called.append(1) or json.dumps(
        {"verdict": "warn", "summary": "能写，年份再核一下",
         "issues": [{"path": "x", "severity": "warn", "what": "年份存疑", "why": "记忆里是 2017", "fix": "查原文"}]},
        ensure_ascii=False), {"model": "假审校", "ms": 1234})
    try:
        changes = new_node_changes()
        before = md_digest(vault)
        r = c.post("/api/audit", json={"changes": changes, "card": "c1"})
        assert r.status_code == 200, r.text
        rep = r.json()
        assert rep["verdict"] == "warn" and rep["model"] == "假审校" and rep["ms"] == 1234, rep
        assert rep["stamp"] and len(called) == 1
        assert md_digest(vault) == before, "审核写了盘"

        w = write_changes(c, changes, card="c1")
        assert w.status_code == 200 and w.json()["applied"], w.text
        assert len(called) == 1, "卡上审过、改法没动，写入时又问了一遍模型"
        assert w.json()["audit"]["reused"] and w.json()["audit"]["summary"] == "能写，年份再核一下"

        # 另一张卡：审完又改了一个字 → 写入时得重审
        edited = new_node_changes("另一个点")
        c.post("/api/audit", json={"changes": edited, "card": "c2"})
        edited[0]["body"] += "补一句。"
        w = write_changes(c, edited, card="c2")
        assert w.status_code == 200 and len(called) == 3, (w.text, called)
        assert not w.json()["audit"]["reused"]
        rows = [r for r in core.load_cards(vault) if r.get("event") == "audited"]
        assert [r["id"] for r in rows] == ["c1", "c2", "c2"], rows
    finally:
        audit_mod.ask_meta = original
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_挡下的结论写入时照样挡_不重问():
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    original = stub_audit(json.dumps({"verdict": "block", "summary": "年份错了", "issues": [
        {"path": "x", "severity": "block", "what": "不是 2018", "why": "2017 发表", "fix": "改 2017"}]},
        ensure_ascii=False))
    try:
        changes = new_node_changes()
        assert c.post("/api/audit", json={"changes": changes, "card": "c9"}).json()["verdict"] == "block"
        w = write_changes(c, changes, card="c9")
        assert w.json()["applied"] is False and w.json()["audit"]["reused"], w.json()["audit"]
        w = write_changes(c, changes, card="c9", force=True)
        assert w.json()["applied"] is True, "仍然写入要能绕过去"
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_审核结论跟着卡片从留档回来():
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    changes = [{"type": "update_body", "source": "b", "body": AUDIT_LONG}]
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes}), "卡给你了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "改 b"}], "session": "s1"})
    finally:
        restore_chat(original)
    card = next(e["card"] for e in sse_events(r) if e["type"] == "card")
    back = lambda: c.get("/api/chat/history", params={"session": "s1"}).json()["messages"][-1]["cards"][0]["card"]
    assert back()["audit"] is None, "没审过的卡不该带结论"
    audit_orig = stub_audit(json.dumps({"verdict": "pass", "summary": "没问题", "issues": []}, ensure_ascii=False))
    try:
        c.post("/api/audit", json={"changes": card["changes"], "card": card["card_id"]})
    finally:
        restore_audit(audit_orig)
        c.put("/api/settings", json={"audit_enabled": False})
    got = back()["audit"]
    assert got["summary"] == "没问题" and got["fresh"] and got["model"] == "假审校", got


@case
def audit_默认关着时不问模型但确定性检查照跑():
    """开关关掉 ≠ 什么都不查。关的人要的是"别拦我"，不是"别告诉我"。"""
    c, vault, _ = with_inbox_node()
    called = []
    original = stub_audit("不该被调用")
    from server import audit as audit_mod
    audit_mod.ask_meta = lambda *a, **k: (called.append(1) or "{}", {"model": "", "ms": 0})
    try:
        r = write_changes(c, new_node_changes(body="太短", edge=False))
        assert r.status_code == 200 and r.json()["applied"] is True, r.text
        report = r.json()["audit"]
        assert called == [], "开关关着还问了模型"
        assert report["checked"] is False and report["verdict"] == "pass"
        assert {i["code"] for i in report["issues"]} == {"thin_body", "no_relation"}, report["issues"]
    finally:
        restore_audit(original)


@case
def audit_预览阶段只跑第一段():
    """改一行摘要就重算一次 diff，每次都烧一次调用没道理。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    called = []
    original = stub_audit("{}")
    from server import audit as audit_mod
    audit_mod.ask_meta = lambda *a, **k: (called.append(1) or "{}", {"model": "", "ms": 0})
    try:
        rev = c.get("/api/index").json()["revision"]
        r = c.post("/api/changes", json={"base_revision": rev, "changes": new_node_changes(body="太短"),
                                         "dry_run": True})
        assert r.status_code == 200, r.text
        assert called == [], "预览阶段不该问模型"
        assert r.json()["audit"]["issues"], "但确定性检查的结果要摆在卡上"
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_开着时挡下不落盘_但返回200把理由带回来():
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    before = md_digest(vault)
    original = stub_audit(json.dumps({"verdict": "block", "summary": "年份写错了",
        "issues": [{"path": "nodes/新点.md", "severity": "block", "what": "Transformer 不是 2018 年",
                    "why": "论文 2017 年发表", "fix": "改成 2017"}]}, ensure_ascii=False))
    try:
        r = write_changes(c, new_node_changes())
        assert r.status_code == 200, "挡下是正常结局，不是 HTTP 错误——抛 4xx 前端只剩一句红字"
        data = r.json()
        assert data["applied"] is False and data["audit"]["verdict"] == "block", data["audit"]
        assert data["audit"]["issues"][0]["why"], "挡下必须给依据"
        assert data["files"], "diff 照样要带回去，人要对着它改"
        assert md_digest(vault) == before, "挡下了还写盘"
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_强制写入跳过模型但留痕():
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    called = []
    original = stub_audit("{}")
    from server import audit as audit_mod
    audit_mod.ask_meta = lambda *a, **k: (called.append(1) or "{}", {"model": "", "ms": 0})
    try:
        r = write_changes(c, new_node_changes(), force=True)
        assert r.status_code == 200 and r.json()["applied"] is True, r.text
        assert called == [], "强制写入还问模型 = 白花一次钱"
        assert r.json()["audit"]["forced"] is True
        issues = core.load_issues(vault)
        assert any(i["kind"] == "audit" for i in issues), "强制写入必须留证据，否则开关就是摆设"
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_关掉强制入口之后强制写入被拒():
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True, "audit_force_allowed": False})
    before = md_digest(vault)
    try:
        r = write_changes(c, new_node_changes(), force=True)
        assert r.status_code == 422, r.text
        assert md_digest(vault) == before
    finally:
        c.put("/api/settings", json={"audit_enabled": False, "audit_force_allowed": True})


@case
def audit_只动关系不问模型():
    """每加一条边都卡几秒的话，两天之内这个开关就会被关掉。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    called = []
    original = stub_audit("{}")
    from server import audit as audit_mod
    audit_mod.ask_meta = lambda *a, **k: (called.append(1) or "{}", {"model": "", "ms": 0})
    try:
        r = write_changes(c, [{"type": "add_edge", "source": "a", "relation": "相关", "target": "b"}])
        assert r.status_code == 200 and r.json()["applied"] is True, r.text
        assert called == [], "只改关系也问模型"
        assert r.json()["audit"]["checked"] is False
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_模型抽风时放行并说出来():
    """挡下意味着"模型不回话 = 你写不了东西"，那是把可用性押在一次网络请求上。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    original = stub_audit("今天不太想审。")
    try:
        r = write_changes(c, new_node_changes())
        assert r.status_code == 200 and r.json()["applied"] is True, r.text
        assert r.json()["audit"]["model_failed"] is True, r.json()["audit"]
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_说挡下却给不出问题的降成提醒():
    """一句"我觉得不太对"就能把人挡在门外，而人连改哪儿都不知道。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"audit_enabled": True})
    original = stub_audit(json.dumps({"verdict": "block", "summary": "感觉不太对", "issues": []},
                                     ensure_ascii=False))
    try:
        r = write_changes(c, new_node_changes())
        assert r.json()["applied"] is True, "没有依据的挡不作数"
        assert r.json()["audit"]["verdict"] == "pass", r.json()["audit"]
    finally:
        restore_audit(original)
        c.put("/api/settings", json={"audit_enabled": False})


@case
def audit_开关是设置里的一对且默认关():
    got = client()[0].get("/api/settings").json()
    assert got["audit_enabled"] is False, "它给每次写入加一次调用和几秒等待，该由人明确打开"
    assert got["audit_force_allowed"] is True, "不给后门，人只会把整套关掉"


@case
def settings_契约和开关全集不许漂移():
    """DEFAULTS 里加了开关、契约上忘了加 → `/api/settings` 直接 500。

    2026-09-22 真炸过一次：audit 那两个开关先进了 core.DEFAULTS，SettingsRead 还没跟上，
    于是**读设置和改设置一起挂**——设置页一点就是红字，跟点的是哪个开关毫无关系。
    契约默认 extra="forbid"，这种漏加不会被静默丢掉，但也只在点下去的那一刻才炸，
    所以把两张表在这里对一遍：加开关只动 DEFAULTS 那句话，靠这条用例兑现。
    """
    from server.contracts import SettingsPatch, SettingsRead
    from server.paths import core as core_mod
    keys = set(core_mod.SETTINGS_DEFAULTS)
    assert set(SettingsRead.model_fields) == keys | {"schema_version"}, "读契约和开关全集对不上"
    assert set(SettingsPatch.model_fields) == keys, "改契约和开关全集对不上"
    got = client()[0].get("/api/settings").json()
    assert set(got) == keys | {"schema_version"}, got


@case
def settings_教练别考我和今日面板复习是两个开关():
    """复盘之后拆的（§11.1）：原来只有一个总闸，"别在聊天里考我"只能靠关总闸达成，
    而总闸一关，**今日面板里的到期与错题也跟着被摘掉**——想专心复习的那个地方恰好空了。
    """
    c, vault, _ = with_inbox_node()          # d 的 learned 是 2026-09-01，早就该复习

    def 今日():
        return c.get("/api/coach/today").json()

    def 教练提示词() -> str:
        original, seen = stub_chat(["好"])
        try:
            c.post("/api/chat", json={"messages": [{"role": "user", "content": "在么"}]})
        finally:
            restore_chat(original)
        return seen[0][0]["content"]

    assert any(it["kind"] == "due" for it in 今日()["items"]), 今日()["items"]
    assert "`quiz`" in 教练提示词(), "默认全开，说明书上该有出题工具"

    # 只关「教练会考我」：聊天里收手，今日面板照旧
    c.put("/api/settings", json={"review_in_chat": False})
    assert any(it["kind"] == "due" for it in 今日()["items"]), "关的是聊天那一档，今日面板不该空"
    assert 今日()["pools"]["今日"], "出题范围也不该空"
    p = 教练提示词()
    assert "`quiz`" not in p, "工具没收走，只靠嘱咐一句是压制不是关闭"
    assert "复习本身还开着" in p, p[-400:]          # 别把它说成"复习关了"

    # 再关总闸：整套都不出现，**出题范围也跟着空**（原来这里行没了、数字还在）
    c.put("/api/settings", json={"review_enabled": False})
    assert not [it for it in 今日()["items"] if it["kind"] in ("due", "wrong")], 今日()["items"]
    assert 今日()["pools"]["今日"] == [], 今日()["pools"]
    assert "整套在设置里关着" in 教练提示词()


@case
def settings_总闸关着时子开关一律当关():
    """子开关是总闸下面的一档，不能出现"总闸关了、教练还在考我"。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/settings", json={"review_enabled": False, "review_in_chat": True})
    from server.paths import core as core_mod
    assert core_mod.review_in_chat() is False
    assert c.get("/api/settings").json()["review_in_chat"] is True, "子开关自己的值要留着，只是不生效"


@case
def chat_搜索也能搜到计划里还没建的点():
    """一个项目常常 40 个点里 39 个还没建——只搜索引会零命中，然后又建一个重复的。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"transformer": {
        "name": "Transformer", "lists": [{"kind": "学习", "name": "主线", "stages": [
            {"name": "一", "points": [{"id": "多头注意力", "why": "核心机制"}]}]}]}}})
    from server import chat as chat_mod
    body, meta = chat_mod._tool_search(vault, {"q": "多头注意力"})
    assert meta["hits"] == 1, body
    assert "计划里列过" in body and "Transformer" in body, body
    assert "还没建" in body, body
    # 已经建成节点的仍然走"已建"那一档，不会重复出现在计划那一档里
    body2, _ = chat_mod._tool_search(vault, {"q": "A"})
    assert "已建的节点" in body2 and "计划里列过" not in body2, body2


@case
def chat_正文档位跟项目难度走_线头哪档都有():
    """了解 = 精简、会用 = 标准、精通 = 详尽。三档都必须保留「线头」——
    那是给以后连边留的钩子，精简掉的关键词（神经生理学家 + 数学家）就是这样丢的。"""
    c, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    for level, word in (("了解", "精简档"), ("会用", "标准档"), ("精通", "详尽档")):
        rev = c.get("/api/projects").json()["doc"].get("revision", 0)
        c.put("/api/projects", json={"base_revision": rev, "projects": {"p": {
            "name": "P", "level": level, "lists": [{"kind": "学习", "name": "主线", "stages": []}]}}})
        prompt = chat_mod._system_prompt(vault, "教练", "p")
        assert word in prompt, (level, word)
        assert "线头" in prompt and "不能省" in prompt, level
    # 不绑项目走默认档
    assert "标准档" in chat_mod._system_prompt(vault, "教练")


@case
def chat_搜索连tags和aliases一起搜():
    """「线头」抄进 tags 就是为了以后建到「神经元」时能搜回「阈值逻辑单元」；
    只搜 id/name/desc 的话，这个钩子挂了等于没挂。"""
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/tlu.md", node_md("TLU", extra="tags:\n  - 神经生理学\naliases:\n  - M-P 神经元\n"))
    index_service.invalidate()
    from server import chat as chat_mod
    body, meta = chat_mod._tool_search(vault, {"q": "神经生理学"})
    assert meta["hits"] == 1 and "tlu" in body, body
    body, meta = chat_mod._tool_search(vault, {"q": "M-P"})
    assert meta["hits"] == 1 and "tlu" in body, body


@case
def chat_中断不算出错():
    """点「停止」/ 关页面走的是生成器被关闭，它既不是模型的锅也没什么可查的。

    两件事要分开：留档里得留个**说得清**的记号（"没答成：" 后面空着，读的人只会当成 bug），
    而且**不进出错流水**——issues.jsonl 是用来看"这东西为什么老出问题"的，
    把人主动按的停止算进去，那张表就没法看了。
    """
    from server import chat as chat_mod
    from server.contracts import ChatRequest
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([tool_block("overview", {}), "看完了"])
    try:
        gen = chat_mod.run(vault, ChatRequest(messages=[{"role": "user", "content": "中断测试"}],
                                              session="s1"))
        next(gen)                      # 收到第一个事件
        gen.close()                    # 客户端断了
    finally:
        restore_chat(original)

    rows = [json.loads(l) for l in
            (vault / ".knowrary/chat/_scratch" / f"{dt.date.today():%Y-%m}.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    kinds = [r["role"] for r in rows]
    assert kinds == ["user", "assistant"], f"留档没成对：{kinds}"
    assert "被我中断" in rows[1]["text"], rows[1]["text"]
    assert "没答成" not in rows[1]["text"], rows[1]["text"]
    assert not core.load_issues(vault), "把人按的停止记进出错流水了"


@case
def chat_这一轮炸了留档里也有记号():
    """只记提问不记结果的话，失败五次就攒出五条没人答的问题，下次全被读回去当上下文。"""
    c, vault, _ = with_inbox_node()
    import llm_backend
    real = llm_backend.chat

    def boom(messages, provider, model_override=None, on_delta=None, tools=None, session=None):
        raise SystemExit("HTTP 503")

    llm_backend.chat = boom
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}]})
    finally:
        llm_backend.chat = real
    rows = c.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in rows] == ["user", "assistant"], rows
    assert "没答成" in rows[1]["content"] and "503" in rows[1]["content"], rows[1]


@case
def chat_能往已有节点补正文而不是只会新建():
    """边聊边完善：聊到图里已有的东西，该往那篇 md 里补，而不是另建一个。

    `update_body` 是**整段替换**，所以模型必须先 read_node 把原文带上——
    这条只能靠 prompt 约束，但"卡片里的 diff 会不会把原文抹掉"是人点之前看得见的。
    """
    c, vault, _ = with_inbox_node()
    body = "正文\n\n补充：这次聊出来的新理解。"
    original, _ = stub_chat([tool_block("propose_changes", {"changes": [
        {"type": "update_body", "source": "a", "body": body}]}), "补在 a 里了，你看一眼 diff"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "刚才聊的补进去"}]})
    finally:
        restore_chat(original)
    cards = [e for e in sse_events(r) if e["type"] == "card"]
    assert cards, [e["type"] for e in sse_events(r)]
    diff = json.dumps(cards[0]["card"], ensure_ascii=False)
    assert "这次聊出来的新理解" in diff and "nodes/组A/a.md" in diff, diff[:300]
    # 仍然没落盘
    assert "这次聊出来的新理解" not in core.read(vault / "nodes/组A/a.md")

    # **说明书要盖满 writer 支持的每一种改动**，而不是手抄一份会漂的清单。
    # 真出过这个事故：说明书上只写了四种，`remove_edge` / `update_edge` /
    # `update_frontmatter` 三种它根本看不见——于是"这条边该删"只能变成一个问句丢回给人，
    # 一件十秒的事要来回好几轮。漏一种就当它不存在，所以这里对着源头断言。
    from server import chat as chat_mod
    prompt = chat_mod._system_prompt(vault, "教练")
    for kind in core.CHANGE_TYPES:
        assert kind in prompt, f"`{kind}` 没写进说明书，模型不会用"
    assert "整段替换" in prompt, "没告诉模型 update_body 会覆盖，它迟早把我的笔记抹掉"
    assert "正文要写成能过半年回看的笔记" in prompt, "没给正文骨架，它只会写两句话交差"
    # 笔记层按档写足：讲多深（chat 栏）和记多满（note 栏）是两件事，缺后者模型会把
    # 「一句话结论就停」套到正文上，建出来的点只剩 desc（阈值逻辑单元那次）
    assert "## 线头" in prompt and "标准档" in prompt, "骨架没带线头 / 档位"
    assert "{{" not in prompt, [l for l in prompt.splitlines() if "{{" in l]
    # frontmatter 白名单同理：少列一个字段，那个字段就永远改不成
    for f in core.EDITABLE_FIELDS:
        assert f in prompt, f"frontmatter 字段 `{f}` 没写进说明书"
    assert "碰不到 frontmatter" in prompt, "没说清 update_body 改不到 desc，摘要会一直停在旧说法上"


@case
def chat_能提议删边和改摘要而不是把选择题丢回来():
    """改图不是只有"往里加"：删错边、改 desc 同样该出卡片。

    这三种（remove_edge / update_edge / update_frontmatter）writer 一直支持，
    漏的只是说明书。补上之后要保证整条链路真的走得通——尤其 `desc`，
    正文改完摘要还停在旧说法上，是这套图最容易攒下的烂账。
    """
    c, vault, _ = with_inbox_node()
    core.write(vault / "nodes/组A/a.md", "---\nname: a\nfield: F\ndesc: 旧摘要\n---\n"
               "# a\n\n正文\n\n## 关系\n- 相关:: [[b]]\n")
    original, _ = stub_chat([tool_block("propose_changes", {"changes": [
        {"type": "remove_edge", "source": "a", "relation": "相关", "target": "b"},
        {"type": "update_frontmatter", "source": "a", "fields": {"desc": "改过的摘要"}}]}),
        "这条边和正文打架，建议删；摘要也一起改了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "这条边不对"}]})
    finally:
        restore_chat(original)
    cards = [e for e in sse_events(r) if e["type"] == "card"]
    assert cards, [e["type"] for e in sse_events(r)]
    diff = json.dumps(cards[0]["card"], ensure_ascii=False)
    assert "改过的摘要" in diff and "相关" in diff, diff[:300]

    # 仍然一个字都没落盘：卡片是提议，写盘只能靠人点
    raw = core.read(vault / "nodes/组A/a.md")
    assert "旧摘要" in raw and "- 相关:: [[b]]" in raw, raw


@case
def chat_一轮里能摆好几张卡各点各的():
    """一张卡是**整份写入**的：夹着一条还没想好的，已经想好的那几条也跟着一起等。

    所以互不相关的几件事要拆成几张卡。链路一直支持（每个 card 事件一张，各带一个「写入」），
    真正拦住它的是措辞——上一版工具返回语只说"等他点写入"，模型读完就收尾，
    于是三件小事被迫拆成三轮。这里把"能摆几张"和"说明书有没有教它这么干"一起钉住。
    """
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([
        tool_block("propose_changes", {"changes": [
            {"type": "update_frontmatter", "source": "a", "fields": {"desc": "改摘要这件事"}}]}),
        tool_block("propose_changes", {"changes": [
            {"type": "add_edge", "source": "a", "relation": "相关", "target": "b"}]}),
        "两张卡都摆出来了，各点各的",
    ])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "两件事一起办"}]})
    finally:
        restore_chat(original)
    cards = [e for e in sse_events(r) if e["type"] == "card"]
    assert len(cards) == 2, f"一轮只摆出了 {len(cards)} 张卡"
    kinds = [c_["card"]["changes"][0]["type"] for c_ in cards]
    assert kinds == ["update_frontmatter", "add_edge"], kinds
    # 两张卡各自独立：谁都还没落盘，人点哪张写哪张
    raw = core.read(vault / "nodes/组A/a.md")
    assert "改摘要这件事" not in raw and "- 相关:: [[b]]" not in raw, raw

    from server import chat as chat_mod
    prompt = chat_mod._system_prompt(vault, "教练")
    assert "分开提成几张卡" in prompt, "没教它拆卡，互不相关的几件事还会被塞进同一张"


@case
def chat_口径提示词不跟说明书抢着教补内容():
    """「往已有节点补一段」的默认路径，整份提示词里只能有一个说法。

    真出过事：`chat-talk` / `chat-coach` 写的是「`read_node` 拿原文 → `update_body` 把原文带上补一段」，
    而 `FORMAT_DOC` 写的是「默认 `append_body`，`update_body` 只在真要重写时才用」。
    两份是拼在一起喂给模型的，它照哪份走全看运气——偏偏打架的那条是危险的那条：
    整段替换，模型把"原文"带少一截，我以前记的就永久没了。
    """
    _, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    # 这两句是原来那个说法的原文，回来一句就说明又漂回去了
    strayed = ("`update_body` 把原文带上补一段", "再 `update_body` **把原文带上**补一段")
    for stance in ("教练", "聊天"):
        prompt = chat_mod._system_prompt(vault, stance)
        assert "append_body" in prompt, f"{stance} 口径没提 append_body"
        assert "默认用 `append_body`" in prompt, f"{stance} 口径没说清默认走哪条"
        for phrase in strayed:
            assert phrase not in prompt, f"{stance} 口径又在教模型用 update_body 补内容：{phrase}"


@case
def append_body_只追加不覆盖原文():
    """**为什么要有这条路径**：`update_body` 是整段替换，要求模型把原文一字不落带回来，
    而它看到的原文随时可能是截断过的——带少了就等于把我以前记的东西删了。
    往笔记里补一段本来不需要读全篇，append_body 从根上免掉那个风险。
    """
    c, vault, _ = with_inbox_node()
    rev = c.get("/api/index").json()["revision"]
    r = c.post("/api/changes", json={"base_revision": rev, "dry_run": False, "changes": [
        {"type": "append_body", "source": "a", "body": "## 和 B 的区别\n这次聊清楚的那点。"}]})
    assert r.status_code == 200, r.text
    text = core.read(vault / "nodes/组A/a.md")
    assert "正文" in text, "原来的正文被抹掉了，追加变成了替换"
    assert "## 和 B 的区别" in text and "这次聊清楚的那点。" in text, text
    # 追加的那段要落在 `## 关系` 之前，关系区块仍然由关系解析器独占
    assert text.index("这次聊清楚的那点。") < text.index("## 关系"), text
    assert len(c.get("/api/index").json()["edges"]) >= 0     # 索引还认得这个文件

    # update_body 把正文改短一大截时，卡片上要喊出来——最典型的事故是模型带回来的
    # "原文"少了一截，一按写入就把以前记的东西删了
    rev = c.get("/api/index").json()["revision"]
    c.post("/api/changes", json={"base_revision": rev, "dry_run": False, "changes": [
        {"type": "append_body", "source": "b", "body": "细节" * 200}]})
    r = c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                     "dry_run": True, "changes": [
        {"type": "update_body", "source": "b", "body": "就剩这一句了"}]})
    notes = " ".join(r.json()["files"][0]["notes"])
    assert "⚠️" in notes and "append_body" in notes, notes

    # 空 body 和自带 `## 关系` 的都要被挡回去
    for bad in ("", "  ", "## 关系\n- 部件:: [[b]]"):
        r = c.post("/api/changes", json={"base_revision": c.get("/api/index").json()["revision"],
                                         "dry_run": False, "changes": [
            {"type": "append_body", "source": "a", "body": bad}]})
        assert r.status_code == 422, (bad, r.status_code)


@case
def read_node_一次读多个且长正文不再被截断():
    """两件事凑一起原来是会吃掉笔记的：正文被截到 1200 字，模型再用 update_body 整段写回去，
    超出的后半截就没了。现在不截到那么短，真截断时也会在结果里直说「只准 append_body」。

    一次能读多个则是为了省步数：一轮只有 MAX_STEPS 步，5 个节点一个一个读根本走不完。
    """
    from server import chat as chat_mod
    c, vault, _ = with_inbox_node()
    long_body = "细节" * 1500                                  # 3000 字，早先会被砍到 1200
    core.write(vault / "nodes/组A/长文.md",
               node_md("长文", extra="", rels="") .replace("正文", long_body))
    index_service.invalidate()

    text, meta = chat_mod._tool_read(vault, {"id": "长文"})
    assert long_body in text, "正文又被截断了：update_body 写回去会把后半截删掉"
    assert not meta["truncated"], meta

    text, meta = chat_mod._tool_read(vault, {"ids": ["a", "b", "长文", "a"]})
    assert meta["ids"] == ["a", "b", "长文"], meta          # 去重保序
    assert "### a" in text and "### b" in text and "### 长文" in text, text[:200]

    # 真截断了，必须明说而且禁掉 update_body
    saved = chat_mod.READ_CHARS
    try:
        chat_mod.READ_CHARS = 500
        text, meta = chat_mod._tool_read(vault, {"id": "长文"})
    finally:
        chat_mod.READ_CHARS = saved
    assert meta["truncated"] == 1, meta
    assert "不许 update_body" in text and "截断" in text, text[-300:]


@case
def read_node_按标题读一节_截断时附目录():
    """上万字的长笔记整篇读只能看到前半篇。现在截断时把目录附上，模型再带 `section` 只读要改的那一节；
    只读了一节同样算「没看全」，结果里要明说只准 append_body。"""
    from server import chat as chat_mod
    c, vault, _ = with_inbox_node()
    body = ("## 描述\n概述。\n\n## 一、分界\n分界正文\n\n```python\n# 代码里的井号不是标题\n```\n\n"
            "### 案例 1\n案例正文\n\n## 二、粒度\n" + "粒度" * 400 + "\n\n## 三、验证\n验证正文")
    core.write(vault / "nodes/组A/长文.md", node_md("长文", extra="", rels="").replace("正文", body, 1))
    index_service.invalidate()

    # 按节读：只有这一节和它的子节，其他节不在；明说不是全文、不许 update_body；附其余目录
    text, meta = chat_mod._tool_read(vault, {"id": "长文", "section": "一、分界"})
    assert "分界正文" in text and "案例正文" in text and "粒度粒度" not in text and "验证正文" not in text, text[:400]
    assert "不许 update_body" in text and "不是全文" in text and "- 三、验证" in text, text[-400:]
    assert meta["truncated"] == 1 and meta["section"] == "一、分界", meta
    assert "代码里的井号" not in text.split("其余小节")[1]

    # 标题写错：不报错，把目录给出来
    text, _ = chat_mod._tool_read(vault, {"id": "长文", "section": "不存在"})
    assert "没有叫「不存在」的小节" in text and "- 二、粒度" in text, text

    # 整篇读被截断时：带目录和 section 的用法提示
    saved = chat_mod.READ_CHARS
    try:
        chat_mod.READ_CHARS = 400
        text, meta = chat_mod._tool_read(vault, {"id": "长文"})
    finally:
        chat_mod.READ_CHARS = saved
    assert meta["truncated"] == 1 and "section" not in meta, meta
    assert "截断" in text and "- 三、验证" in text and "`section:" in text, text[-500:]

    # 一次读多个 + section：每个节点都按同一个标题取
    text, meta = chat_mod._tool_read(vault, {"ids": ["长文", "a"], "section": "描述"})
    assert "### 长文" in text and "### a" in text and "概述。" in text, text[:300]

    # 工具说明书上要写着有这个参数，模型才知道能用
    assert "`section`" in chat_mod.TOOL_DOC["read_node"]


@case
def read_node_outline只给目录_搜索结果标出长笔记():
    """模型不该为了知道一篇长笔记长什么样先花 8000 字整篇读一遍：
    `outline: true` 只给 desc + 字数 + 全部标题；搜索结果里超过 READ_CHARS 的节点标「长笔记」指向这条路。"""
    from server import chat as chat_mod
    c, vault, _ = with_inbox_node()
    body = "## 一、开头\n开头正文\n\n## 二、中间\n" + "中间" * 5000 + "\n\n### 尾巴里的子节\n子节正文\n\n## 三、结尾\n结尾正文"
    core.write(vault / "nodes/组A/长文.md", node_md("长文", extra="", rels="").replace("正文", body, 1))
    index_service.invalidate()

    text, meta = chat_mod._tool_read(vault, {"id": "长文", "outline": True})
    # 三个标题都在（包括 8000 字之后的），正文一个字都不带
    assert "- 一、开头" in text and "    - 尾巴里的子节" in text and "- 三、结尾" in text, text
    assert "中间中间" not in text and "开头正文" not in text and "结尾正文" not in text, text
    assert "超过一次能读的上限" in text and "不许 `update_body`" in text and "`section:" in text, text
    assert meta["outline"] is True and meta["truncated"] == 0, meta
    assert len(text) < 600, f"目录应该是几百字，不是 {len(text)}"

    # 短笔记的目录不说「超过上限」
    text, _ = chat_mod._tool_read(vault, {"id": "a", "outline": True})
    assert "超过一次能读的上限" not in text and "这只是目录" in text, text

    # 搜索结果：长的标出来，短的不标
    body_s, _ = chat_mod._tool_search(vault, {"q": "长文"})
    rows = json.loads(body_s)["已建的节点"]
    assert rows[0]["id"] == "长文" and "outline: true" in rows[0]["长笔记"], rows
    body_s, _ = chat_mod._tool_search(vault, {"q": "a"})
    assert all("长笔记" not in r for r in json.loads(body_s)["已建的节点"]), body_s
    assert "`outline`" in chat_mod.TOOL_DOC["read_node"]
    assert "outline" in chat_mod.TOOLS_SPEC["read_node"]["params"]


@case
def 梳理游标_写入之后才推进且只进不退():
    """梳理是这里最贵的一次动作（一轮工具循环，每一步都把整段对话再发一遍）。
    第二天打开同一段再点一次「梳理这段」，没有游标就是把昨天那笔钱原样再付一遍。

    **游标只在变更卡真写进 md 之后才推进**：梳理过但没采纳的内容不算整理过。
    """
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat(["记下了"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "聊一句"}],
                                  "session": "s1"})
    finally:
        restore_chat(original)
    rows = c.get("/api/chat/history?session=s1").json()["messages"]
    assert all(m["ts"] for m in rows), "留档的 ts 没带出来，前端就没有游标坐标"
    assert c.get("/api/chat/sessions").json()["sessions"][0]["tidied"] is None

    upto = rows[-1]["ts"]
    assert c.post("/api/chat/tidied", json={"session": "s1", "upto": upto, "turns": 2}
                  ).json()["tidied"]["upto"] == upto
    hit = next(s for s in c.get("/api/chat/sessions").json()["sessions"] if s["id"] == "s1")
    assert hit["tidied"]["upto"] == upto, hit

    # 只进不退：先写了后面那张卡、再回头写前面那张，游标不该被拖回去
    older = "2020-01-01T00:00:00+08:00"
    assert c.post("/api/chat/tidied", json={"session": "s1", "upto": older}
                  ).json()["tidied"]["upto"] == upto
    assert c.post("/api/chat/tidied", json={"session": "s1", "upto": ""}).status_code == 400
    # 贴纸删掉只是退回全量重梳，一个字的知识都不会丢
    (vault / ".knowrary/chat/_scratch/tidied.json").unlink()
    assert c.get("/api/chat/sessions").json()["sessions"][0]["tidied"] is None


@case
def chat_today工具跟着当前项目走():
    """一期把 `plans` 改名成 `projects` 时漏了这一处，`today` 工具直接抛 KeyError——
    真拿模型跑一次才发现（它只好说一句"today 挂了"接着聊）。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"p1": {
        "name": "项目一", "lists": [{"stages": [{"name": "一", "points": [{"id": "没建的"}]}]}]}}})
    from server import chat as chat_mod
    body, meta = chat_mod._tool_today(vault, {})
    got = json.loads(body)
    assert "项目进度" in got and "大概要花" in got, got            # 不再是 KeyError
    assert any(i["id"] == "d" for i in got["清单"]), got["清单"]   # 全局能看到 Inbox 里的 d

    scoped = json.loads(chat_mod._tool_today(vault, {"_project": "p1"})[0])
    assert [i["id"] for i in scoped["清单"]] == ["没建的"], scoped["清单"]

    # 请求里的项目要真的传到工具里（不是靠模型自己填参数）
    seen = {}
    original = chat_mod.TOOLS["today"]
    chat_mod.TOOLS["today"] = lambda v, a: (seen.setdefault("args", a), "ok")[1]
    orig_llm, _ = stub_chat([tool_block("today", {}), "看完了"])
    try:
        c.post("/api/chat", json={"project": "p1", "messages": [{"role": "user", "content": "今天学啥"}]})
    finally:
        restore_chat(orig_llm)
        chat_mod.TOOLS["today"] = original
    assert seen["args"].get("_project") == "p1", seen


@case
def chat_新建的点顺手补进当前项目的清单():
    """节点建出来了、清单却没列它的话，项目进度不认它，今日清单也不会再提它。"""
    c, vault, _ = with_inbox_node()
    c.put("/api/projects", json={"base_revision": 0, "projects": {"ai": {
        "name": "AI技术发展史", "field": "AI",
        "lists": [{"kind": "学习", "name": "主线",
                   "stages": [{"name": "一", "points": [{"id": "已经列过的"}]}]}]}}})
    changes = [{"type": "create_node", "source": "NPU", "path": "nodes/组A/NPU.md",
                "fields": {"name": "NPU", "field": "AI", "layer": "硬件", "year": 2017,
                           "desc": "专用推理电路"}},
               {"type": "add_edge", "source": "NPU", "relation": "对比", "target": "a"}]
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes}), "卡给你了"])
    try:
        r = c.post("/api/chat", json={"project": "ai",
                                      "messages": [{"role": "user", "content": "把 NPU 记下来"}]})
    finally:
        restore_chat(original)
    card = [e for e in sse_events(r) if e["type"] == "card"][0]["card"]
    assert card["into"] == {"project": "ai", "project_name": "AI技术发展史", "list": 0,
                            "list_name": "主线", "points": ["NPU"]}, card["into"]
    # 已经在清单里的点不重复加
    changes2 = [{"type": "create_node", "source": "已经列过的", "path": "nodes/组A/已经列过的.md",
                 "fields": {"name": "x", "field": "AI", "desc": "d"}}]
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes2}), "好"])
    try:
        r2 = c.post("/api/chat", json={"project": "ai",
                                       "messages": [{"role": "user", "content": "再记一个"}]})
    finally:
        restore_chat(original)
    assert [e for e in sse_events(r2) if e["type"] == "card"][0]["card"]["into"] is None

    # 不在项目下（全局那条线）就不往任何清单里塞
    original, _ = stub_chat([tool_block("propose_changes", {"changes": changes}), "好"])
    try:
        r3 = c.post("/api/chat", json={"messages": [{"role": "user", "content": "记一下"}]})
    finally:
        restore_chat(original)
    assert [e for e in sse_events(r3) if e["type"] == "card"][0]["card"]["into"] is None


@case
def chat_侧写来自vault且项目级覆盖全局():
    """"我是谁、要什么口气"属于**我的数据**，放 .knowrary/ 里随便改；
    工具协议和纪律属于**程序行为**，留在 tools/prompts/ 里跟代码一起测。"""
    c, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    empty = chat_mod._system_prompt(vault, "教练")
    assert "还没写" in empty and "coach.md" in empty, "没告诉人该去哪写"

    (vault / ".knowrary" / "coach.md").write_text("别夸我，直接说错在哪。", "utf-8")
    p1 = chat_mod._system_prompt(vault, "教练")
    assert "别夸我" in p1 and "还没写" not in p1

    c.put("/api/projects", json={"base_revision": 0, "projects": {"ai": {
        "name": "AI", "lists": [{"kind": "学习", "name": "主线", "coach": "大模型"}]}}})
    (vault / ".knowrary" / "coaches").mkdir(exist_ok=True)
    (vault / ".knowrary" / "coaches" / "ai.md").write_text("这个项目只要时间线。", "utf-8")
    p2 = chat_mod._system_prompt(vault, "教练", "ai")
    assert "别夸我" in p2 and "只要时间线" in p2, "项目级没接上"
    assert p2.index("别夸我") < p2.index("只要时间线"), "项目级要排在全局之后才盖得住"
    assert "大模型" in p2, "清单上的教练方向也该带进来"
    # 别的项目不串味
    assert "只要时间线" not in chat_mod._system_prompt(vault, "教练", "other")

    # **现读不缓存**：服务跑着的时候改，下一句话就该生效
    (vault / ".knowrary" / "coach.md").write_text("改了。", "utf-8")
    assert "改了。" in chat_mod._system_prompt(vault, "教练")


@case
def chat_难度档跟着项目走():
    """一个维度决定三件事。对话这一头按**项目**的档（一次对话不属于某一份清单）。"""
    c, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    c.put("/api/projects", json={"base_revision": 0, "projects": {
        "hist": {"name": "AI 发展史", "level": "了解", "lists": [{"kind": "学习", "name": "主线"}]},
        "npu": {"name": "NPU", "level": "精通", "lists": [{"kind": "学习", "name": "主线"}]}}})
    assert "只要了解" in chat_mod._system_prompt(vault, "教练", "hist")
    assert "当场追问" in chat_mod._system_prompt(vault, "教练", "npu")
    # 不绑项目的全局对话落默认档，而不是一段空白
    assert "会用" in chat_mod._system_prompt(vault, "教练") or "结论 → 机制" in \
        chat_mod._system_prompt(vault, "教练")


@case
def 难度档一个项目一个():
    assert core.level_of({"level": "了解"}) == "了解"
    assert core.level_of({}) == core.DEFAULT_LEVEL
    assert core.level_of({"level": "瞎填的"}) == core.DEFAULT_LEVEL
    assert core.level_of(None) == core.DEFAULT_LEVEL


@case
def 两层合一层时清单上的旧难度抬到项目上():
    """直接丢掉等于把人填过的东西悄悄抹了。"""
    c, vault, _ = with_inbox_node()
    (vault / ".knowrary" / "projects.json").write_text(json.dumps({
        "schema_version": 2, "revision": 3, "projects": {
            "ai": {"name": "AI", "level": "会用",
                   "lists": [{"kind": "学习", "name": "主线", "level": "了解"}]},
            "hw": {"name": "硬件", "level": "精通",
                   "lists": [{"kind": "学习", "name": "主线", "level": "了解"}]}}},
        ensure_ascii=False), "utf-8")
    doc = c.get("/api/projects").json()["doc"]
    assert doc["projects"]["ai"]["level"] == "了解", doc["projects"]["ai"]
    assert doc["projects"]["hw"]["level"] == "精通", "项目自己写过档就不该被清单盖掉"
    assert all("level" not in ls for p in doc["projects"].values() for ls in p["lists"]), doc


@case
def chat_长对话按条数和字数两道闸裁_并且说出来():
    """产品本身是把**整段历史**发过去的（不是每句话单发）；超长时从最早的开始丢，
    但**必须告诉模型丢了**——它不知道自己少了上下文时，会拿半截记忆当完整的用。"""
    c, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    kept, dropped = chat_mod._fit_history(
        [{"role": "user", "content": "x" * 2000} for _ in range(30)])
    assert dropped == 18 and sum(len(m["content"]) for m in kept) <= chat_mod.MAX_HISTORY_CHARS
    assert chat_mod._fit_history([{"role": "user", "content": "短"}] * 5)[1] == 0

    long_talk = [{"role": "user" if i % 2 == 0 else "assistant", "content": "y" * 3000}
                 for i in range(20)] + [{"role": "user", "content": "接着说"}]
    original, seen = stub_chat(["好"])
    try:
        c.post("/api/chat", json={"messages": long_talk})
    finally:
        restore_chat(original)
    sent = seen[0]
    assert any("没带过来" in (m.get("content") or "") for m in sent), \
        "截断了却没告诉模型，它会默默失忆"
    # 队尾那条是图谱快照（mid-conversation system），最后一句话在它前面
    assert [m["content"] for m in sent if m["role"] == "user"][-1] == "接着说", "最后一轮必须留着"
    # 短对话不插提醒
    original, seen2 = stub_chat(["好"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "在吗"}]})
    finally:
        restore_chat(original)
    assert not any("没带过来" in (m.get("content") or "") for m in seen2[0])


@case
def chat_出错会记进流水():
    """工具失败 / LLM 挂了原来只在那一轮闪一下，"为什么老出问题"没地方回答。"""
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([tool_block("read_node", {"id": None}), "读不到"])
    from server import chat as chat_mod
    real = chat_mod.TOOLS["read_node"]

    def boom(v, a):
        raise RuntimeError("故意炸一个")

    chat_mod.TOOLS["read_node"] = boom
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "看看 a"}]})
    finally:
        restore_chat(original)
        chat_mod.TOOLS["read_node"] = real

    rows = core.load_issues(vault)
    assert rows and rows[0]["kind"] == "tool" and "故意炸一个" in rows[0]["message"], rows
    assert rows[0]["where"] == "read_node", rows[0]
    # 欠账里能看见
    d = c.get("/api/digest").json()
    assert d["counts"]["issues"] >= 1 and d["issues"]["by_kind"].get("tool") >= 1, d["issues"]


@case
def chat_把变更贴成正文也要出卡():
    """模型不调工具、把 `propose_changes` 的参数贴进了正文——服务端代它提卡。

    原生 tool use 那条路上没有围栏回退，正文里的 JSON 没人解析：屏幕上是一段漂亮的 JSON、
    图谱里什么都没发生，模型自己也收不到「卡已摆在他面前」那句回执，下一轮还会再问一次
    「要不要发」（2026-09-21 qwen-plus：一轮拖成三轮，同一份参数生成了两遍）。
    """
    c, vault, _ = with_inbox_node()
    change = {"type": "create_node", "source": "e", "path": "nodes/组A/e.md",
              "fields": {"name": "E", "field": "测试", "desc": "一句话摘要"}, "body": "正文"}
    pasted = ("现在提一张卡：\n\n```json\n" + json.dumps(change, ensure_ascii=False)
              + "\n```\n\n要我现在发吗？")
    original, _ = stub_chat([pasted, "卡摆出来了，等你点写入。"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "建一下 E"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    cards = [e for e in evs if e["type"] == "card"]
    assert len(cards) == 1, [e["type"] for e in evs]
    assert cards[0]["card"]["changes"][0]["source"] == "e", cards[0]["card"]
    # 屏幕上那段 JSON 要被换掉：不换的话它会被 tool 事件原样收进过程折叠区
    repl = [e for e in evs if e["type"] == "replace"]
    assert repl and "```json" not in repl[0]["text"] and "现在提一张卡" in repl[0]["text"], repl
    # 留档里也不留 JSON
    row = json.loads(next((vault / ".knowrary" / "chat").rglob("*.jsonl")).read_text("utf-8")
                     .strip().splitlines()[-1])
    assert "```json" not in row["text"], row["text"]
    # 记一笔："这个模型是不是老不会调工具"得有地方回答
    issues = core.load_issues(vault)
    assert any(i["kind"] == "llm" and "贴成了正文" in i["message"] for i in issues), issues


@case
def chat_只是在解释格式的JSON不当成卡():
    """捞回来的前提是**能过校验**。举例子用的占位 id 在图里不存在，`preview` 会拒，
    那就当没看见——正文原样留着，别把说明文档变成一张他不敢点的卡。"""
    c, vault, _ = with_inbox_node()
    示例 = json.dumps({"type": "append_body", "source": "这个节点不存在", "body": "补一段"},
                      ensure_ascii=False)
    乱码 = json.dumps({"foo": 1}, ensure_ascii=False)
    text = f"格式长这样：\n\n```json\n{示例}\n```\n\n再比如：\n\n```json\n{乱码}\n```\n\n明白了吗？"
    original, _ = stub_chat([text])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "变更集怎么写"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    assert not [e for e in evs if e["type"] in ("card", "replace")], [e["type"] for e in evs]
    done = next(e for e in evs if e["type"] == "done")
    assert "```json" in done["text"] and "这个节点不存在" in done["text"], done["text"]
    assert not core.load_issues(vault), core.load_issues(vault)


@case
def chat_贴成正文的项目卡和清单卡也捞得回来():
    """同一个病不止 `propose_changes` 一处：项目卡、清单卡的参数一样会被贴成正文。
    一段里同时贴了两种，两张卡都得出来，正文里一段 JSON 都不许剩。"""
    c, vault, _ = with_inbox_node()
    _vault_with_list(c)
    proj = {"id": "nlp", "name": "NLP 方向", "field": "AI", "level": "会用",
            "lists": [{"kind": "学习", "name": "主线", "goal": "吃透 Transformer"}]}
    edit = {"project": "ai", "list": "主线", "edits": [{"op": "drop", "id": "符号主义与连接主义"}]}
    pasted = ("两件事：\n\n```json\n" + json.dumps(proj, ensure_ascii=False)
              + "\n```\n\n还有\n\n```json\n" + json.dumps(edit, ensure_ascii=False)
              + "\n```\n\n要发吗？")
    original, _ = stub_chat([pasted, "两张卡都摆好了。"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "建 NLP，顺手清清单"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    assert [e["project"]["id"] for e in evs if e["type"] == "project"] == ["nlp"], \
        [e["type"] for e in evs]
    assert [e["list_edit"]["project"] for e in evs if e["type"] == "list_edit"] == ["ai"], \
        [e["type"] for e in evs]
    repl = [e for e in evs if e["type"] == "replace"]
    assert repl and "```json" not in repl[0]["text"] and "两件事" in repl[0]["text"], repl
    # 仍然只是提议
    assert c.get("/api/projects").json()["doc"]["projects"].get("nlp") is None, "提议就写进去了"


@case
def chat_指不着东西的清单卡不捞():
    """第二道闸是「它指的东西真的存在吗」：举例子用的占位项目名过不了 `_resolve_list`。"""
    c, vault, _ = with_inbox_node()
    示例 = {"project": "某个项目", "list": "主线", "edits": [{"op": "drop", "id": "某个点"}]}
    text = "清单卡长这样：\n\n```json\n" + json.dumps(示例, ensure_ascii=False) + "\n```"
    original, _ = stub_chat([text])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "清单卡怎么写"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    assert not [e for e in evs if e["type"] in ("list_edit", "replace")], [e["type"] for e in evs]
    assert "```json" in next(e for e in evs if e["type"] == "done")["text"]
    assert not core.load_issues(vault), core.load_issues(vault)


@case
def chat_面试档贴了变更也不给捞():
    """白名单管的是**手段**，不是模型的自觉。面试档没有 `propose_changes`——
    边考边改图谱等于开卷，捞回来照样不给调。"""
    c, vault, _ = with_inbox_node()
    change = {"type": "create_node", "source": "e", "path": "nodes/组A/e.md",
              "fields": {"name": "E", "field": "测试", "desc": "一句话摘要"}, "body": "正文"}
    pasted = "顺手记一下：\n\n```json\n" + json.dumps(change, ensure_ascii=False) + "\n```"
    original, _ = stub_chat([pasted])
    try:
        r = c.post("/api/chat", json={"stance": "面试",
                                      "messages": [{"role": "user", "content": "考我"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    assert not [e for e in evs if e["type"] in ("card", "replace")], [e["type"] for e in evs]
    assert not (vault / "nodes/组A/e.md").exists()


@case
def chat_关系类型表要喂给模型():
    """不给表它只能猜类型名：真实对话里写过 `提出者::`，没登记，落弱关联还带警告。
    「发展方向」全靠 `演化` 那一族。"""
    c, vault, _ = with_inbox_node()
    from server import chat as chat_mod
    p = chat_mod._system_prompt(vault, "教练")
    for t in ("演化为", "被激活", "对比", "包含"):
        assert f"`{t}`" in p, t
    assert "从早指向晚" in p, "没说方向，边会连反"
    # canonical（会被归一掉的反向写法）不用给模型看
    assert "`源自`" not in p and "`属于`" not in p, "把反向写法也喂进去了，模型会两种混用"


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
def chat_过程和答案分开留档():
    """「我先查一下」「工具挂了」是过程，不该和最后那段有营养的话拌在一起读。"""
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat([tool_block("search_nodes", {"q": "A"}, text="我先去图里找找。"),
                             "找到了。核心差别是通用 vs 专用。"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "A 是什么"}]})
    finally:
        restore_chat(original)
    done = [e for e in sse_events(r) if e["type"] == "done"][-1]
    assert done["text"] == "找到了。核心差别是通用 vs 专用。", done["text"]
    assert len(done["trace"]) == 1 and done["trace"][0].startswith("我先去图里找找。"), done["trace"]

    path = next((vault / ".knowrary" / "chat").rglob("*.jsonl"))
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    said = [x for x in rows if x["role"] == "assistant"][-1]
    assert said["text"] == done["text"] and said["trace"] == done["trace"], said
    # 回看时也是分开的，否则历史还是得整段重读
    hist = c.get("/api/chat/history").json()["messages"]
    assert hist[-1]["trace"] == done["trace"], hist[-1]


@case
def chat_检验题进题库():
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat(["A 是一个测试节点。\n\n```check\nA 的关键机制是什么？\n考点: a\n```"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "讲讲 A"}]})
    finally:
        restore_chat(original)
    evs = sse_events(r)
    q = [e for e in evs if e["type"] == "question"]
    assert q and q[0]["stem"] == "A 的关键机制是什么？" and q[0]["points"] == ["a"], evs
    pool = core.load_pool(vault)
    assert [x["stem"] for x in pool["questions"]] == ["A 的关键机制是什么？"], pool
    # 题干留在正文里（那句问话本来就是对话的一部分），摘掉的只是围栏
    done = [e for e in evs if e["type"] == "done"][-1]
    assert done["text"].endswith("A 的关键机制是什么？") and "```" not in done["text"], done["text"]


@case
def chat_会话能改名而且不建会话表():
    """名字默认取第一句我说的话；改过的存成一张「id → 名字」的贴纸，撕掉就回到自动的。"""
    c, vault, _ = with_inbox_node()
    original, _ = stub_chat(["好的"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "NPU 和 GPU 的核心差别是什么"}],
                                  "session": "s1"})
    finally:
        restore_chat(original)
    rows = c.get("/api/chat/sessions").json()["sessions"]
    assert rows[0]["title"].startswith("NPU 和 GPU"), rows
    assert rows[0]["renamed"] is False and rows[0]["auto"] == rows[0]["title"], rows

    r = c.patch("/api/chat/sessions/s1", json={"title": "  端侧推理那次  "})
    assert r.json()["title"] == "端侧推理那次", r.text
    rows = c.get("/api/chat/sessions").json()["sessions"]
    assert rows[0]["title"] == "端侧推理那次" and rows[0]["renamed"] is True, rows
    assert rows[0]["auto"].startswith("NPU 和 GPU"), "自动取的那个还要留着当占位符"
    # 贴纸就是一个小文件，删了只是回到自动标题
    assert (vault / ".knowrary" / "chat" / "_scratch" / "titles.json").exists()

    c.patch("/api/chat/sessions/s1", json={"title": ""})
    rows = c.get("/api/chat/sessions").json()["sessions"]
    assert rows[0]["title"].startswith("NPU 和 GPU") and rows[0]["renamed"] is False, rows


@case
def chat_最后一条必须是我说的话():
    c, _, _ = with_inbox_node()
    r = c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "在"}]})
    assert [e for e in sse_events(r) if e["type"] == "error"], r.text


@case
def chat_一轮里的几次工具往返只往尾巴上追加():
    """一个用户回合常常夹着三四次工具往返，**那几次才是账单的大头**。

    它们之间只差末尾几百个字，所以前面那一截**一个字都不能动**——anthropic 的缓存断点、
    openai 的自动前缀缓存，要的都是逐字节相同的前缀。这条用例钉的就是"只增不改"：
    第 n+1 步发出去的消息，前 len(第 n 步) 条必须和第 n 步一模一样。

    顺带钉住工具 schema：每一步都得带上，而且是同一份。
    """
    c, _, _ = with_inbox_node()
    original, seen = stub_chat([tool_block("search_nodes", {"q": "A"}),
                                tool_block("overview", {}), "讲完了"])
    try:
        c.post("/api/chat", json={"messages": [{"role": "user", "content": "我图里有什么"}],
                                  "session": "s1", "project": "llm", "stance": "教练"})
    finally:
        restore_chat(original)

    assert len(seen) == 3, len(seen)
    for step in range(1, len(seen)):
        prev, now = seen[step - 1], seen[step]
        assert len(now) > len(prev), f"第 {step} 步没变长，工具往返丢了"
        for i, (a, b) in enumerate(zip(prev, now)):
            assert a == b, f"第 {step} 步动了第 {i} 条（{a.get('role')}）——前缀断在这里\n{a}\n{b}"

    names = [[t["name"] for t in ts] for ts in seen.tools]
    assert all(n == names[0] and n for n in names), names


@case
def llm_工具协议按provider能力自动挑_上层看不见区别():
    """**对上只有一个契约**：给 `tools`、拿回结构化的 `calls`。底下两套实现自动选。

    `claude -p` 是子进程，没有结构化工具接口，所以它那条路是文本围栏适配；
    另外两条走原生。**围栏只活在 `_chat_claude_cli` 一个函数里**——
    agent 循环那边一行 if 都没有，它不知道底下用的是哪套协议。

    砍掉 claude-cli 的对话能力是最省事的做法，但它是零配置的默认 provider
    （复用本机 Claude Code 登录、不用填任何密钥），砍了等于"想聊天先去开个 API key"。
    """
    import llm_backend as backend
    assert backend.supports_tools({"type": "anthropic"}) is True
    assert backend.supports_tools({"type": "openai"}) is True
    assert backend.supports_tools({"type": "claude-cli"}) is False, "claude -p 哪来的结构化工具通道"
    # 零配置那条路照样能聊：模板和内置默认都指着 claude-cli
    assert backend.resolve_provider(backend.DEFAULT_CONFIG, "learn")[1]["type"] == "claude-cli"

    tools = [{"name": "search_nodes", "description": "搜", "input_schema": {"type": "object"}},
             {"name": "overview", "description": "概况", "input_schema": {"type": "object"}}]
    seen: dict = {}

    def fake_cli(messages, model, on_delta, session):
        seen["prompt"] = backend._render_transcript(messages)
        return '我查一下。\n```knowrary\n{"tool": "search_nodes", "args": {"q": "注意力"}}\n```', backend.empty_usage()

    original = backend._run_claude_cli
    backend._run_claude_cli = fake_cli
    try:
        text, calls, _ = backend.chat([{"role": "user", "content": "我图里有什么"}],
                                      {"type": "claude-cli"}, tools=tools)
    finally:
        backend._run_claude_cli = original

    # 回来的形状和原生那条**一模一样**
    assert [(c["name"], c["args"]) for c in calls] == [("search_nodes", {"q": "注意力"})], calls
    assert calls[0]["id"], "没有 id 的话 tool_result 对不回 tool_use"
    assert text == "我查一下。", ("围栏没剥干净，屏幕上会闪过一段 JSON", text)
    # 协议机制进了提示词，但**不重复抄一遍工具表**（那份已经在 chat.md 里渲染过）
    assert "```knowrary" in seen["prompt"] and "不要一次调两个" in seen["prompt"]
    assert "`search_nodes`、`overview`" in seen["prompt"], seen["prompt"][:300]


@case
def llm_围栏那条路的转录要能原样喂回去():
    """`claude -p` 收的是一段纯文本：没有 messages 数组、没有 tool_result 通道，
    工具往返只能写成对话里的话。**渲染必须是确定性的**——`--resume` 的前缀指纹按它算。
    """
    import llm_backend as backend
    msgs = [{"role": "system", "content": "你是教练"},
            {"role": "user", "content": "我图里有啥"},
            {"role": "assistant", "content": "我查一下", "tool_calls": [
                {"id": "t1", "name": "overview", "args": {}}]},
            {"role": "tool", "tool_call_id": "t1", "name": "overview", "content": "95 个节点"}]
    text = backend._render_transcript(msgs)
    assert '{"tool": "overview", "args": {}}' in text, text
    assert "[工具 overview 的结果]" in text and "95 个节点" in text, text
    assert backend._render_transcript(msgs) == text, "渲染不确定 = 指纹每次都变 = 永远续不上"

    # 正文一样但调的工具不一样：指纹必须分得开，否则会续上一段其实不同的上下文
    other = [*msgs[:2], {"role": "assistant", "content": "我查一下", "tool_calls": [
        {"id": "t1", "name": "search_nodes", "args": {"q": "a"}}]}]
    assert backend._fingerprint(msgs[:3]) != backend._fingerprint(other), "指纹撞了"


@case
def llm_围栏捞不到就只能当没调_这是它的先天缺陷():
    """原生协议里参数写坏了仍然算"调过这个工具"（带空参数交上去让工具报错）；
    围栏这边连"他是不是想调工具"都判不出来，只能当他没调——**那段话会直接成为答案**。

    钉住这条不是为了接受它，是为了记住**能走原生的一律走原生**的理由。
    """
    import llm_backend as backend
    assert backend._parse_fence("这就是我的回答，没调工具") == []
    assert backend._parse_fence('```knowrary\n{"tool": "read_node", "args": {"id": \n```') == [], "半截 JSON 不该蒙混过去"
    assert backend._parse_fence('```knowrary\n{"args": {"q": "x"}}\n```') == [], "没有 tool 名"
    # args 缺了 / 不是对象都当空参数，工具那边会把话说回给模型
    assert backend._parse_fence('```knowrary\n{"tool": "overview"}\n```')[0]["args"] == {}


@case
def llm_原生工具往返翻译成两家各自的形状():
    """内部消息形状是中立的，provider 差异只在 `_to_anthropic` / `_to_openai` 这一层。

    最容易错的是 anthropic：`tool_result` 必须和它的 `tool_use` 在相邻的两条消息里，
    而且**同一个 assistant 回合的几个结果要并进同一条 user**，拆开会被 API 拒。
    """
    import llm_backend as backend
    calls = [{"id": "t1", "name": "search_nodes", "args": {"q": "A"}},
             {"id": "t2", "name": "overview", "args": {}}]
    msgs = [{"role": "user", "content": "问"},
            {"role": "assistant", "content": "我查一下", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "t1", "name": "search_nodes", "content": "结果1"},
            {"role": "tool", "tool_call_id": "t2", "name": "overview", "content": "结果2"}]

    a = backend._to_anthropic(msgs)
    assert [m["role"] for m in a] == ["user", "assistant", "user"], [m["role"] for m in a]
    assert [b["type"] for b in a[1]["content"]] == ["text", "tool_use", "tool_use"], a[1]
    assert a[1]["content"][1]["input"] == {"q": "A"}, a[1]["content"][1]
    assert [b["tool_use_id"] for b in a[2]["content"]] == ["t1", "t2"], a[2]

    o = backend._to_openai(msgs)
    assert [m["role"] for m in o] == ["user", "assistant", "tool", "tool"], [m["role"] for m in o]
    assert json.loads(o[1]["tool_calls"][0]["function"]["arguments"]) == {"q": "A"}, o[1]
    assert o[2]["tool_call_id"] == "t1" and o[3]["tool_call_id"] == "t2", o[2:]

    # 只调工具、一个字没说的那种回合：assistant 正文是空的，但这条**不能被丢掉**，
    # 丢了 tool_result 就找不到它的 tool_use，整段上下文是坏的
    silent = [{"role": "assistant", "content": "", "tool_calls": calls[:1]},
              {"role": "tool", "tool_call_id": "t1", "name": "search_nodes", "content": "r"}]
    _, rest = backend._split_system([{"role": "system", "content": "S"}, *silent])
    assert len(rest) == 2, rest
    assert [b["type"] for b in backend._to_anthropic(rest)[0]["content"]] == ["tool_use"], rest


@case
def llm_流式的工具参数是碎片_要攒齐再解析():
    """两家的流式都把 `arguments` 拆成一片一片发，中途每一片都不是合法 JSON。

    攒不齐就解析，结果是"模型明明调了工具，服务端当它没调"——正是原生协议要根治的那种静默失败。
    """
    import llm_backend as backend
    pending = {0: {"id": "t1", "name": "search_nodes", "json": '{"q": "注'},
               1: {"id": "t2", "name": "overview", "json": ""}}
    pending[0]["json"] += '意力"}'
    calls = backend._finish_calls(pending)
    assert calls == [{"id": "t1", "name": "search_nodes", "args": {"q": "注意力"}},
                     {"id": "t2", "name": "overview", "args": {}}], calls

    # 参数真的写坏了：**仍然算调过这个工具**，带空参数交上去，让工具自己报错把话说回给模型
    broken = backend._finish_calls({0: {"id": "t", "name": "read_node", "json": '{"id": '}})
    assert broken == [{"id": "t", "name": "read_node", "args": {}}], broken


@case
def chat_一步里可以并行调几个工具():
    """文本围栏时代只能一次一个（一个块、然后停下），"搜一下再读三个节点"要烧掉四个来回。

    原生协议本来就允许并行，放开之后这些活儿一步就干完了。
    """
    c, _, _ = with_inbox_node()
    original, seen = stub_chat([tool_blocks(("search_nodes", {"q": "A"}), ("overview", {})),
                                "两样都查完了"])
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "看看"}]})
    finally:
        restore_chat(original)
    assert len(seen) == 2, "并行调用被拆成了两步"
    tools = [e for e in sse_events(r) if e["type"] == "tool"]
    assert [t["name"] for t in tools] == ["search_nodes", "overview"], tools
    # 两条结果都得回灌，而且各自对上自己的 tool_call_id
    fed = [m for m in seen[1] if m.get("role") == "tool"]
    assert [m["name"] for m in fed] == ["search_nodes", "overview"], fed
    ids = {c["id"] for c in seen[1][-3]["tool_calls"]}
    assert {m["tool_call_id"] for m in fed} == ids, (fed, ids)


@case
def llm_两家的流式工具调用都能跑通一整趟():
    """端到端替掉 `_sse`：payload 里真带了 tools，流里的 tool_use / tool_calls 真被解析出来。

    前面那几条钉的是翻译和拼接两个零件，这条钉的是**整条路**——零件都对、
    payload 里忘了塞 tools 的话，表现是模型一个工具都不调，而且不报错。
    """
    import llm_backend as backend
    tools = [{"name": "search_nodes", "description": "搜", "input_schema": {"type": "object"}}]
    msgs = [{"role": "system", "content": "S" * 50}, {"role": "user", "content": "我图里有什么"}]
    sent: list = []
    original = backend._sse

    def anthropic_stream(url, headers, payload):
        sent.append(payload)
        yield "message_start", {"message": {"usage": {"input_tokens": 9}}}
        yield "content_block_delta", {"index": 0, "delta": {"text": "我查一下"}}
        yield "content_block_start", {"index": 1, "content_block": {
            "type": "tool_use", "id": "toolu_1", "name": "search_nodes"}}
        yield "content_block_delta", {"index": 1, "delta": {"partial_json": '{"q": "注'}}
        yield "content_block_delta", {"index": 1, "delta": {"partial_json": '意力"}'}}

    backend._sse = anthropic_stream
    try:
        text, calls, usage = backend._chat_anthropic(msgs, {"api_key": "k"}, "claude-opus-5",
                                                     lambda t: None, tools)
    finally:
        backend._sse = original
    assert text == "我查一下", text
    assert calls == [{"id": "toolu_1", "name": "search_nodes", "args": {"q": "注意力"}}], calls
    assert usage["input_tokens"] == 9, usage
    assert [t["name"] for t in sent[0]["tools"]] == ["search_nodes"], sent[0].get("tools")
    # 工具表和顶层 system 一样是逐字不变的一大块，而且排在 system 之前：不标它就永远不进缓存
    assert sent[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}, sent[0]["tools"][-1]

    sent.clear()

    def openai_stream(url, headers, payload):
        sent.append(payload)
        yield "", {"choices": [{"delta": {"content": "我查一下"}}]}
        yield "", {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "search_nodes", "arguments": '{"q": "注'}}]}}]}
        yield "", {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '意力"}'}}]}}]}
        yield "", {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}

    backend._sse = openai_stream
    try:
        text, calls, usage = backend._chat_openai(msgs, {"base_url": "http://x/v1"}, "qwen-max",
                                                  lambda t: None, tools)
    finally:
        backend._sse = original
    assert text == "我查一下", text
    assert calls == [{"id": "call_1", "name": "search_nodes", "args": {"q": "注意力"}}], calls
    assert usage["input_tokens"] == 9 and usage["output_tokens"] == 2, usage
    fn = sent[0]["tools"][0]
    assert fn["type"] == "function" and fn["function"]["name"] == "search_nodes", fn


@case
def llm_openai兼容那条路的用量三支都要记上():
    """非流式那支原来直接还一张空表，token 和命中数全丢账。

    丢账的样子和"真的没命中"**一模一样**：都是 0。于是排查缓存时看到的是一条
    假信号，而这条路不报错、答案也全对，只有账本在骗人。

    顺带钉住 openai 兼容口径的另一半：`cache_write_tokens` 恒为 0 是**接口没有这一列**
    （隐式缓存不报写入），不是没缓存——判命中要看 `cache_read ÷ input`。
    """
    import llm_backend as backend
    raw = {"prompt_tokens": 1200, "completion_tokens": 40,
           "prompt_tokens_details": {"cached_tokens": 1024}}
    original = backend._post_json
    backend._post_json = lambda url, headers, payload: {
        "model": "qwen-plus", "usage": raw,
        "choices": [{"message": {"content": "答完了", "tool_calls": [
            {"id": "call_1", "function": {"name": "overview", "arguments": "{}"}}]}}]}
    try:
        text, calls, usage = backend._chat_openai(
            [{"role": "user", "content": "我图里有什么"}],
            {"base_url": "http://x/v1"}, "qwen-plus", None, None)
    finally:
        backend._post_json = original
    assert text == "答完了" and [c["name"] for c in calls] == ["overview"], (text, calls)
    assert usage["input_tokens"] == 1200 and usage["output_tokens"] == 40, usage
    assert usage["cache_read_tokens"] == 1024, usage
    assert usage["cache_write_tokens"] == 0, usage   # 这一路没有"写"这一列
    assert usage["model"] == "qwen-plus", usage      # 报回来的型号优先于配置里写的

    # 单轮那支（suggest / quiz / 拆计划走的就是它）用同一份解析
    backend._post_json = lambda url, headers, payload: {
        "model": "qwen-plus", "usage": raw, "choices": [{"message": {"content": "好"}}]}
    try:
        _, single = backend._ask_openai("问一句", {"base_url": "http://x/v1"}, "qwen-plus")
    finally:
        backend._post_json = original
    assert single["cache_read_tokens"] == 1024 and single["input_tokens"] == 1200, single


@case
def chat_claude_cli那条路也能走完整一轮工具往返():
    """**端到端**：provider 配成 claude-cli，整条 agent 循环照跑，SSE 事件一个不少。

    只测适配器不够——要证明的是"上层看不见区别"，那就得从 `/api/chat` 打进去，
    看工具真的被执行、结果真的回灌、答案真的出来。这条路上：
    - 工具协议是文本围栏（`claude -p` 没有结构化通道）；
    - `_run` 那边一行 if 都没有，它拿到的还是结构化的 calls。
    """
    import llm_backend as backend
    c, vault, _ = with_inbox_node()
    core.write(vault / ".knowrary" / "llm.local.json", json.dumps({
        "providers": {"cli": {"type": "claude-cli"}},
        "roles": {"learn": "cli", "review": "cli"}}, ensure_ascii=False))

    turns_seen: list = []
    replies = ['我先查一下。\n```knowrary\n{"tool": "search_nodes", "args": {"q": "A"}}\n```',
               "图里有 a 这个节点。"]

    def fake_cli(messages, model, on_delta, session):
        turns_seen.append(backend._render_transcript(messages))
        text = replies[len(turns_seen) - 1] if len(turns_seen) <= len(replies) else "没话说了"
        if on_delta:
            on_delta(text)
        return text, backend.empty_usage()

    original = backend._run_claude_cli
    backend._run_claude_cli = fake_cli
    try:
        r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "我图里有什么"}],
                                      "session": "s1"})
    finally:
        backend._run_claude_cli = original

    evs = sse_events(r)
    tools = [e for e in evs if e["type"] == "tool"]
    assert [t["name"] for t in tools] == ["search_nodes"], tools
    done = [e for e in evs if e["type"] == "done"][-1]
    assert done["text"] == "图里有 a 这个节点。", done["text"]
    assert "```knowrary" not in done["text"], "围栏漏进了给人看的正文"
    assert done["trace"] == ["我先查一下。"], done["trace"]

    # 第二次调用的转录里：协议说明、上一步的围栏、工具结果，三样都在
    assert len(turns_seen) == 2, turns_seen
    assert "```knowrary" in turns_seen[0], "协议说明没进提示词，模型根本不知道怎么调"
    assert "[工具 search_nodes 的结果]" in turns_seen[1], turns_seen[1][-400:]


@case
def llm_围栏那条路的会话在工具往返之间要续得上():
    """`--resume` 的全部前提：**这一段只增不改**。围栏这侧有两个额外的坑：

    1. 适配层会在最前面插一条协议说明——它必须每步都一模一样，否则前缀天天变；
    2. 记指纹时要记**调用方随后会追加的那条 assistant**（正文 + 这一步调的工具），
       不是 CLI 吐出来的原文。两边形状对不上就等于每轮都在重开会话，
       **而且不报错，只是账单悄悄变贵**——正是复盘 §11.2 那个 67 段里 54 段的老毛病。
    """
    import llm_backend as backend
    tools = [{"name": "overview", "description": "概况", "input_schema": {"type": "object"}}]
    key = "围栏续接回归"
    backend.drop_cli_session(key)

    seen: list = []
    original = backend._run_claude_cli

    def fake(messages, model, on_delta, session):
        extra, start = backend._cli_session(session, messages, model)
        seen.append({"extra": extra[0], "start": start, "sent": len(messages)})
        text = ('我查一下。\n```knowrary\n{"tool": "overview", "args": {}}\n```'
                if len(seen) == 1 else "图里有 95 个节点。")
        return text, backend.empty_usage()

    backend._run_claude_cli = fake
    try:
        msgs = [{"role": "system", "content": "静态指令"}, {"role": "user", "content": "我图里有啥"}]
        text, calls, _ = backend.chat(msgs, {"type": "claude-cli"}, tools=tools, session=key)
        assert [c["name"] for c in calls] == ["overview"], calls

        # 调用方（server/chat.py 的 _run）随后会这样往尾巴上追加
        msgs = [*msgs, {"role": "assistant", "content": text, "tool_calls": calls},
                {"role": "tool", "tool_call_id": calls[0]["id"], "name": "overview",
                 "content": "95 个节点"}]
        backend.chat(msgs, {"type": "claude-cli"}, tools=tools, session=key)
    finally:
        backend._run_claude_cli = original
        backend.drop_cli_session(key)

    assert seen[0]["extra"] == "--session-id" and seen[0]["start"] == 0, seen[0]
    assert seen[1]["extra"] == "--resume", (seen[1], "第二步没续上 = 又在重发全文")
    # 只发新增的那一条（工具结果）：协议说明 + 原来两条 + 上一步的 assistant 都在 CLI 那侧了
    assert seen[1]["start"] == seen[0]["sent"] + 1, seen
    assert seen[1]["sent"] - seen[1]["start"] == 1, (seen, "续上了却还在重发前面几条")


# ---------------------------------------------------------------- 设置页：模型配置

LLM_CFG = {
    "_说明": "本机配置的注释行，读写都要原样留着",
    "providers": {
        "claude-cli": {"_说明": "兜底", "type": "claude-cli"},
        "bailian": {"type": "openai", "model": "qwen-plus",
                    "base_url": "https://dash/v1", "api_key": "sk-old", "max_tokens": 16000},
    },
    "roles": {"_说明": "注释", "learn": "bailian", "review": "bailian"},
}


def llm_client() -> tuple[TestClient, Path]:
    """模型配置是**用户级**的（`~/.knowrary/llm.local.json`），所以写到 KNOWRARY_HOME 里，
    不在库里——每个用例现开一份，互不干扰。返回的第二项仍是 vault（别的断言要用）。"""
    c, vault = client()
    core.write_json_atomic(llm_home() / "llm.local.json", json.loads(json.dumps(LLM_CFG)))
    return c, vault


def llm_home() -> Path:
    """每个模型用例一份干净的用户级目录：不然前一条改的 provider 会串进下一条。"""
    home = Path(tempfile.mkdtemp(prefix="knowrary-llmhome-")).resolve()
    os.environ["KNOWRARY_HOME"] = str(home)
    return home


@case
def 新增的provider还没保存就能先测一把():
    """设置页「添加模型」里点测试连接：配置文件一个字都不许动，密钥用输入框里现填的那个。"""
    import llm_backend
    real = llm_backend.ask_detailed
    seen = {}
    llm_backend.ask_detailed = lambda prompt, provider, m=None: (
        seen.update(provider) or ("OK", {"model": provider.get("model")}))
    c, vault = llm_client()
    cfg_path = Path(os.environ["KNOWRARY_HOME"]) / "llm.local.json"
    before = cfg_path.read_text("utf-8")
    try:
        r = c.post("/api/llm/config/test", json={"provider": {
            "name": "qwen3-plus", "type": "openai", "model": "qwen3-plus",
            "base_url": "https://dash/v1", "api_key": "sk-new",
            "max_tokens": None, "temperature": None}})
    finally:
        llm_backend.ask_detailed = real
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "qwen3-plus", r.text
    assert seen.get("api_key") == "sk-new", (seen, "测的不是输入框里那把钥匙")
    assert cfg_path.read_text("utf-8") == before, "测试写了配置文件"


@case
def 编辑已有provider时留空的密钥不算清空():
    """列表回显时 api_key 一律是空串（后端从不回显），拿它去测必须落回老密钥。"""
    import llm_backend
    real = llm_backend.ask_detailed
    seen = {}
    llm_backend.ask_detailed = lambda prompt, provider, m=None: (
        seen.update(provider) or ("OK", {"model": provider.get("model")}))
    c, _ = llm_client()
    row = [p for p in c.get("/api/llm/config").json()["providers"] if p["name"] == "bailian"][0]
    assert row["api_key_set"] is True and "api_key" not in row, row
    try:
        r = c.post("/api/llm/config/test", json={"provider": dict(row, api_key="")})
    finally:
        llm_backend.ask_detailed = real
    assert r.status_code == 200, r.text
    assert seen.get("api_key") == "sk-old", (seen, "留空被当成清空，等于拿空钥匙去连")


@case
def 保存新增的provider不碰别人的密钥和注释():
    c, vault = llm_client()
    draft = [dict(p, api_key="") for p in c.get("/api/llm/config").json()["providers"]]
    draft.append({"name": "qwen3-plus", "type": "openai", "model": "qwen3-plus",
                  "base_url": "https://dash/v1", "api_key": "sk-new",
                  "max_tokens": None, "temperature": None})
    r = c.put("/api/llm/config", json={"providers": draft,
                                       "roles": {"learn": "bailian", "review": "bailian"}})
    assert r.status_code == 200, r.text
    assert [p["name"] for p in r.json()["providers"]] == ["claude-cli", "bailian", "qwen3-plus"]
    saved = json.loads((Path(os.environ["KNOWRARY_HOME"]) / "llm.local.json").read_text("utf-8"))
    assert saved["providers"]["bailian"]["api_key"] == "sk-old", "别人的密钥被空串洗掉了"
    assert saved["providers"]["qwen3-plus"]["api_key"] == "sk-new", saved
    assert saved["_说明"] == LLM_CFG["_说明"] and "_说明" in saved["roles"], "注释行被吃了"
    assert (Path(os.environ["KNOWRARY_HOME"]) / "backup").exists(), "改配置前没留备份"


@case
def 改名之后roles要跟着指过去():
    """前端改名时会把 roles 一起迁走；迁漏了服务端就该明确打回，而不是默默存一份指空的配置。"""
    c, _ = llm_client()
    draft = [dict(p, api_key="") for p in c.get("/api/llm/config").json()["providers"]]
    draft[1]["name"] = "bailian-2"
    stale = c.put("/api/llm/config", json={"providers": draft,
                                           "roles": {"learn": "bailian", "review": "bailian"}})
    assert stale.status_code == 422, stale.text
    assert "`bailian`" in stale.json()["detail"], (stale.text, "报错没点名是哪个 provider 没了")
    ok = c.put("/api/llm/config", json={"providers": draft,
                                        "roles": {"learn": "bailian-2", "review": "bailian-2"}})
    assert ok.status_code == 200, ok.text
    assert ok.json()["roles"] == {"learn": "bailian-2", "review": "bailian-2"}, ok.text


# ---------------------------------------------------------------- 跨库复制

COPY_SRC = {
    "nodes/网络/quic.md": node_md("QUIC", field="网络",
                                  extra="year: 2021\nlayer: 系统软件\ntags:\n  - 示例\n",
                                  rels="- 基于:: [[udp]] — 借它的什么都不做\n- 相关:: [[队头阻塞]]"),
    "nodes/网络/udp.md": node_md("UDP", field="网络", extra="year: 1980\n"),
    "nodes/网络/tcp.md": node_md("TCP", field="网络", rels="- 对比:: [[udp]]"),
}


def copy_pair() -> tuple[TestClient, Path, Path]:
    """一个源库（别人的参考库）+ 一个目标库（我自己的），当前库是目标库。"""
    from server import vaults
    base = tmp_dir("knowrary-copy-")
    src = base / "参考库"
    (src / ".knowrary").mkdir(parents=True)
    (src / "relation-types.json").write_text((REPO / "seed" / "relation-types.json").read_text("utf-8"), "utf-8")
    for rel, text in COPY_SRC.items():
        core.write(src / rel, text)
    target = base / "我的库"
    target.mkdir()
    home = tmp_dir("knowrary-copyhome-")
    os.environ["KNOWRARY_HOME"] = str(home)
    os.environ.pop("KNOWRARY_VAULT", None)
    vaults.init(target)
    vaults.save({"schema_version": 1, "current": str(target), "recent": [str(target), str(src)]})
    index_service.invalidate()
    return TestClient(app), src, target


@case
def 跨库复制把正文和关系一起抄过来():
    """抄来的点落在固定的收件箱目录，但 **field 保留源库的**——
    压平成一个领域的话，抄 8 个点回来会挤成一坨，以后往各领域搬还得重新判断。"""
    c, src, target = copy_pair()
    r = c.post("/api/copy", json={"source": str(src), "ids": ["quic"], "dry_run": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True and body["picked"] == 1, body
    md = (target / "nodes" / "_抄来的" / "quic.md").read_text("utf-8")
    assert "field: 网络" in md, ("领域没保留", md[:200])
    assert "year: 2021" in md and "layer: 系统软件" in md, ("frontmatter 掉字段了", md[:200])
    assert "source: 抄自 参考库" in md and f"learned: {dt.date.today().isoformat()}" in md, md[:200]
    assert "- 基于:: [[udp]]" in md and "借它的什么都不做" in md, ("关系连说明一起抄", md)
    assert body["log"].startswith(".knowrary/imports/"), body["log"]


@case
def 指向没抄的点要补壳而不是丢边():
    """丢掉边等于悄悄削掉知识结构，事后根本不知道缺了什么。"""
    c, src, target = copy_pair()
    c.post("/api/copy", json={"source": str(src), "ids": ["quic"], "dry_run": False})
    assert (target / "nodes" / "_stubs" / "udp.md").exists(), "udp 没抄也没补壳"
    assert (target / "nodes" / "_stubs" / "队头阻塞.md").exists(), "源库里根本没有的目标也该补壳"
    stub = (target / "nodes" / "_stubs" / "udp.md").read_text("utf-8")
    assert "status: stub" in stub and "参考库" in stub, ("壳里要写清它从哪来", stub)


@case
def 勾上邻居就真把一跳抄过来():
    c, src, target = copy_pair()
    body = c.post("/api/copy", json={"source": str(src), "ids": ["quic"],
                                     "with_neighbors": True, "dry_run": False}).json()
    assert body["picked"] == 2, ("quic 一跳内有 udp", body["picked"])
    md = (target / "nodes" / "_抄来的" / "udp.md").read_text("utf-8")
    assert "year: 1980" in md, ("邻居要抄成完整节点，不是壳", md[:200])
    assert not (target / "nodes" / "_stubs" / "udp.md").exists(), "既然抄了完整的，就不该再有壳"


@case
def 连到目标库已有同id的点要进待审():
    """**同 id 不等于同概念**，跨库尤其：你库里的「Agent」和我库里的「Agent」很可能不是一回事。"""
    c, src, target = copy_pair()
    core.write(target / "nodes/网络/udp.md",
               node_md("UDP", field="网络", extra="desc: 我自己写的 UDP\n"))
    index_service.invalidate()
    body = c.post("/api/copy", json={"source": str(src), "ids": ["quic"], "dry_run": False}).json()
    assert [(p["source"], p["relation"], p["target"]) for p in body["pending"]] == [("quic", "基于", "udp")], body["pending"]
    md = (target / "nodes" / "_抄来的" / "quic.md").read_text("utf-8")
    assert "[[udp]]" not in md, ("待审的边不该直接写进关系段", md)
    mine = (target / "nodes/网络/udp.md").read_text("utf-8")
    assert "我自己写的 UDP" in mine, "把人家自己的节点覆盖了"


@case
def 重复抄同一个点不覆盖只提醒():
    c, src, target = copy_pair()
    c.post("/api/copy", json={"source": str(src), "ids": ["quic"], "dry_run": False})
    core.write(target / "nodes/_抄来的/quic.md",
               (target / "nodes/_抄来的/quic.md").read_text("utf-8") + "\n我后来补的一段\n")
    index_service.invalidate()
    body = c.post("/api/copy", json={"source": str(src), "ids": ["quic"], "dry_run": False}).json()
    assert not body["files"], ("不该有写入", body["files"])
    assert any("已存在" in w for w in body["warnings"]), body["warnings"]
    assert "我后来补的一段" in (target / "nodes/_抄来的/quic.md").read_text("utf-8"), "把我补的内容冲掉了"


@case
def 站在参考库里也能往外推():
    """两个方向共用一条接口：从别处拉（不给 target）、往别处推（给 target）。
    看别人的库时看到好东西，直接抄进自己的库，不用先切过去。"""
    from server import vaults
    c, src, target = copy_pair()
    vaults.switch(src)                     # 现在站在参考库里
    index_service.invalidate()
    body = c.post("/api/copy", json={"source": str(src), "target": str(target),
                                     "ids": ["quic"], "dry_run": False}).json()
    assert body["applied"] is True, body
    assert body["target_vault"] == str(target), body
    assert (target / "nodes" / "_抄来的" / "quic.md").exists(), "没写进目标库"
    assert not (src / "nodes" / "_抄来的").exists(), "把参考库自己也写脏了"
    outsider = tmp_dir("knowrary-outsider2-")
    (outsider / ".knowrary").mkdir()
    bad = c.post("/api/copy", json={"source": str(src), "target": str(outsider), "ids": ["quic"]})
    assert bad.status_code == 422, ("写入方也得在放行名单里", bad.text)


@case
def 只能从认识的库里抄():
    """这条链路会读另一个目录里的 md，放行名单和目录浏览是同一套，不许传任意路径。"""
    c, src, target = copy_pair()
    outsider = tmp_dir("knowrary-outsider-")
    (outsider / ".knowrary").mkdir()
    r = c.post("/api/copy", json={"source": str(outsider), "ids": ["x"]})
    assert r.status_code == 422 and "不在可选的知识库里" in r.json()["detail"], r.text
    same = c.post("/api/copy", json={"source": str(target), "ids": ["quic"]})
    assert same.status_code == 422 and "同一个" in same.json()["detail"], same.text


@case
def 源库清单能搜能排():
    c, src, target = copy_pair()
    rows = c.get("/api/copy/sources").json()
    assert {r["name"] for r in rows} == {"我的库", "参考库"}, rows
    assert next(r for r in rows if r["name"] == "我的库")["current"] is True, rows
    assert next(r for r in rows if r["name"] == "参考库")["nodes"] >= 3, rows
    cat = c.get("/api/copy/catalog", params={"source": str(src), "q": "ud"}).json()
    assert [n["id"] for n in cat["nodes"]] == ["udp"], cat
    full = c.get("/api/copy/catalog", params={"source": str(src)}).json()
    assert full["nodes"][0]["degree"] >= full["nodes"][-1]["degree"], ("该按度数排", full["nodes"])


# ---------------------------------------------------------------- 知识库（vault）选择

def tmp_dir(prefix: str) -> Path:
    """一个会被自动清掉的临时目录。**一律 resolve**：macOS 的 `/var` 是指向
    `/private/var` 的符号链接，而服务端回的全是规范路径，不摊平就永远对不上。"""
    tmp = tempfile.TemporaryDirectory(prefix=prefix)
    _TMPDIRS.append(tmp)
    return Path(tmp.name).resolve()


def vault_client(files: dict[str, str] | None = None) -> tuple[TestClient, Path, Path]:
    """起一个"没被环境变量钉住"的服务：当前库改由用户级配置说了算。

    这一组用例是整份自测里唯一**不设 `KNOWRARY_VAULT`** 的——设了就 pinned，
    切库会被钉住，正好测不到要测的东西。`KNOWRARY_HOME` 指到临时目录，
    真实的 `~/.knowrary` 一个字节都不许碰。

    库也**不直接建在系统临时根目录下**：那一层堆着上千个别人的临时目录，
    目录浏览的用例会把它们一起列出来（还会撞上 400 条的上限）。
    """
    from server import vaults
    base = tmp_dir("knowrary-base-")
    vault = base / "库"
    (vault / ".knowrary").mkdir(parents=True)
    (vault / "relation-types.json").write_text((REPO / "seed" / "relation-types.json").read_text("utf-8"), "utf-8")
    for rel, text in (files or DEFAULT_FILES).items():
        core.write(vault / rel, text)
    home = tmp_dir("knowrary-home-")
    os.environ["KNOWRARY_HOME"] = str(home)
    os.environ.pop("KNOWRARY_VAULT", None)
    vaults.save({"schema_version": 1, "current": str(vault), "recent": [str(vault)]})
    index_service.invalidate()
    return TestClient(app), vault, home


@case
def 没选过库时不拿仓库自己顶上():
    """2026-09-22 拆分前这里会回落到仓库根目录。数据搬走之后那条从兼容变成陷阱：
    在代码仓库里随手建个 nodes/ 就被当成库。现在一律明说"还没选"。"""
    from server import vaults
    make_vault()
    os.environ["KNOWRARY_HOME"] = str(Path(tempfile.mkdtemp(prefix="knowrary-home-")).resolve())
    os.environ.pop("KNOWRARY_VAULT", None)
    try:
        (REPO / "nodes").mkdir(exist_ok=True)      # 就算仓库里冒出个 nodes/ 也不许认
        try:
            vaults.current_vault()
            raise AssertionError("没选库却自作主张挑了一个")
        except vaults.NoVaultSelected as exc:
            assert "设置" in str(exc), str(exc)
        assert TestClient(app).get("/api/vault").json()["current"] is None
    finally:
        (REPO / "nodes").rmdir()
        os.environ.pop("KNOWRARY_HOME", None)


@case
def 一个库都没选时接口给409而不是崩():
    """程序还没被指向任何数据，是个要人做一次选择的状态——不是 404 也不是 500。"""
    make_vault()
    os.environ["KNOWRARY_HOME"] = str(Path(tempfile.mkdtemp(prefix="knowrary-home-")).resolve())
    os.environ.pop("KNOWRARY_VAULT", None)
    try:
        c = TestClient(app)
        r = c.get("/api/health")
        assert r.status_code == 409, (r.status_code, r.text)
        assert r.json()["need_vault"] is True, r.text
        assert c.get("/api/vault").status_code == 200, "选库接口自己也 409 了，人就没法自救"
        assert c.get("/api/vault/browse").status_code == 200, "浏览接口也挂了"
    finally:
        os.environ.pop("KNOWRARY_HOME", None)


@case
def 初始化空目录会铺出骨架和种子():
    c, _, home = vault_client()
    target = tmp_dir("knowrary-new-") / "新库"
    target.mkdir()
    r = c.post("/api/vault/init", json={"path": str(target)})
    assert r.status_code == 200, r.text
    assert r.json()["current"]["path"] == str(target), r.text
    for rel in ("nodes", "fields", "assets", ".knowrary/layouts", ".knowrary/imports", ".knowrary/coaches"):
        assert (target / rel).is_dir(), f"骨架少了 {rel}"
    # 库里只铺**数据**要用的那几份；模型、开关这些配置在 ~/.knowrary/，不进库
    for rel in ("relation-types.json", "README.md", ".gitignore", ".knowrary/coach.example.md"):
        assert (target / rel).is_file(), f"种子少了 {rel}"
    assert json.loads((home / "config.json").read_text("utf-8"))["current"] == str(target)
    assert not (target / ".knowrary" / "settings.json").exists(), "开关是用户级的，不该铺进库里"
    assert not (target / ".knowrary" / "llm.local.json").exists(), "密钥更不该铺进库里"
    assert c.get("/api/index").json()["stats"]["nodes"] == 0, "新库里不该有别人的节点"


@case
def 勾了示例才铺示例内容():
    """示例是给第一次打开的人看的，默认不铺——否则所有人建库的第一件事都是删它。"""
    c, _, _ = vault_client()
    blank = tmp_dir("knowrary-blank-") / "空库"
    blank.mkdir()
    c.post("/api/vault/init", json={"path": str(blank)})
    index_service.invalidate()
    assert c.get("/api/index").json()["stats"]["nodes"] == 0, "没勾却铺了示例"
    assert not (blank / "fields" / "网络.md").exists()

    withsample = tmp_dir("knowrary-sample-") / "带示例的库"
    withsample.mkdir()
    r = c.post("/api/vault/init", json={"path": str(withsample), "sample": True})
    assert r.status_code == 200, r.text
    # 这一条要在读索引之前问：读一次索引就会把 index.json 现建出来，之后再问就分不清是搬来的还是建的
    assert not (withsample / ".knowrary" / "index.json").exists(), "派生缓存不该跟着搬"
    index_service.invalidate()
    stats = c.get("/api/index").json()["stats"]
    assert stats["nodes"] == 9 and stats["stubs"] == 1, stats
    assert stats["errors"] == 0 and stats["warnings"] == 0, ("示例库自己都不合规", stats)
    assert set(stats["by_family"]) == {"结构", "依赖", "演化", "对照", "弱关联"}, (
        "示例要覆盖全部 5 个关系族，否则演示不出关系是怎么回事", stats["by_family"])
    doc = c.get("/api/projects").json()["doc"]
    assert "http-evolution" in doc["projects"], doc["projects"].keys()
    assert (withsample / ".knowrary" / "layout.json").exists(), "示例没带摆好的图，打开还是一团乱"
    assert (withsample / "素材").is_dir(), "没带可导入的素材，「导入」面板里一个文件都选不到"


@case
def 示例的复习与答题日期跟着今天走():
    """写死日期会烂：示例是某一天造的，半年后新用户看到「逾期 183 天」、热力图挤在一个旧月份，
    功能没坏但看着像坏了。锚点 + 平移让它无论哪天初始化都是"今天有一个到期"。"""
    import datetime as dt
    c, _, _ = vault_client()
    target = tmp_dir("knowrary-demo-") / "示例库"
    target.mkdir()
    assert c.post("/api/vault/init", json={"path": str(target), "sample": True}).status_code == 200

    assert not (target / ".knowrary" / "_demo.json").exists(), "锚点文件该铺完就删，别留给用户"
    log = core.load_json(target / ".knowrary" / "review-log.json")
    today = dt.date.today()
    dues = {nid: dt.date.fromisoformat(e["next_due"]) for nid, e in log["nodes"].items()}
    assert any(d == today for d in dues.values()), ("没有一个正好今天到期", dues)
    assert any(d < today for d in dues.values()), ("没有逾期的，「今日」就没东西可做", dues)
    assert any(d > today for d in dues.values()), ("全都到期＝看不出「学稳了就不打扰」", dues)
    assert max(abs((d - today).days) for d in dues.values()) <= 14, ("日期离今天太远，多半没平移", dues)
    assert any(e["lapses"] for e in log["nodes"].values()), "没有忘过一次的，看不出间隔会被打回去"

    quiz = core.load_json(target / ".knowrary" / "quiz-log.json")
    ts = [a["ts"][:10] for a in quiz["answers"]]
    assert all(abs((dt.date.fromisoformat(t) - today).days) <= 14 for t in ts), ("答题时间没跟着平移", ts)
    from core import quiz as quiz_core
    wrong = quiz_core.wrong_nodes(quiz)
    assert {w["id"] for w in wrong} == {"quic", "http-2"}, ("错题本该有两条", wrong)


@case
def 锚点过期时日期真的会被挪():
    """上一条在造数据的当天跑，平移量是 0 —— 等于没测。这条把锚点拨回 100 天前，
    看它是不是真挪了 100 天（半年后新用户遇到的正是这个分支）。"""
    import datetime as dt
    from server import vaults
    target = tmp_dir("knowrary-rebase-") / "旧示例"
    target.mkdir()
    vaults._copy_tree(vaults.SAMPLE, target)
    before = core.load_json(target / ".knowrary" / "review-log.json")
    old_due = {nid: e["next_due"] for nid, e in before["nodes"].items()}
    anchor = dt.date.today() - dt.timedelta(days=100)
    core.write_json_atomic(target / ".knowrary" / "_demo.json", {"anchor": anchor.isoformat()})

    vaults._rebase_demo(target)

    after = core.load_json(target / ".knowrary" / "review-log.json")
    for nid, e in after["nodes"].items():
        moved = (dt.date.fromisoformat(e["next_due"]) - dt.date.fromisoformat(old_due[nid])).days
        assert moved == 100, (nid, old_due[nid], e["next_due"], moved)
    assert not (target / ".knowrary" / "_demo.json").exists(), "锚点文件没删掉"
    quiz = core.load_json(target / ".knowrary" / "quiz-log.json")
    assert all(a["ts"].endswith("Z") for a in quiz["answers"]), ("平移把时间后缀吃掉了", quiz["answers"][0])


@case
def 锚点文件坏了也不许挡着建库():
    """示例数据平移失败最多是日期不新鲜，不该让人建不成库。"""
    from server import vaults
    target = tmp_dir("knowrary-badmark-") / "库"
    target.mkdir()
    vaults._copy_tree(vaults.SAMPLE, target)
    (target / ".knowrary" / "_demo.json").write_text("{不是 JSON", "utf-8")
    vaults._rebase_demo(target)
    assert not (target / ".knowrary" / "_demo.json").exists(), "坏掉的锚点文件也该清走"
    assert (target / ".knowrary" / "review-log.json").exists(), "复习记录被牵连删掉了"


@case
def 重复初始化不覆盖已有数据():
    """幂等：手滑点两次「初始化」不该把人的笔记洗掉。"""
    c, _, _ = vault_client()
    target = tmp_dir("knowrary-new-") / "新库"
    target.mkdir()
    c.post("/api/vault/init", json={"path": str(target)})
    core.write(target / "nodes" / "x.md", node_md("X"))
    (target / "README.md").write_text("我改过了", "utf-8")
    r = c.post("/api/vault/init", json={"path": str(target)})
    assert r.status_code == 200, r.text
    assert (target / "README.md").read_text("utf-8") == "我改过了", "种子把人改过的文件覆盖了"
    assert (target / "nodes" / "x.md").exists(), "节点被清掉了"


@case
def 非空又不像库的目录一律拒绝():
    """往别人的工程目录里撒十几个文件是不可逆的，这一步必须挡住，而且不许留下半个骨架。"""
    c, _, _ = vault_client()
    busy = tmp_dir("knowrary-busy-")
    (busy / "pom.xml").write_text("<project/>", "utf-8")
    r = c.post("/api/vault/init", json={"path": str(busy)})
    assert r.status_code == 422, (r.status_code, r.text)
    assert not (busy / "nodes").exists(), "拒绝了还是留了痕"
    assert c.put("/api/vault/current", json={"path": str(busy)}).status_code == 422, "切到非库目录居然成了"


@case
def 切库之后数据接口跟着换():
    c, vault, _ = vault_client()
    other = tmp_dir("knowrary-other-") / "另一个库"
    other.mkdir()
    c.post("/api/vault/init", json={"path": str(other)})
    index_service.invalidate()
    assert c.get("/api/health").json()["vault"] == str(other), "切了库 health 还指着老的"
    assert c.get("/api/index").json()["stats"]["nodes"] == 0
    r = c.put("/api/vault/current", json={"path": str(vault)})
    assert r.status_code == 200, r.text
    index_service.invalidate()
    assert c.get("/api/health").json()["vault"] == str(vault), "切回去没生效"
    assert c.get("/api/index").json()["stats"]["nodes"] == 3, c.get("/api/index").json()["stats"]
    assert any(e["path"] == str(other) for e in r.json()["recent"]), r.text


@case
def 最近列表能移除但不删目录():
    c, vault, _ = vault_client()
    other = tmp_dir("knowrary-other-") / "另一个库"
    other.mkdir()
    c.post("/api/vault/init", json={"path": str(other)})
    c.put("/api/vault/current", json={"path": str(vault)})
    r = c.request("DELETE", f"/api/vault/recent?path={other}")
    assert r.status_code == 200, r.text
    assert not any(e["path"] == str(other) for e in r.json()["recent"]), r.text
    assert other.is_dir() and (other / "nodes").is_dir(), "只该从列表移除，目录不许动"


@case
def 程序自己的目录一律不能当知识库():
    """2026-09-22 的第二次故障：被钉住的旧服务往 `<仓库>/.knowrary/` 写了 index.json 和
    layout.json，仓库于是"长得像库"，在目录选择器里显示成「已是库」被选中——
    可它没有 nodes/，解析器退回去扫整个仓库，把 examples/ 里的示例当成了人家的知识库。
    只判"是不是库"堵不住：它确实**变成**了库。所以按身份拒绝。"""
    from server import vaults
    c, _, _ = vault_client()
    (REPO / ".knowrary").mkdir(exist_ok=True)      # 复现：仓库里冒出 .knowrary/
    try:
        assert vaults.status(REPO) == "program", vaults.status(REPO)
        assert vaults.status(REPO / "server") == "program", "仓库里的别的目录也不行"
        # examples/ 是唯一例外：示例库放出来就是给人切过去随便改的
        assert vaults.status(REPO / "examples" / "sample-vault") == "vault", "示例库该能切过去"
        ok = c.put("/api/vault/current", json={"path": str(REPO / "examples" / "sample-vault")})
        assert ok.status_code == 200, ok.text
        assert ok.json()["current"]["path"].endswith("sample-vault"), ok.text
        r = c.put("/api/vault/current", json={"path": str(REPO)})
        assert r.status_code == 422 and "程序自己的目录" in r.json()["detail"], r.text
        r = c.post("/api/vault/init", json={"path": str(REPO)})
        assert r.status_code == 422, r.text
        os.environ["KNOWRARY_VAULT"] = str(REPO)   # 旧版 dev.sh 干的就是这个
        try:
            vaults.current_vault()
            raise AssertionError("钉在程序目录上也照单全收")
        except vaults.NoVaultSelected as exc:
            assert "dev.sh" in str(exc), str(exc)
        assert c.get("/api/health").status_code == 409
    finally:
        os.environ.pop("KNOWRARY_VAULT", None)
        try:
            (REPO / ".knowrary").rmdir()
        except OSError:
            pass


@case
def 指着一个不是库的目录要当场报错():
    """2026-09-22 的真实故障：旧版 dev.sh 把 KNOWRARY_VAULT 钉在代码仓库上，而仓库的 nodes/
    已经搬走。解析器按老规矩退回去扫整个目录，把 examples/ 里的示例节点当成了人家的知识库——
    界面上 17 个节点 26 个错误，而人只会以为"切换没生效"。与其扫出垃圾，不如直说不是库。"""
    from server import vaults
    c, _, _ = vault_client()
    notvault = tmp_dir("knowrary-notvault-")
    (notvault / "pom.xml").write_text("<project/>", "utf-8")
    os.environ["KNOWRARY_VAULT"] = str(notvault)
    try:
        try:
            vaults.current_vault()
            raise AssertionError("不是库也照单全收了")
        except vaults.NoVaultSelected as exc:
            assert "不是知识库" in str(exc), str(exc)
        r = c.get("/api/health")
        assert r.status_code == 409, (r.status_code, r.text)
        assert "KNOWRARY_VAULT" in r.json()["detail"], r.text
    finally:
        os.environ.pop("KNOWRARY_VAULT", None)


@case
def 上次选的库不见了也要说清楚():
    """库被删了 / 改名了：报"它不在了"，不是默默扫出一堆别的东西。"""
    from server import vaults
    c, _, _ = vault_client()
    gone = tmp_dir("knowrary-gone-") / "搬走了"
    gone.mkdir()
    c.post("/api/vault/init", json={"path": str(gone)})
    shutil.rmtree(gone)
    index_service.invalidate()
    try:
        vaults.current_vault()
        raise AssertionError("库都没了还认")
    except vaults.NoVaultSelected as exc:
        assert "不在了" in str(exc) or "不是知识库" in str(exc), str(exc)
    assert c.get("/api/health").status_code == 409
    assert c.get("/api/vault").status_code == 200, "自救的入口不能跟着挂"


@case
def 环境变量钉住时读回pinned():
    """自测和脚本靠 KNOWRARY_VAULT 临时覆盖；界面上得说清"在这儿切没用"。"""
    c, vault, _ = vault_client()
    os.environ["KNOWRARY_VAULT"] = str(vault)
    try:
        body = c.get("/api/vault").json()
        assert body["pinned"] is True, body
        assert body["current"]["path"] == str(vault), body
    finally:
        os.environ.pop("KNOWRARY_VAULT", None)


@case
def 目录浏览只在放行范围内并标出状态():
    """能枚举本机目录的接口：`..` 和符号链接摊平后越界的一律弹回落脚点。"""
    c, vault, _ = vault_client()
    r = c.get("/api/vault/browse", params={"path": str(vault.parent)}).json()
    names = {e["name"]: e["status"] for e in r["entries"]}
    assert names.get(vault.name) == "vault", names
    assert r["roots"] and r["roots"][0]["name"] == "家目录", r["roots"]
    home = str(Path.home().resolve())
    assert c.get("/api/vault/browse", params={"path": "/etc"}).json()["path"] != "/etc", "越界没弹回"
    escaped = c.get("/api/vault/browse", params={"path": f"{vault}/../../../../etc"}).json()["path"]
    assert escaped != "/etc" and escaped.startswith((home, str(vault.parent))), escaped


@case
def 不给路径时选择器停在当前库旁边():
    """开在家目录是不够的：库放在别的分支上时，从家目录根本走不过去。"""
    c, vault, _ = vault_client()
    assert c.get("/api/vault/browse").json()["path"] == str(vault.parent), c.get("/api/vault/browse").json()


@case
def 用户级配置读坏了不影响开机():
    """配置文件烂掉最差回到"没选过"，不能让整个程序打不开。"""
    from server import vaults
    c, vault, home = vault_client()
    (home / "config.json").write_text("{不是 JSON", "utf-8")
    assert vaults.load()["current"] is None, vaults.load()
    assert c.get("/api/vault").status_code == 200


# ---------------------------------------------------------------- 密钥跟人不跟库

def llm_scope_client() -> tuple[TestClient, Path, Path]:
    """起一个不被 KNOWRARY_VAULT 钉住的服务，并把用户级目录指到临时位置。"""
    c, vault, home = vault_client()
    os.environ.pop("KNOWRARY_LLM_CONFIG", None)
    return c, vault, home


@case
def 模型配置只有一份且在用户级():
    """一个人有好几个库（自己的、参考的、示例的）。密钥绑在库上就得切一次配一次，
    参考库和示例库里更不该出现密钥。所以**配置只有一份**：~/.knowrary/llm.local.json。"""
    c, vault, home = llm_scope_client()
    before = c.get("/api/llm/config").json()
    assert before["exists"] is False, before
    assert before["path"] == str(home / "llm.local.json"), before
    r = c.put("/api/llm/config", json={
        "providers": [{"name": "qwen", "type": "openai", "model": "qwen-max",
                       "base_url": "https://x/v1", "api_key": "sk-1"}],
        "roles": {"learn": "qwen", "review": "qwen"}})
    assert r.status_code == 200, r.text
    assert (home / "llm.local.json").exists(), "没落到用户级"
    assert not (vault / ".knowrary" / "llm.local.json").exists(), "不该在库里建一份"


@case
def 换个库也读得到同一套密钥():
    c, _, home = llm_scope_client()
    c.put("/api/llm/config", json={
        "providers": [{"name": "qwen", "type": "openai", "model": "qwen-max",
                       "base_url": "https://x/v1", "api_key": "sk-1"}],
        "roles": {"learn": "qwen", "review": "qwen"}})
    other = tmp_dir("knowrary-other-") / "另一个库"
    other.mkdir()
    c.post("/api/vault/init", json={"path": str(other)})
    got = c.get("/api/llm/config").json()
    assert [p["name"] for p in got["providers"]] == ["qwen"], got
    assert got["providers"][0]["api_key_set"] is True, got


@case
def 库里放一份配置不再生效():
    """2026-09-22 之前库级优先于用户级。现在**没有库级这回事**——
    留着那条分支就意味着参考库能悄悄带一套 provider 进来，而且切库还要重配。"""
    c, vault, home = llm_scope_client()
    core.write_json_atomic(home / "llm.local.json", {
        "providers": {"共用": {"type": "openai", "model": "m", "base_url": "https://u/v1", "api_key": "sk-u"}},
        "roles": {"learn": "共用", "review": "共用"}})
    core.write_json_atomic(vault / ".knowrary" / "llm.local.json", {
        "providers": {"库里塞的": {"type": "openai", "model": "m", "base_url": "https://v/v1", "api_key": "sk-v"}},
        "roles": {"learn": "库里塞的", "review": "库里塞的"}})
    got = c.get("/api/llm/config").json()
    assert [p["name"] for p in got["providers"]] == ["共用"], ("库里那份不该被读", got)


@case
def 改名不该把密钥弄丢():
    """密钥留空的意思是"保持原值"，而原值要按**旧名字**去找。只按新名字找的话，
    改一次名字密钥就静默消失——2026-09-22 真把一份线上密钥冲掉了。"""
    c, _, home = llm_scope_client()
    c.put("/api/llm/config", json={
        "providers": [{"name": "老名字", "type": "openai", "model": "m",
                       "base_url": "https://x/v1", "api_key": "sk-keep"}],
        "roles": {"learn": "老名字", "review": "老名字"}})
    r = c.put("/api/llm/config", json={
        "providers": [{"name": "新名字", "type": "openai", "model": "m",
                       "base_url": "https://x/v1", "api_key": "", "prev_name": "老名字"}],
        "roles": {"learn": "新名字", "review": "新名字"}})
    assert r.status_code == 200, r.text
    saved = core.load_json(home / "llm.local.json")
    assert saved["providers"]["新名字"]["api_key"] == "sk-keep", ("改个名字把密钥弄丢了", saved)
    assert "prev_name" not in saved["providers"]["新名字"], ("prev_name 不该落盘", saved)


@case
def 用户级配置的备份放在它自己旁边():
    """备份不该按"当时切着哪个库"散落到各个库的 backup/ 里。"""
    c, vault, home = llm_scope_client()
    body = {"providers": [{"name": "qwen", "type": "openai", "model": "qwen-max",
                           "base_url": "https://x/v1", "api_key": "sk-1"}],
            "roles": {"learn": "qwen", "review": "qwen"}}
    c.put("/api/llm/config", json=body)
    c.put("/api/llm/config", json=body)          # 第二次才会触发备份
    backups = list((home / "backup").glob("llm-config-*/llm.local.json"))
    assert backups, f"用户级配置没留备份：{list(home.iterdir())}"
    assert not (vault / ".knowrary" / "backup").exists(), "备份跑到库里去了"


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
