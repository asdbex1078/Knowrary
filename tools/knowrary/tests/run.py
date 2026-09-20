#!/usr/bin/env python3
"""Knowrary core 自测（零第三方依赖）：python3 tools/knowrary/tests/run.py [关键字]

每个用例在临时目录里搭一个最小 vault，跑 build_index 并断言节点/边/诊断/契约。
覆盖阶段 1 验收点：索引可重建且结果一致、非法 YAML / 未知目标 / 非法年份都有诊断、
改文件后节点和边正确更新。
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import core  # noqa: E402  （必须先注入 sys.path）

REPO = HERE.parent.parent.parent
TYPES_TABLE = REPO / "relation-types.json"
CASES: list = []
_TMPDIRS: list[tempfile.TemporaryDirectory] = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------------------------------------------------------- 夹具

def make_vault(files: dict[str, str]) -> Path:
    """按 {相对路径: 内容} 建一个临时 vault，并放入真实的关系类型表。"""
    tmp = tempfile.TemporaryDirectory(prefix="knowrary-test-")
    _TMPDIRS.append(tmp)
    vault = Path(tmp.name)
    (vault / "relation-types.json").write_text(TYPES_TABLE.read_text(encoding="utf-8"), encoding="utf-8")
    for rel, text in files.items():
        core.write(vault / rel, text)
    return vault


def node_md(name: str, *, rels: str = "", field: str = "测试", extra: str = "", body: str = "正文") -> str:
    fm = f"---\nname: {name}\nfield: {field}\ndesc: {name} 的一句话摘要\n{extra}---\n"
    rel_block = f"\n## 关系\n{rels}\n" if rels else ""
    return f"{fm}# {name}\n\n{body}\n{rel_block}"


def build(files: dict[str, str]) -> tuple[Path, core.IndexResult]:
    vault = make_vault(files)
    result = core.build_index(vault)
    problems = core.validate_index(result.data)
    assert not problems, f"索引契约不合法：{problems}"
    return vault, result


def codes(result: core.IndexResult, level: str) -> list[str]:
    items = result.diags.errors if level == "error" else result.diags.warnings
    return [d.code for d in items]


def edge_ids(result: core.IndexResult) -> list[str]:
    return [e["id"] for e in result.data["edges"]]


def node_by_id(result: core.IndexResult, nid: str) -> dict:
    for n in result.data["nodes"]:
        if n["id"] == nid:
            return n
    raise AssertionError(f"节点 {nid} 不在索引里：{[n['id'] for n in result.data['nodes']]}")


# ---------------------------------------------------------------- 用例：解析与结构

@case
def 基本解析与邻接():
    _, r = build({
        "nodes/a.md": node_md("A", rels="- 部件:: [[b]] (2018) — 说明文字"),
        "nodes/b.md": node_md("B"),
    })
    assert edge_ids(r) == ["a->b#部件"], edge_ids(r)
    edge = r.data["edges"][0]
    assert (edge["family"], edge["year"], edge["note"]) == ("结构", 2018, "说明文字"), edge
    assert edge["declared_in"] == ["nodes/a.md"], edge
    assert node_by_id(r, "a")["out"] == ["a->b#部件"] and node_by_id(r, "a")["in"] == []
    assert node_by_id(r, "b")["in"] == ["a->b#部件"] and node_by_id(r, "b")["degree"] == 1
    assert r.stats["by_field"] == {"测试": 2}, r.stats


@case
def 旧箭头语法兼容():
    _, r = build({"nodes/a.md": node_md("A", rels="- 部件 → [[b]] 旧说明"), "nodes/b.md": node_md("B")})
    assert edge_ids(r) == ["a->b#部件"], edge_ids(r)
    assert not codes(r, "error"), codes(r, "error")


@case
def 显式_id_优先于文件名():
    _, r = build({"nodes/x.md": node_md("A", extra="id: alpha\n", rels="- 部件:: [[b]]"),
                  "nodes/b.md": node_md("B")})
    assert edge_ids(r) == ["alpha->b#部件"], edge_ids(r)
    assert node_by_id(r, "alpha")["path"] == "nodes/x.md"


@case
def 只读关系区块以外不入图():
    _, r = build({"nodes/a.md": node_md("A", body="正文里写 - 部件:: [[b]] 不算关系", rels="- 依赖:: [[b]]"),
                  "nodes/b.md": node_md("B")})
    assert edge_ids(r) == ["a->b#依赖"], edge_ids(r)


@case
def 关系段之后的章节不当关系解析():
    """规范 4 允许 `## 关系` 后接 `## 参考资料` / `## 待办`，它们既不是关系也不能被吞掉。"""
    md = ("---\nname: A\nfield: 测试\ndesc: A 摘要\n---\n# A\n\n正文\n\n## 关系\n- 部件:: [[b]]\n\n"
          "## 参考资料\n- [某文档](https://example.com)\n\n## 待办\n- [ ] 核实年份\n")
    vault, r = build({"nodes/a.md": md, "nodes/b.md": node_md("B")})
    assert edge_ids(r) == ["a->b#部件"], edge_ids(r)
    assert not codes(r, "error"), codes(r, "error")
    node, _ = core.load_node(vault, vault / "nodes/a.md")
    assert node.rel_tail.startswith("## 参考资料"), repr(node.rel_tail)
    assert "## 待办" in node.rel_tail and "核实年份" in node.rel_tail
    assert "## 参考资料" not in node.body, node.body


@case
def 扫描忽略备份与非节点文件():
    vault, r = build({
        "nodes/a.md": node_md("A"),
        "README.md": "# 说明",
        ".knowrary/backup/20260910/a.md": node_md("旧A"),
        "nodes/.trash/b.md": node_md("B"),
        "nodes/empty.md": "   \n",
        "doc/设计.md": "# 文档",
    })
    assert [n["id"] for n in r.data["nodes"]] == ["a"], r.data["nodes"]
    assert "empty_file" in codes(r, "warning"), codes(r, "warning")


# ---------------------------------------------------------------- 用例：归一与去重

@case
def 互逆类型归一到正向():
    _, r = build({"nodes/a.md": node_md("A", rels="- 属于:: [[b]]"), "nodes/b.md": node_md("B")})
    assert edge_ids(r) == ["b->a#包含"], edge_ids(r)
    assert r.data["edges"][0]["raw_types"] == ["属于"]


@case
def 两侧重复维护合并为一条边():
    _, r = build({"nodes/a.md": node_md("A", rels="- 属于:: [[b]]"),
                  "nodes/b.md": node_md("B", rels="- 包含:: [[a]]")})
    assert edge_ids(r) == ["b->a#包含"], edge_ids(r)
    edge = r.data["edges"][0]
    assert sorted(edge["declared_in"]) == ["nodes/a.md", "nodes/b.md"], edge
    assert sorted(edge["raw_types"]) == ["包含", "属于"], edge
    assert codes(r, "warning").count("duplicate_relation") == 1, codes(r, "warning")


@case
def 对称关系按_id_排序定向():
    _, r = build({"nodes/b.md": node_md("B", rels="- 对比:: [[a]]"),
                  "nodes/a.md": node_md("A", rels="- 对比:: [[b]]")})
    assert edge_ids(r) == ["a->b#对比"], edge_ids(r)
    assert r.data["edges"][0]["symmetric"] is True
    assert codes(r, "warning").count("duplicate_relation") == 1


@case
def 同文件重复声明与自环():
    _, r = build({"nodes/a.md": node_md("A", rels="- 部件:: [[b]]\n- 部件:: [[b]]\n- 相关:: [[a]]"),
                  "nodes/b.md": node_md("B")})
    assert edge_ids(r) == ["a->b#部件"], edge_ids(r)
    assert "duplicate_relation" in codes(r, "warning") and "self_loop" in codes(r, "warning")


@case
def 未登记类型落默认族并警告():
    _, r = build({"nodes/a.md": node_md("A", rels="- 疑似:: [[b]]"), "nodes/b.md": node_md("B")})
    assert r.data["edges"][0]["family"] == "弱关联", r.data["edges"][0]
    assert "unknown_type" in codes(r, "warning")
    fam = {f["name"]: f for f in r.data["families"]}["弱关联"]
    assert fam["default"] is True and "疑似" in fam["types"], fam


# ---------------------------------------------------------------- 用例：stub 与诊断

@case
def 未知目标占位为虚拟_stub():
    _, r = build({"nodes/a.md": node_md("A", rels="- 依赖:: [[ghost]]")})
    assert edge_ids(r) == ["a->ghost#依赖"], edge_ids(r)
    ghost = node_by_id(r, "ghost")
    assert ghost["virtual"] is True and ghost["stub"] is True and ghost["referrers"] == ["nodes/a.md"]
    assert r.data["stubs"] == ["ghost"] and r.stats["missing_targets"] == 1
    assert codes(r, "error") == ["unknown_target"], codes(r, "error")


@case
def status_stub_计入_stub_列表():
    _, r = build({"nodes/a.md": node_md("A", extra="status: stub\n")})
    assert r.data["stubs"] == ["a"] and node_by_id(r, "a")["stub"] is True


@case
def 非法_yaml_有诊断且不丢节点():
    _, r = build({"nodes/a.md": "---\nname: A\n  bad: [1, 2\nfield: 测试\n---\n# A\n"})
    assert codes(r, "error").count("bad_yaml") == 1, codes(r, "error")
    assert node_by_id(r, "a")["id"] == "a"


@case
def 非法年份与区间反转():
    _, r = build({"nodes/a.md": node_md("A", extra="year: 20177\n"),
                  "nodes/b.md": node_md("B", extra="start_year: 2020\nend_year: 2010\n"),
                  "nodes/c.md": node_md("C", extra="year: 2017\n")})
    assert codes(r, "error").count("bad_year") == 1, codes(r, "error")
    assert codes(r, "error").count("year_range_inverted") == 1, codes(r, "error")
    assert r.stats["with_year"] == 2, r.stats  # 20177 仍保留原值，只是报错


@case
def 缺字段_非法status_布局字段_非法关系行():
    _, r = build({"nodes/a.md": "---\nname: A\n---\n# A\n\n## 关系\n- 这行不合法\n",
                  "nodes/b.md": node_md("B", extra="status: 存疑\nx: 100\n")})
    got = sorted(set(codes(r, "error")))
    assert got == ["bad_relation_line", "bad_status", "layout_in_frontmatter", "missing_field"], got


@case
def 重复_id_报错():
    _, r = build({"nodes/a.md": node_md("A", extra="id: same\n"),
                  "nodes/b.md": node_md("B", extra="id: same\n")})
    assert "duplicate_id" in codes(r, "error"), codes(r, "error")


@case
def 演化无年份与正文死链警告():
    _, r = build({"nodes/a.md": node_md("A", rels="- 演化为:: [[b]]", body="正文引用 [[nowhere]]"),
                  "nodes/b.md": node_md("B")})
    warns = codes(r, "warning")
    assert "evolution_without_year" in warns and "dead_body_link" in warns, warns


@case
def 演化边有年份则不警告():
    _, r = build({"nodes/a.md": node_md("A", rels="- 演化为:: [[b]] (2018)"), "nodes/b.md": node_md("B")})
    assert "evolution_without_year" not in codes(r, "warning")


# ---------------------------------------------------------------- 用例：revision 与幂等

@case
def pagerank_权重与骨干一致():
    """pageRank 按无向图算：连得多、且连的对象也重要的节点权重最高。"""
    rels = "\n".join(f"- 部件:: [[叶{i}]]" for i in range(6))
    files = {"nodes/hub.md": node_md("枢纽", rels=rels + "\n- 依赖:: [[次枢纽]]"),
             "nodes/sub.md": node_md("次枢纽", extra="id: 次枢纽\n", rels="- 部件:: [[叶0]]")}
    for i in range(6):
        files[f"nodes/leaf{i}.md"] = node_md(f"叶{i}", extra=f"id: 叶{i}\n")
    _, r = build(files)
    ranks = {n["id"]: n["rank"] for n in r.data["nodes"]}
    assert abs(sum(ranks.values()) - 1) < 1e-4, sum(ranks.values())   # index 里 rank 存的是 6 位小数
    assert max(ranks, key=ranks.get) == "hub", ranks
    weights = {n["id"]: n["weight"] for n in r.data["nodes"]}
    # 叶0 同时连 hub 与次枢纽（对称），权重相等是对的；拿只连一条边的叶1 比才有意义
    assert weights["hub"] == 1.0 and weights["叶1"] < weights["次枢纽"], weights


@case
def 同族短环被报为方向矛盾():
    _, r = build({"nodes/a.md": node_md("A", rels="- 包含:: [[b]]"),
                  "nodes/b.md": node_md("B", rels="- 包含:: [[a]]")})
    cycles = [d for d in r.diags.warnings if d.code == "relation_cycle"]
    assert len(cycles) == 1, [d.message for d in r.diags.warnings]
    assert "结构族存在环" in cycles[0].message and r.stats["cycles"] == 1, cycles[0].message


@case
def 跨族的环不报():
    """A 包含 B、B 依赖 A 是两种不同关系，不算方向矛盾。"""
    _, r = build({"nodes/a.md": node_md("A", rels="- 包含:: [[b]]"),
                  "nodes/b.md": node_md("B", rels="- 依赖:: [[a]]")})
    assert not [d for d in r.diags.warnings if d.code == "relation_cycle"], [d.message for d in r.diags.warnings]


@case
def 重复生成结果逐字节一致():
    vault, first = build({"nodes/a.md": node_md("A", rels="- 部件:: [[b]]"), "nodes/b.md": node_md("B")})
    out = core.index_path(vault)
    core.write_json_atomic(out, first.data)
    second = core.build_index(vault, core.load_previous(out))
    assert second.changed is False, "内容未变却报 changed"
    assert second.data["revision"] == first.data["revision"] == 1
    assert second.data["generated_at"] == first.data["generated_at"]
    assert json.dumps(second.data, sort_keys=True) == json.dumps(first.data, sort_keys=True)


@case
def 改文件后节点与边更新且_revision_递增():
    vault, first = build({"nodes/a.md": node_md("A", rels="- 部件:: [[b]]"), "nodes/b.md": node_md("B")})
    out = core.index_path(vault)
    core.write_json_atomic(out, first.data)
    core.write(vault / "nodes/a.md", node_md("A", rels="- 依赖:: [[c]]"))
    core.write(vault / "nodes/c.md", node_md("C"))
    (vault / "nodes/b.md").unlink()
    second = core.build_index(vault, core.load_previous(out))
    assert second.changed is True and second.data["revision"] == 2, second.data["revision"]
    assert edge_ids(second) == ["a->c#依赖"], edge_ids(second)
    assert [n["id"] for n in second.data["nodes"]] == ["a", "c"], second.data["nodes"]
    assert not core.validate_index(second.data)


@case
def 索引损坏时退回全量重建():
    vault, first = build({"nodes/a.md": node_md("A")})
    out = core.index_path(vault)
    core.write(out, "{ 半个 JSON")
    assert core.load_previous(out) is None
    again = core.build_index(vault, core.load_previous(out))
    assert again.data["revision"] == 1 and again.data["content_hash"] == first.data["content_hash"]


@case
def 原子写后可回读且契约合法():
    vault, r = build({"nodes/a.md": node_md("A", rels="- 部件:: [[b]]"), "nodes/b.md": node_md("B")})
    out = core.index_path(vault)
    core.write_json_atomic(out, r.data)
    assert not list(out.parent.glob("index.json.*.tmp")), "临时文件没清掉"
    assert not core.validate_index(core.load_json(out))


@case
def 契约校验能抓出被改坏的索引():
    _, r = build({"nodes/a.md": node_md("A", rels="- 部件:: [[b]]"), "nodes/b.md": node_md("B")})
    broken = json.loads(json.dumps(r.data))
    broken["nodes"] = [n for n in broken["nodes"] if n["id"] != "b"]
    assert core.validate_index(broken), "删掉边端点却没报错"
    broken2 = json.loads(json.dumps(r.data))
    broken2["edges"][0]["type"] = "依赖"
    assert core.validate_index(broken2), "改坏 edge id 却没报错"


# ---------------------------------------------------------------- 用例：真实 vault

# ---------------------------------------------------------------- 用例：Markdown 写回

RICH_MD = """---
name: 甲
field: 测试
desc: 甲的摘要
tags:
  - 标签A
---
# 甲

正文第一段，包含 `行内代码` 和 **加粗**。

```python
# 代码块必须逐字保留
def f(x):
    return x * 2
```

- 列表项 1
- 列表项 2

## 关系
- 部件:: [[乙]]
- 依赖:: [[丙]] (2020) — 说明文字

## 参考资料
- [某文档](https://example.com)

## 待办
- [ ] 核实年份
"""


def rich_vault():
    return build({"nodes/a.md": RICH_MD,
                  "nodes/b.md": node_md("乙", extra="id: 乙\n"),
                  "nodes/c.md": node_md("丙", extra="id: 丙\n")})


@case
def 写回只动关系段_其余逐字保留():
    vault, r = rich_vault()
    edits = core.plan(vault, [{"type": "add_edge", "source": "a", "relation": "相关", "target": "丙"}], r.data)
    after = edits[0].after
    for keep in ("# 代码块必须逐字保留", "def f(x):", "- 列表项 2", "## 参考资料", "- [ ] 核实年份",
                 "`行内代码`", "**加粗**", "tags:", "  - 标签A"):
        assert keep in after, f"丢了原文片段：{keep}"
    assert "- 相关:: [[丙]]" in after, after
    assert after.count("## 关系") == 1
    # 关系段之外的字节完全一致
    cut = lambda t: (t.split("## 关系")[0], t.split("## 参考资料")[1])
    assert cut(after) == cut(RICH_MD), "关系段以外被改动了"


@case
def 增删改关系():
    vault, r = rich_vault()
    changes = [
        {"type": "add_edge", "source": "a", "relation": "对比", "target": "丙", "note": "新增说明"},
        {"type": "remove_edge", "source": "a", "relation": "部件", "target": "乙"},
        {"type": "update_edge", "source": "a", "target": "丙", "from_relation": "依赖",
         "relation": "基于", "year": 2021},
    ]
    edits = core.plan(vault, changes, r.data)
    after = edits[0].after
    assert "- 对比:: [[丙]] — 新增说明" in after, after
    assert "部件:: [[乙]]" not in after, after
    assert "- 基于:: [[丙]] (2021) — 说明文字" in after, after
    assert len(edits[0].notes) == 3, edits[0].notes


@case
def frontmatter_白名单():
    vault, r = rich_vault()
    edits = core.plan(vault, [{"type": "update_frontmatter", "source": "a",
                               "fields": {"desc": "换个摘要", "year": 2018}}], r.data)
    assert "desc: 换个摘要" in edits[0].after and "year: 2018" in edits[0].after
    for bad in ({"x": 10}, {"id": "别的"}, {"unknown": 1}, {"status": "乱写"}):
        try:
            core.plan(vault, [{"type": "update_frontmatter", "source": "a", "fields": bad}], r.data)
            raise AssertionError(f"{bad} 应该被拒绝")
        except core.ChangeRejected:
            pass


@case
def 预览不写盘_确认才写并备份():
    vault, r = rich_vault()
    before = (vault / "nodes/a.md").read_text("utf-8")
    edits = core.plan(vault, [{"type": "add_edge", "source": "a", "relation": "相关", "target": "丙"}], r.data)
    assert (vault / "nodes/a.md").read_text("utf-8") == before, "预览阶段就写盘了"
    snapshot = core.commit(vault, edits)
    assert (vault / "nodes/a.md").read_text("utf-8") != before
    assert "- 相关:: [[丙]]" in (vault / "nodes/a.md").read_text("utf-8")
    assert (vault / snapshot / "nodes/a.md").read_text("utf-8") == before, "备份内容不是改动前的原文"


@case
def 外部改过的文件拒绝覆盖():
    vault, r = rich_vault()
    (vault / "nodes/a.md").write_text(RICH_MD + "\n外部追加的一行\n", encoding="utf-8")
    try:
        core.plan(vault, [{"type": "add_edge", "source": "a", "relation": "相关", "target": "丙"}], r.data)
        raise AssertionError("外部改过还允许写回")
    except core.WriteConflict as exc:
        assert "被改过" in str(exc), str(exc)


@case
def 写回后仍然可解析且索引更新():
    vault, r = rich_vault()
    edits = core.plan(vault, [{"type": "add_edge", "source": "a", "relation": "相关", "target": "丙"}], r.data)
    core.commit(vault, edits)
    again = core.build_index(vault)
    assert not core.validate_index(again.data), core.validate_index(again.data)
    assert any(e["id"] == "a->丙#相关" for e in again.data["edges"]), [e["id"] for e in again.data["edges"]]
    assert again.data["stats"]["errors"] == 0, again.data["errors"]


# ---------------------------------------------------------------- 阶段 4：复习 / 放置 / Digest

import datetime as _dt  # noqa: E402  （只有阶段 4 用例需要）


def placed_vault() -> tuple[Path, dict, dict]:
    """三个已上画布的节点 + 一个刚写好还没上画布的 d。"""
    vault, r = build({
        "nodes/组A/a.md": node_md("A", rels="- 部件:: [[b]]", extra="learned: 2026-09-01\n"),
        "nodes/组A/b.md": node_md("B"),
        "nodes/组B/c.md": node_md("C"),
    })
    layout = core.build_initial_layout(r.data)
    core.write(vault / "nodes/组A/d.md", node_md("D", rels="- 部件:: [[a]]"))
    return vault, core.build_index(vault).data, layout


@case
def 复习间隔按次数推进():
    assert core.next_due_for(None, "2026-09-01") == "2026-09-02"          # learned + 1 天
    entry = {"reviews": ["2026-09-10"]}
    assert core.next_due_for(entry, None) == "2026-09-11"                 # 第 1 次后隔 1 天
    entry["reviews"].append("2026-09-11")
    assert core.next_due_for(entry, None) == "2026-09-13"                 # 第 2 次后隔 2 天
    entry["reviews"] += ["2026-09-13", "2026-09-17", "2026-09-24", "2026-10-09", "2026-11-08"]
    assert core.next_due_for(entry, None) == "2026-12-08"                 # 到顶后固定 30 天
    assert core.next_due_for({"reviews": []}, None) is None               # 既没复习过也没 learned
    assert core.next_due_for(None, "不是日期") is None


@case
def 到期列表按日期排序且跳过stub():
    vault, index, _ = placed_vault()
    log = {"nodes": {}}
    due = core.due_nodes(index, log, _dt.date(2026, 9, 12))
    assert [d["id"] for d in due] == ["a"], due                            # 只有 a 填了 learned
    assert due[0]["overdue_days"] == 10 and due[0]["reviews"] == 0, due[0]
    core.record_review(vault, "a", _dt.date(2026, 9, 12))
    assert core.due_nodes(index, core.load_log(vault), _dt.date(2026, 9, 12)) == []
    assert core.load_log(vault)["nodes"]["a"]["next_due"] == "2026-09-13"


@case
def 三档反馈推进或回退间隔序号():
    vault, index, _ = placed_vault()
    core.record_review(vault, "a", _dt.date(2026, 9, 12), grade="记得")
    assert core.load_log(vault)["nodes"]["a"]["step"] == 1
    core.record_review(vault, "a", _dt.date(2026, 9, 13), grade="记得")
    e = core.load_log(vault)["nodes"]["a"]
    assert e["step"] == 2 and e["next_due"] == "2026-09-15", e            # 序号 2 → 隔 2 天

    core.record_review(vault, "a", _dt.date(2026, 9, 15), grade="模糊")
    e = core.load_log(vault)["nodes"]["a"]
    assert e["step"] == 2 and e["next_due"] == "2026-09-17", e            # 模糊：序号不动

    core.record_review(vault, "a", _dt.date(2026, 9, 17), grade="忘了")
    e = core.load_log(vault)["nodes"]["a"]
    assert e["step"] == 0 and e["lapses"] == 1, e                          # 忘了：序号归 0
    assert e["next_due"] == "2026-09-18", e                                # 明天再考一次
    assert [r["grade"] for r in e["reviews"]] == ["记得", "记得", "模糊", "忘了"], e

    try:
        core.record_review(vault, "a", grade="半懂")
    except ValueError:
        pass
    else:
        raise AssertionError("非法档位必须被拒绝")


@case
def 旧版复习记录能读成三档格式():
    vault, index, _ = placed_vault()
    # v1：reviews 是日期字符串数组，没有 step / lapses
    core.write_json_atomic(vault / ".knowrary/review-log.json", {
        "schema_version": 1, "updated_at": None,
        "nodes": {"a": {"reviews": ["2026-09-10", "2026-09-11"], "next_due": "2026-09-13"}}})
    e = core.load_log(vault)["nodes"]["a"]
    assert e["step"] == 2 and e["lapses"] == 0, e                          # 旧记录等价于每次都"记得"
    assert [r["grade"] for r in e["reviews"]] == ["记得", "记得"], e
    assert core.next_due_for(e, None) == "2026-09-13", e                   # 到期日与旧算法一致

    # 读操作不许改用户的文件
    raw = (vault / ".knowrary/review-log.json").read_text(encoding="utf-8")
    core.load_log(vault)
    assert (vault / ".knowrary/review-log.json").read_text(encoding="utf-8") == raw


@case
def 错题本按考点聚合答错次数():
    vault, index, _ = placed_vault()
    kept = core.append_answers(vault, [
        {"points": ["a"], "type": "回忆题", "stem": "A 是什么", "answer": "...", "grade": "忘了"},
        {"points": ["a", "b"], "type": "关系题", "stem": "A 和 B", "answer": "...", "grade": "模糊"},
        {"points": ["b"], "type": "回忆题", "stem": "B 是什么", "answer": "...", "grade": "记得"},
        {"points": ["c"], "grade": "不认识"},        # 非法档位：丢弃
        {"points": [], "grade": "忘了"},             # 没有考点：丢弃
    ])
    assert len(kept) == 3, kept
    rows = core.wrong_nodes(core.load_quiz_log(vault))
    assert [r["id"] for r in rows] == ["a", "b"], rows                     # 全对的不进错题本
    assert rows[0]["wrong"] == 1 and rows[0]["fuzzy"] == 1 and rows[0]["attempts"] == 2, rows[0]
    assert rows[1]["wrong"] == 0 and rows[1]["attempts"] == 2, rows[1]


@case
def 答题记录不碰md():
    vault, index, _ = placed_vault()
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    core.append_answers(vault, [{"points": ["a"], "stem": "x", "answer": "y", "grade": "忘了"}])
    core.record_review(vault, "a", grade="忘了")
    after = {p: p.read_bytes() for p in vault.rglob("*.md")}
    assert before == after, "测验与复习不许改动任何 Markdown"


@case
def 掌握度五档全部算得出来():
    today = _dt.date(2026, 9, 15)
    m = core.mastery_of
    assert m(None, None, today) == "未建"                          # 计划里的点，图里还没有
    assert m({"id": "x", "virtual": True}, None, today) == "未建"   # 只被引用过的虚拟 stub
    assert m({"id": "x", "stub": True}, None, today) == "只有壳"
    # 有正文、没复习记录：learned 次日到期 → 待复习
    assert m({"id": "x", "learned": "2026-09-01"}, None, today) == "待复习"
    fresh = {"reviews": [{"date": "2026-09-15", "grade": "记得"}], "step": 1}
    assert m({"id": "x"}, fresh, today) == "学过"                   # 刚复习过，间隔还短
    deep = {"reviews": [{"date": "2026-09-15", "grade": "记得"}], "step": 5}
    assert m({"id": "x"}, deep, today) == "已掌握"
    # 已掌握但到期了，仍然先报"待复习"——它是当下要动手的那一档
    due = {"reviews": [{"date": "2026-08-01", "grade": "记得"}], "step": 5}
    assert m({"id": "x"}, due, today) == "待复习"


@case
def 清单进度按点汇总且不落盘():
    stages = [{"points": [{"id": "a"}, {"id": "没建的"}]},
              {"points": [{"id": "a"}, {"id": "也没建"}]}]            # a 跨阶段重复出现
    index = {"nodes": [{"id": "a", "learned": "2026-09-14"}]}
    log = {"nodes": {"a": {"reviews": [{"date": "2026-09-15", "grade": "记得"}], "step": 1}}}
    got = core.progress_of(stages, index, log, _dt.date(2026, 9, 15))
    assert got["total"] == 3 and got["built"] == 1, got               # 去重后 3 个点，建好 1 个
    assert got["counts"]["未建"] == 2, got["counts"]
    assert got["points"]["a"] == "学过", got["points"]
    doc = {"projects": {"p": {"lists": [{"stages": stages}]}}}
    assert core.point_ids(doc) == ["a", "没建的", "也没建"]


@case
def 演化边年份倒挂会被算出来():
    """**唯一不依赖外部知识的年份矫正**：不问"1997 对不对"，只问"这条线自己自洽吗"。
    口述一句"year 填 2017"没人能核，但"它比它的前身还早"是能算的。"""
    vault = make_vault({
        "nodes/a.md": node_md("A", extra="year: 1997\n", rels="- 演化为:: [[b]]"),
        "nodes/b.md": node_md("B", extra="year: 1986\n"),      # 比前身还早 → 一定有一个错了
        "nodes/c.md": node_md("C", extra="year: 2099\n"),      # 未来
    })
    r = core.build_index(vault, None)
    codes = {w["code"] for w in r.data["warnings"]}
    assert "year_inverted" in codes, [w["message"] for w in r.data["warnings"]]
    assert "year_in_future" in codes, codes
    msg = next(w["message"] for w in r.data["warnings"] if w["code"] == "year_inverted")
    assert "1997" in msg and "1986" in msg and "倒挂" in msg, msg
    assert not r.diags.errors, [d.render() for d in r.diags.errors]   # 只警告，不拦着你先记下来

    # 顺序对的不报
    ok = make_vault({"nodes/a.md": node_md("A", extra="year: 1986\n", rels="- 演化为:: [[b]]"),
                     "nodes/b.md": node_md("B", extra="year: 1997\n")})
    assert not [w for w in core.build_index(ok, None).data["warnings"]
                if w["code"] == "year_inverted"]

    # 欠账清单里能看见（不是只躺在 index 的 warnings 里）
    d = core.build_digest(vault, r.data, core.empty_layout())
    assert d["counts"]["bad_years"] == 2, d["counts"]


@case
def 建节点时带的正文要整篇落盘_没给才落最小骨架():
    """对话教练按骨架写足了 body，写盘却只认 desc、正文整篇丢掉——
    节点建出来只剩一句话，笔记层等于没有（阈值逻辑单元那次就是这样）。"""
    vault = make_vault({"nodes/a.md": node_md("A")})
    r = core.build_index(vault, None)
    body = ("1943 年由神经生理学家 McCulloch 和数学家 Pitts 提出。\n\n"
            "## 怎么运作\n输入加权求和，过阈值输出 1。\n\n## 线头\n- 神经生理学\n- 数学")
    fields = {"name": "TLU", "field": "AI", "desc": "最简人工神经元"}
    edits = core.plan(vault, [{"type": "create_node", "source": "TLU", "path": "nodes/TLU.md",
                               "fields": fields, "body": body}], r.data)
    text = edits[0].after
    assert "数学家 Pitts" in text and "## 线头" in text, text
    assert text.count("# TLU") == 1 and "## 描述" not in text, text
    assert text.rstrip().endswith("## 关系"), text
    assert any("正文" in n for n in edits[0].notes), edits[0].notes
    # 模型自带 H1 时不重复加标题
    edits = core.plan(vault, [{"type": "create_node", "source": "T2", "path": "nodes/T2.md",
                               "fields": {**fields, "name": "T2"}, "body": "# T2\n\n正文"}], r.data)
    assert edits[0].after.count("# T2") == 1, edits[0].after
    # 没给 body（画布右键建空节点）仍然落「## 描述 + desc」
    edits = core.plan(vault, [{"type": "create_node", "source": "T3", "path": "nodes/T3.md",
                               "fields": {**fields, "name": "T3"}}], r.data)
    assert "## 描述\n最简人工神经元" in edits[0].after, edits[0].after
    # 正文里自带 `## 关系` 要拒：关系段由解析器独占
    try:
        core.plan(vault, [{"type": "create_node", "source": "T4", "path": "nodes/T4.md",
                           "fields": {**fields, "name": "T4"}, "body": "x\n\n## 关系\n- 对比:: [[a]]"}], r.data)
    except core.ChangeRejected as exc:
        assert "关系" in str(exc), exc
    else:
        raise AssertionError("正文里夹带 ## 关系 也放过去了")


@case
def 建节点时能写layer_写错则拒():
    """`layer` 加了字段、加了校验、加了泳道，却忘了开写回白名单——
    表现是对话里建出来的节点没有 layer，模型只能在正文里留一句"待补"。"""
    vault = make_vault({"nodes/a.md": node_md("A")})
    r = core.build_index(vault, None)
    edits = core.plan(vault, [{"type": "create_node", "source": "NPU", "path": "nodes/NPU.md",
                               "fields": {"name": "NPU", "field": "计算机系统", "layer": "硬件",
                                          "year": 2017, "desc": "专用推理电路"}}], r.data)
    assert "layer: 硬件" in edits[0].after, edits[0].after
    try:
        core.plan(vault, [{"type": "create_node", "source": "X", "path": "nodes/X.md",
                           "fields": {"name": "X", "field": "f", "layer": "硬體", "desc": "d"}}], r.data)
    except core.ChangeRejected as exc:
        assert "硬體" in str(exc), exc
    else:
        raise AssertionError("拼错的 layer 也放过去了")


@case
def 改layer时也校验_和新建同一把尺子():
    """新建时校验了、改的时候不校验，等于没校验：propose_changes 走的就是 update_frontmatter。

    写进一个不存在的层名不会报错，只会让这个节点从历史视图上**悄悄消失**——
    泳道是按 LAYERS 建的，对不上的那个没有泳道可去。
    """
    vault = make_vault({"nodes/a.md": node_md("A", extra="layer: 理论\n")})
    r = core.build_index(vault, None)
    edits = core.plan(vault, [{"type": "update_frontmatter", "source": "a",
                               "fields": {"layer": "系统软件"}}], r.data)
    assert "layer: 系统软件" in edits[0].after, edits[0].after
    # 清空是合法的：layer 本来就是可选字段
    edits = core.plan(vault, [{"type": "update_frontmatter", "source": "a",
                               "fields": {"layer": ""}}], r.data)
    assert "layer: 理论" not in edits[0].after, edits[0].after
    try:
        core.plan(vault, [{"type": "update_frontmatter", "source": "a",
                           "fields": {"layer": "系統軟件"}}], r.data)
    except core.ChangeRejected as exc:
        assert "系統軟件" in str(exc), exc
    else:
        raise AssertionError("改的时候放过了新建会拒的层名")


@case
def 按抽象层铺布局_二级分组是层且次序固定():
    vault = make_vault({
        "nodes/x/a.md": node_md("A", extra="layer: AI应用\n"),
        "nodes/y/b.md": node_md("B", extra="layer: 理论\n"),
        "nodes/y/c.md": node_md("C", extra="layer: 硬件\n"),
        "nodes/y/d.md": node_md("D"),                       # 没填 → 未分层
    })
    index = core.build_index(vault, None).data
    doc = core.build_initial_layout(index, by="layer")
    subs = [g["name"] for g in doc["groups"].values() if g.get("parent")]
    assert subs == ["理论", "硬件", "AI应用", "未分层"], subs      # 底层在前，不是字典序
    assert doc["nodes"]["b"]["group"].endswith("理论"), doc["nodes"]["b"]
    # 默认那套（按子目录）没被动过
    old = core.build_initial_layout(index)
    assert sorted(g["name"] for g in old["groups"].values() if g.get("parent")) == ["x", "y"]


@case
def 抽象层是可选字段_写错只警告不报错():
    """`layer` 是历史视图分泳道用的**正交维度**：结构图按主题分组，历史图按层分泳道。
    可选，所以没填不算错；但拼错必须看得见，否则会静静多出一条泳道。"""
    vault = make_vault({
        "nodes/a.md": node_md("A", extra="year: 1936\nlayer: 理论\n"),
        "nodes/b.md": node_md("B", extra="year: 1971\n"),                 # 没填，合法
        "nodes/c.md": node_md("C", extra="year: 1999\nlayer: 硬體\n"),    # 拼错
    })
    r = core.build_index(vault, None)
    by_id = {n["id"]: n for n in r.data["nodes"]}
    assert by_id["a"]["layer"] == "理论", by_id["a"]        # 进了索引，前端才看得见
    assert "layer" not in by_id["b"] or by_id["b"].get("layer") is None
    assert not r.diags.errors, [d.render() for d in r.diags.errors]
    warns = [d.render() for d in r.diags.warnings if "layer" in d.render()]
    assert warns and "硬體" in warns[0], warns
    assert "理论" in core.LAYERS and "AI应用" in core.LAYERS and len(core.LAYERS) == 7


@case
def 日历全部派生且热力不算模型调用():
    """五期：日历不新增任何记录，四份现有数据拼出来。"""
    vault = make_vault({"nodes/a.md": node_md("A", extra="learned: 2026-09-15\n"),
                        "nodes/b.md": node_md("B", extra="learned: 2026-09-15\n"),
                        "nodes/c.md": node_md("C", extra="learned: 2026-08-01\n")})
    (vault / ".knowrary").mkdir(exist_ok=True)
    (vault / ".knowrary" / "review-log.json").write_text(json.dumps({"nodes": {
        "a": {"reviews": [{"date": "2026-09-15", "grade": "记得"},
                          {"date": "2026-09-16", "grade": "忘了"}], "step": 1, "lapses": 1}}}), "utf-8")
    (vault / ".knowrary" / "quiz-log.json").write_text(json.dumps({"answers": [
        {"ts": "2026-09-16T03:00:00Z", "points": ["a"], "grade": "忘了", "stem": "A 是什么"}]}), "utf-8")
    (vault / ".knowrary" / "llm-usage.json").write_text(json.dumps({"recent": [], "by_day": {
        "2026-09-14": {"calls": 9, "cost_usd": 1.5}}}), "utf-8")

    index = core.build_index(vault, None).data
    cal = core.build_calendar(vault, index, _dt.date(2026, 9, 1), _dt.date(2026, 9, 16))
    assert cal["days"]["2026-09-15"]["built"] == 2, cal["days"]["2026-09-15"]
    assert "2026-08-01" not in cal["days"], "区间外的也算进来了"
    assert cal["days"]["2026-09-16"]["reviews"] == 1 and cal["days"]["2026-09-16"]["lapses"] == 1
    assert cal["days"]["2026-09-16"]["answers"] == 1 and cal["days"]["2026-09-16"]["wrong"] == 1
    assert cal["days"]["2026-09-14"]["cost_usd"] == 1.5 and cal["days"]["2026-09-14"]["calls"] == 9

    # 只调了模型、没学东西的那天**不算"有动静"**：那是花销不是学习量
    assert cal["totals"]["active_days"] == 2, cal["totals"]
    assert cal["busiest"] == "2026-09-15", cal["busiest"]
    # 明细能点开看
    assert {n["id"] for n in cal["detail"]["2026-09-15"]["built"]} == {"a", "b"}
    assert cal["detail"]["2026-09-16"]["reviews"][0]["grade"] == "忘了"


@case
def 日历的连续天数不把今天算成断档():
    days = {"2026-09-14": {"built": 1, "reviews": 0, "answers": 0},
            "2026-09-15": {"built": 0, "reviews": 2, "answers": 0}}
    # 今天还没动手 ≠ 断了——早上八点打开就显示"断了"太蠢
    assert core.streak(days, _dt.date(2026, 9, 16)) == 2
    days["2026-09-16"] = {"built": 1, "reviews": 0, "answers": 0}
    assert core.streak(days, _dt.date(2026, 9, 16)) == 3
    assert core.streak({}, _dt.date(2026, 9, 16)) == 0


@case
def 学考双态_错题本压过自评():
    """两个数据源可以合法地互相矛盾：模型判我答漏了，我自评点了「记得」。
    这时显示绿就把错题盖住了，而错题本的全部价值恰恰在这里。"""
    today = _dt.date(2026, 9, 16)
    node = {"id": "a", "learned": "2026-09-01"}
    remembered = {"reviews": [{"date": "2026-09-15", "grade": "记得"}], "step": 3}
    assert core.exam_state(node, remembered, False, today) == "绿"
    assert core.exam_state(node, remembered, True, today) == "红", "错题本没压过自评"
    # 各档
    assert core.exam_state(node, None, False, today) == "灰"            # 没考过
    assert core.exam_state(None, None, False, today) == "灰"            # 还没建出来，谈不上考
    assert core.exam_state(node, {"reviews": [{"date": "2026-09-15", "grade": "忘了"}]},
                           False, today) == "红"
    assert core.exam_state(node, {"reviews": [{"date": "2026-09-15", "grade": "模糊"}]},
                           False, today) == "黄"
    # 记得但已经到期 → 黄（该再考了），不是绿
    stale = {"reviews": [{"date": "2026-08-01", "grade": "记得"}], "step": 1}
    assert core.exam_state(node, stale, False, today) == "黄"


@case
def 学考双态_学只看建没建():
    assert core.study_state(None) == "灰"                               # 图里没有
    assert core.study_state({"id": "a", "virtual": True}) == "灰"        # 只是被引用的占位
    assert core.study_state({"id": "a", "stub": True}) == "红"           # 只有壳
    assert core.study_state({"id": "a"}) == "绿"
    # 进度里带着双态，和五档掌握度是同一份数据的两种编码
    index = {"nodes": [{"id": "a", "learned": "2026-09-01"}]}
    got = core.progress_of([{"points": [{"id": "a"}, {"id": "没建的"}]}], index,
                           {"nodes": {}}, _dt.date(2026, 9, 16), wrong={"a"})
    assert got["states"]["a"] == {"study": "绿", "exam": "红"}, got["states"]
    assert got["states"]["没建的"] == {"study": "灰", "exam": "灰"}, got["states"]


@case
def 项目里多份清单的进度各算各的且合并时去重():
    """一个项目可以同时有学习主线和面试清单，两者重叠是常态——合并时同一个点只能算一次。"""
    project = {"lists": [
        {"kind": "学习", "stages": [{"points": [{"id": "a"}, {"id": "b"}]}]},
        {"kind": "面试", "stages": [{"points": [{"id": "a"}, {"id": "c"}]}]}]}
    index = {"nodes": [{"id": "a", "learned": "2026-09-14"}]}
    log = {"nodes": {}}
    got = core.progress_of_project(project, index, log, _dt.date(2026, 9, 15))
    assert [p["total"] for p in got["lists"]] == [2, 2], got["lists"]
    assert got["all"]["total"] == 3, got["all"]                       # a 只算一次，不是 4
    assert got["all"]["built"] == 1, got["all"]


@case
def 项目是视角不是容器_重叠自动成立():
    """同一个点属于两个项目时，掌握度是同一个——复习调度全局唯一，项目只是过滤器。"""
    index = {"nodes": [{"id": "Transformer", "learned": "2026-09-01"}]}
    log = {"nodes": {"Transformer": {"reviews": [{"date": "2026-09-14", "grade": "记得"}], "step": 5}}}
    doc = {"projects": {
        "transformer": {"lists": [{"stages": [{"points": [{"id": "Transformer"}]}]}]},
        "nlp": {"lists": [{"stages": [{"points": [{"id": "Transformer"}, {"id": "分词"}]}]}]}}}
    prog = core.all_progress(doc, index, log, _dt.date(2026, 9, 15))
    a = prog["transformer"]["all"]["points"]["Transformer"]
    b = prog["nlp"]["all"]["points"]["Transformer"]
    assert a == b == "已掌握", (a, b)                                  # 不需要任何父子字段
    assert prog["nlp"]["all"]["total"] == 2 and prog["transformer"]["all"]["total"] == 1


@case
def v1的plans读成v2的projects且读不写盘():
    vault = make_vault({"nodes/a.md": node_md("A")})
    (vault / ".knowrary").mkdir(exist_ok=True)
    legacy = vault / ".knowrary" / "plans.json"
    legacy.write_text(json.dumps({"schema_version": 1, "revision": 7, "plans": {"新计划2": {
        "name": "Transformer", "kind": "面试", "goal": "JD", "coach": "大模型", "field": "AI",
        "weekly_hours": 10, "daily_quota": 3, "target_date": "2026-12-01",
        "stages": [{"name": "一", "points": [{"id": "a", "load": "重"}]}]}}}, ensure_ascii=False),
        encoding="utf-8")
    doc = core.load_projects(vault)
    assert list(doc["projects"]) == ["transformer"], list(doc["projects"])   # id 压成 ASCII，取自显示名
    pr = doc["projects"]["transformer"]
    assert pr["legacy_id"] == "新计划2" and pr["weekly_hours"] == 10 and pr["daily_quota"] == 3
    assert len(pr["lists"]) == 1 and pr["lists"][0]["kind"] == "面试"        # kind 降到清单一层
    assert pr["lists"][0]["target_date"] == "2026-12-01"                     # 截止日跟着清单走
    assert pr["lists"][0]["stages"][0]["points"][0]["load"] == "重"
    assert doc["revision"] == 7 and not core.projects_path(vault).exists(), "读了一下就写盘了"


@case
def llm配置里的注释键不算条目():
    """模板 llm.example.json 的 roles 段里就带着 `_说明`，照抄它必须能用。"""
    import llm_backend
    repo_example = REPO / ".knowrary" / "llm.example.json"
    cfg = json.loads(repo_example.read_text(encoding="utf-8"))
    llm_backend.validate_config(cfg, repo_example)          # 以前这里会抛「角色 _说明 指向不存在的 provider」
    name, provider = llm_backend.resolve_provider(cfg, "learn")
    assert name == "claude-cli" and provider["type"] == "claude-cli", (name, provider)
    assert "_说明" not in llm_backend.describe(cfg, repo_example)
    # 注释键不能顶替真条目：providers 里只剩注释时仍然要报错
    try:
        llm_backend.validate_config({"providers": {"_说明": "空的"}}, repo_example)
    except llm_backend.LLMConfigError:
        pass
    else:
        raise AssertionError("providers 里只有注释也放过了")


@case
def 用量按本地日期分桶():
    """record 按 UTC 分桶、summary 按本地日期读，东八区每天前 8 小时「今天」就永远是 0。"""
    vault = make_vault({"nodes/a.md": node_md("A")})
    log = core.record_usage(vault, {"op": "quiz", "role": "review", "provider": "x",
                                    "ok": True, "ms": 5, "input_tokens": 7})
    today = _dt.date.today().isoformat()
    assert today in log["by_day"], (today, list(log["by_day"]))
    assert core.usage_summary(log)["today"]["calls"] == 1, core.usage_summary(log)["today"]
    assert log["recent"][0]["ts"].endswith("Z")        # 时间点仍然记 UTC，只有账期是本地的


@case
def 教练换个说法再问一次不该攒出一道新题():
    """教练问了一道、人没答，下一轮它会换个说法再问——规整后当然不一样，于是每问一次入库一条。

    真实题库里 10 道题有 5 道是同一道 XOR 题的不同措辞，而且全部 `asked` 都是 0
    （2026-09-19 复盘 §11.3）。**没答过的题，同一组考点只留最新那一条。**
    """
    vault = make_vault({})
    # **考点集合每次都不一样**（`_mentioned` 按当轮提到的节点算，图在长集合就在变），
    # 所以只按集合相等去并，一条都并不掉——真正稳定的信号是题面本身
    core.add_question(vault, "1969 年 Minsky 用 XOR 打死单层感知机，为什么要拖到 1986 年才翻盘？",
                      ["达特茅斯会议"], source="chat")
    core.add_question(vault, "1969 年 Minsky 用 XOR 打死单层感知机，可为什么要拖到 1986 年才翻盘？",
                      ["感知器", "连接主义"], source="chat")
    qs = core.load_pool(vault)["questions"]
    assert len(qs) == 1, [q["stem"] for q in qs]
    assert qs[0]["stem"].startswith("1969 年 Minsky 用 XOR 打死单层感知机，可"), qs[0]  # 以最新措辞为准
    assert set(qs[0]["points"]) == {"达特茅斯会议", "感知器", "连接主义"}, qs[0]        # 考点取并集

    # 同一个主题但确实是另一道题（实测相似度 0.3～0.4）不能被并掉
    core.add_question(vault, "XOR 用两层网络就能解，delta rule 1960 年就有了，那缺的到底是什么？",
                      ["Adaline"], source="chat")
    assert len(core.load_pool(vault)["questions"]) == 2, "把一道不同的题也并掉了"

    # **出题那一路不并**：一轮测验围绕同几个节点出好几道不同的题，那是设计如此
    core.add_question(vault, "连接主义的起点是哪一年？", ["连接主义"], source="quiz")
    core.add_question(vault, "M-P 神经元和感知器差在哪？", ["连接主义"], source="quiz")
    assert len(core.load_pool(vault)["questions"]) == 4, "把一整轮测验并成一道了"

    # 答过的那条是历史，不再被顶替

    pool = core.load_pool(vault)
    pool["questions"][0]["asked"] = 1
    from core import pool as pool_mod          # save_pool 没从 core 包导出，直接用模块
    pool_mod.save_pool(vault, pool)
    core.add_question(vault, "1969 年 Minsky 用 XOR 打死单层感知机，到底卡在哪一样？",
                      ["连接主义"], source="chat")
    stems = [q["stem"] for q in core.load_pool(vault)["questions"]]
    assert len(stems) == 5, stems


@case
def 关掉复习是收走工具而不是嘱咐一句():
    """关掉复习必须是**结构性**的：规矩整段不拼、工具真收走、今日清单里没有复习项。

    只在提示词里加一句"不要出题"是压制不是关闭——手段还在模型手里（说明书上照样写着
    `quiz | 按这些节点出题考我`），而"不要做 X"这种反向指令本身还在提醒它有 X 这回事。
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from server import chat                                   # noqa: E402

    vault = make_vault({"nodes/a.md": node_md("A")})
    (vault / "relation-types.json").write_text(
        (Path(__file__).resolve().parents[3] / "relation-types.json").read_text("utf-8"), "utf-8")

    on = chat._system_prompt(vault, "教练", None)
    assert "quiz" in chat.tools_of("教练", vault), chat.tools_of("教练", vault)
    assert "每次开场先看一眼" in on and "按这些节点出题考我" in on, "默认该是全开的"

    # 先只关「教练会考我」那一档：聊天里该收手的全收手，但今日面板那一侧不归它管
    core.save_settings(vault, {"review_in_chat": False})
    only_chat = chat._system_prompt(vault, "教练", None)
    assert "quiz" not in chat.tools_of("教练", vault)
    assert "复习本身还开着" in only_chat, "只关这一档时不该说成整套关了"

    core.save_settings(vault, {"review_enabled": False})
    off = chat._system_prompt(vault, "教练", None)
    tools = chat.tools_of("教练", vault)
    # 能力层：工具真的没了（说明书从白名单渲染，所以说明书上也不会有）
    assert "quiz" not in tools and "record_review" not in tools, tools
    assert "按这些节点出题考我" not in off and "record_review" not in off, "说明书还留着出题工具"
    # 规矩层：三段整段不拼接
    for gone in ("每次开场先看一眼", "复习判定只许降级", "考点:"):
        assert gone not in off, f"`{gone}` 那段没被剔掉"
    # 建设那半边不能误伤
    assert "propose_changes" in tools and "线头" in off, "关掉复习不该动到建设那半边"
    # 只剩一句陈述状态的话，没有"不要做 X"的禁令
    assert "复习与出题整套在设置里关着" in off, "该留一句状态说明"
    assert "不要出" not in off and "不要主动" not in off, "又写回禁令了"

    # 设置本身：默认全开、坏文件回落默认、只认登记过的键
    assert core.load_settings(vault)["review_enabled"] is False
    core.settings.settings_path(vault).write_text("{ 这不是 json", encoding="utf-8")
    assert core.load_settings(vault) == core.settings.empty_settings(), "坏掉的设置文件该回落默认"
    core.save_settings(vault, {"乱写的键": True, "review_brief": False})
    got = core.load_settings(vault)
    assert "乱写的键" not in got and got["review_brief"] is False, got


@case
def 今日清单按开关摘掉复习项():
    """今日清单同时是教练 `today` 工具的数据源。只在前端过滤的话，界面安静了、
    教练一调工具照样看见一串欠账，然后开口催——所以要在这一层就摘掉。"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from server import curation                               # noqa: E402

    assert curation.REVIEW_KINDS == ("wrong", "due"), curation.REVIEW_KINDS


@case
def 缓存监控只看今天不看累计():
    """累计桶只加不减：糟过一天，这盏灯就再也不会转绿——那它既不报警也不解除，等于没有。

    所以 `cache_health` 的窗口必须是今天。这条用例钉的正是"今天已经健康、
    累计仍然难看"这个组合：2026-09-16 烧掉 $8 的那天永远躺在 by_op 里。
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    today = _dt.date.today().isoformat()

    def row(ts, read, write):
        return {"ts": ts.isoformat().replace("+00:00", "Z"), "op": "chat-教练", "ok": True,
                "ms": 1000, "input_tokens": 2, "output_tokens": 100,
                "cache_read_tokens": read, "cache_write_tokens": write}

    # 今天 5 次，每次读 30k / 写 3k = 10×（健康）；累计桶里压着一整天 1.2× 的烂账
    recent = [row(now - _dt.timedelta(minutes=i), 30000, 3000) for i in range(5)]
    log = {"totals": {"cache_read_tokens": 1_200_000, "cache_write_tokens": 1_000_000},
           "by_day": {}, "recent": recent,
           "by_op": {"chat-教练": {"calls": 60, "cache_read_tokens": 1_200_000,
                                   "cache_write_tokens": 1_000_000}}}
    health = core.usage.cache_health(log, today)
    assert health["ok"] and health["ratio"] == 10.0, health
    assert health["window"] == today and health["calls"] == 5, health
    assert health["worst"]["op"] == "chat-教练", health          # 今天的数，不是累计的 1.2×

    # 反过来：今天真的退化了就得报，而且不受累计好看的掩护
    bad = [row(now - _dt.timedelta(minutes=i), 13000, 20000) for i in range(5)]
    log_bad = {**log, "recent": bad,
               "by_op": {"chat-教练": {"calls": 60, "cache_read_tokens": 9_000_000,
                                       "cache_write_tokens": 1_000_000}}}
    assert not core.usage.cache_health(log_bad, today)["ok"], core.usage.cache_health(log_bad, today)

    # 样本不够不判：今天才两次调用，别急着报红
    few = {**log, "recent": [row(now, 1000, 9000), row(now, 1000, 9000)]}
    assert core.usage.cache_health(few, today)["ok"], core.usage.cache_health(few, today)

    # 单轮的 op 不进这个判据（quiz / suggest 每次都是新前缀，天然贴着 0）
    single = {**log, "recent": [{**row(now, 100, 9000), "op": "suggest"} for _ in range(5)]}
    got = core.usage.cache_health(single, today)
    assert got["ok"] and got["worst"] is None and got["calls"] == 0, got

    # summary 里今天 / 累计两桶都要带上读写比（契约默认 None，不填就永远是 null）
    got = core.usage_summary({**log, "by_day": {today: {"cache_read_tokens": 150_000,
                                                        "cache_write_tokens": 15_000}}}, today)
    assert got["today"]["cache_ratio"] == 10.0, got["today"]
    assert got["totals"]["cache_ratio"] == 1.2, got["totals"]


@case
def 时间账按负荷排阶段并判可行性():
    ls = {"target_date": "2026-10-01",                          # 16 天 × 1h/天 = 16 小时
          "stages": [{"name": "一", "points": [{"id": "a", "load": "重"}, {"id": "b", "load": "轻"}]},
                     {"name": "二", "points": [{"id": "c", "load": "中"}]}]}
    today = _dt.date(2026, 9, 15)
    got = core.schedule_of(ls, set(), 7, today)
    assert got["total_hours"] == 8.5 and got["remaining_hours"] == 8.5, got   # 5 + 1 + 2.5
    assert got["capacity_hours"] == 16.0 and got["verdict"] == "充裕", got
    # 已经建出来的点不再占时间预算，剩余工时和建议日一起往回缩
    done = core.schedule_of(ls, {"a"}, 7, today)
    assert done["remaining_hours"] == 3.5 and done["total_hours"] == 8.5, done
    assert done["suggested_target_date"] < got["suggested_target_date"], (done, got)
    # 阶段建议截止日按剩余工时摊在窗口里，且逐段递增、不超过目标日
    days = [r["suggested_deadline"] for r in got["stages"]]
    assert days[0] < days[1] == "2026-10-01", days


@case
def 时间账装不下时判不可能并给出现实日期():
    ls = {"target_date": "2026-09-20",                          # 5 天 × 1h = 5 小时
          "stages": [{"name": "一", "points": [{"id": f"p{i}", "load": "重"} for i in range(4)]}]}
    today = _dt.date(2026, 9, 15)
    got = core.schedule_of(ls, set(), 7, today)
    assert got["verdict"] == "不可能", got                        # 20 小时塞进 5 小时
    assert got["suggested_target_date"] == "2026-10-10", got      # 20h × 1.25 缓冲 ÷ 1h/天 = 25 天
    assert got["suggested_quota"] == 1, got                       # 4 个点 / 5 天
    # 目标日已经过去 → 容量为 0，仍然判不可能而不是崩
    ls["target_date"] = "2026-09-01"
    assert core.schedule_of(ls, set(), 7, today)["verdict"] == "不可能"
    # 日期填成人话不该让整条链路炸，当没填处理
    ls["target_date"] = "下个月吧"
    loose = core.schedule_of(ls, set(), 7, today)
    assert loose["verdict"] == "" and loose["days_left"] is None, loose


@case
def 落后只认写进计划的截止日():
    today = _dt.date(2026, 9, 15)
    ls = {"stages": [
        {"name": "一", "deadline": "2026-09-10", "points": [{"id": "a"}, {"id": "b"}]},
        {"name": "二", "deadline": "2026-12-01", "points": [{"id": "c"}]}]}
    assert core.schedule_of(ls, set(), 7, today)["behind"] == 2          # 逾期阶段里两个都没建
    assert core.schedule_of(ls, {"a"}, 7, today)["behind"] == 1          # 建好一个就少欠一个
    assert core.schedule_of(ls, {"a", "b"}, 7, today)["behind"] == 0
    # 没写 deadline 的阶段不算落后——建议日随时会变，拿它判落后会天天变脸
    bare = {"stages": [{"name": "一", "points": [{"id": "a"}]}]}
    assert core.schedule_of(bare, set(), 7, today)["behind"] == 0


@case
def 放置按关系族加权投票选分组():
    _, index, layout = placed_vault()
    assert core.inbox_ids(index, layout) == ["d"], core.inbox_ids(index, layout)
    assert core.target_group("d", index, layout) == layout["nodes"]["a"]["group"]
    # 把 a 挪到组B：投票跟着邻居走，不看 d 自己的 field
    layout["nodes"]["a"]["group"] = layout["nodes"]["c"]["group"]
    assert core.target_group("d", index, layout) == layout["nodes"]["c"]["group"]


@case
def 一条边都没有的节点落进对应抽象层那条泳道():
    """分组按抽象层切开之后，只认 field 会把节点丢在父框里、所有泳道之外。"""
    layout = {"revision": 1, "groups": {
        "g-AI": {"name": "AI", "x": 0, "y": 0, "w": 900, "h": 600},
        "g-AI--硬件": {"name": "硬件", "parent": "g-AI", "x": 20, "y": 40, "w": 400, "h": 200},
        "g-AI--AI应用": {"name": "AI应用", "parent": "g-AI", "x": 20, "y": 300, "w": 400, "h": 200},
        "g-别的": {"name": "别的", "x": 1000, "y": 0, "w": 300, "h": 300},
        "g-别的--硬件": {"name": "硬件", "parent": "g-别的", "x": 1020, "y": 40, "w": 200, "h": 100},
    }, "nodes": {}}
    index = {"edges": [], "nodes": [
        {"id": "孤点", "field": "AI", "layer": "AI应用"},
        {"id": "没填层", "field": "AI"},
        {"id": "别的域", "field": "不存在的域", "layer": "硬件"},
    ]}
    assert core.target_group("孤点", index, layout) == "g-AI--AI应用"
    # 同名的「硬件」在别的域下面，不能把点吸过去
    assert core.target_group("没填层", index, layout) == "g-AI", "没有「未分层」子框时退回父框"
    layout["groups"]["g-AI--未分层"] = {"name": "未分层", "parent": "g-AI",
                                      "x": 440, "y": 40, "w": 200, "h": 100}
    assert core.target_group("没填层", index, layout) == "g-AI--未分层"
    assert core.target_group("别的域", index, layout) is None, "域都不在图上就该留 Inbox"


@case
def 排满的分组会往下长一行而不是挤开别人():
    _, index, layout = placed_vault()
    gid = layout["nodes"]["a"]["group"]
    before = {nid: dict(n) for nid, n in layout["nodes"].items()}
    old_h = layout["groups"][gid]["h"]

    assert core.place_node("d", index, layout, gid=gid) is None, "框内还有空位？这个用例就没意义了"
    box, gpatch, npatch = core.place_or_grow("d", index, layout, today="2026-09-12", gid=gid)
    assert box and box["group"] == gid and box["state"] == "draft", box
    assert box["anchor"] == "a" and box["placedAt"] == "2026-09-12", box
    assert gpatch[gid]["h"] == old_h + core.CELL_H, gpatch
    assert npatch == {}, "只是长高，不该挪任何节点"
    assert layout["nodes"] == before, "长框时挪动了已有节点"
    assert box["y"] + box["h"] <= layout["groups"][gid]["y"] + gpatch[gid]["h"], "节点落在长高后的框外"


@case
def 长框会压到兄弟组时宁可不放():
    _, index, layout = placed_vault()
    gid = layout["nodes"]["a"]["group"]
    box = layout["groups"][gid]
    layout["groups"]["挡路的"] = {"name": "挡路的", "x": box["x"], "y": box["y"] + box["h"] + 4,
                                  "w": box["w"], "h": 100.0, "parent": box.get("parent"),
                                  "collapsed": False, "pinned": None, "color": None}
    assert core.plan_growth(gid, layout) is None
    # 并排的块被挡住就宁可不放；只有**泳道**（子框横跨整幅、上下码开）才允许整体下移
    assert core.is_lane_stack(layout["groups"][gid].get("parent"), layout) is False
    assert core.place_or_grow("d", index, layout, gid=gid) == (None, {}, {})


@case
def 泳道满了整条往下挪一行():
    """泳道框是按「当时有几个点」算出来的，常常只装得下一两个；
    不许长就等于这条道以后再也进不来新点（实测 248x128 的道，容量正好 1 个）。"""
    layout = {"revision": 1, "groups": {
        "g-AI": {"name": "AI", "x": 0, "y": 0, "w": 300, "h": 400},
        "g-AI--硬件": {"name": "硬件", "parent": "g-AI", "x": 10, "y": 40, "w": 280, "h": 128},
        "g-AI--应用": {"name": "AI应用", "parent": "g-AI", "x": 10, "y": 168, "w": 280, "h": 128},
    }, "nodes": {
        "GPU": {"x": 22, "y": 84, "w": 168, "h": 52, "group": "g-AI--硬件", "state": "final"},
        "RAG": {"x": 22, "y": 212, "w": 168, "h": 52, "group": "g-AI--应用", "state": "final"},
    }}
    index = {"edges": [], "nodes": [{"id": "NPU", "field": "AI", "layer": "硬件"},
                                    {"id": "GPU", "field": "AI", "layer": "硬件"},
                                    {"id": "RAG", "field": "AI", "layer": "AI应用"}]}
    assert core.is_lane_stack("g-AI", layout) is True
    assert core.plan_growth("g-AI--硬件", layout) is None, "普通长法会压到下面那条道"

    box, gpatch, npatch = core.place_or_grow("NPU", index, layout, today="2026-09-16")
    assert box and box["group"] == "g-AI--硬件", box
    assert gpatch["g-AI--硬件"]["h"] == 128 + core.CELL_H, gpatch
    assert gpatch["g-AI--应用"]["y"] == 168 + core.CELL_H, "下面那条道要整条下移"
    assert gpatch["g-AI"]["h"] == 400 + core.CELL_H, "父框跟着长高，否则泳道顶出去"
    assert npatch == {"RAG": {"y": 212 + core.CELL_H}}, "道里的节点要跟着道一起走"
    assert "GPU" not in npatch, "本来就在上面的节点不该动"
    # 新节点落在长高后的道里
    assert box["y"] + box["h"] <= 40 + gpatch["g-AI--硬件"]["h"], box


@case
def 并排的块不算泳道():
    """两个域并排摆着时，把人家推走是破坏排版，不是加一行。"""
    layout = {"revision": 1, "groups": {
        "top": {"name": "顶", "x": 0, "y": 0, "w": 400, "h": 200},
        "left": {"name": "左", "parent": "top", "x": 10, "y": 40, "w": 180, "h": 120},
        "right": {"name": "右", "parent": "top", "x": 200, "y": 40, "w": 180, "h": 120},
    }, "nodes": {}}
    assert core.is_lane_stack("top", layout) is False
    assert core.plan_lane_growth("left", layout) is None


@case
def digest_汇总欠账():
    vault, index, layout = placed_vault()
    box, gpatch, _ = core.place_or_grow("d", index, layout, today="2026-09-01")
    layout["nodes"]["d"] = box
    for k, fields in gpatch.items():
        layout["groups"][k].update(fields)
    d = core.build_digest(vault, index, layout, _dt.date(2026, 9, 12))
    assert d["counts"]["inbox"] == 0 and d["counts"]["drafts"] == 1, d["counts"]
    assert d["drafts"][0]["days"] == 11 and d["drafts"][0]["stale"] is True, d["drafts"]
    assert d["counts"]["due"] == 1 and d["due"][0]["id"] == "a", d["due"]
    assert any(b["count"] >= 1 for b in d["bridges"]) or not d["bridges"], d["bridges"]


def _digest_of(files: dict) -> dict:
    vault, r = build(files)
    return core.build_digest(vault, r.data, core.build_initial_layout(r.data), _dt.date(2026, 9, 12))


def _links(d: dict) -> dict:
    return {(h["source"], h["target"]): h["relation"] for h in d["links"]}


@case
def digest_同族兄弟是缺边不是重复():
    """`专用寄存器` / `通用寄存器` 字面重合度 0.8，可它们是两个东西，不是一个东西的两份。

    中文复合词天生共享中心语，字面相似度在这里判不了重复——它判出来的是"同族"，
    而同族缺的是一条 `对比` 边。这条测试锁的就是这个翻转。
    """
    d = _digest_of({
        "nodes/x/通用寄存器.md": node_md("通用寄存器"),
        "nodes/x/专用寄存器.md": node_md("专用寄存器"),
        "nodes/x/完全无关的东西.md": node_md("完全无关的东西"),
    })
    assert _links(d).get(("专用寄存器", "通用寄存器")) == "对比", d["links"]
    assert not d["duplicates"], d["duplicates"]
    assert all("完全无关的东西" not in (h["source"], h["target"]) for h in d["links"]), d["links"]


@case
def digest_名字里含着另一个是上下位():
    d = _digest_of({
        "nodes/x/内存.md": node_md("内存"),
        "nodes/x/堆内存.md": node_md("堆内存"),
    })
    assert _links(d).get(("内存", "堆内存")) == "包含", d["links"]   # 短的那个是上位
    assert not d["duplicates"], d["duplicates"]


@case
def digest_多出来的尾巴加了等于没加才算重复():
    """`MHA` / `MHA机制` 和 `内存` / `堆内存` 字面上都是包含关系，差别只在多出来的那一截。"""
    d = _digest_of({
        "nodes/x/MHA.md": node_md("MHA"),
        "nodes/x/MHA机制.md": node_md("MHA机制"),
    })
    assert not d["links"], d["links"]
    assert {tuple(sorted((x["a"], x["b"]))) for x in d["duplicates"]} == {("MHA", "MHA机制")}


@case
def digest_已经连过边的对不再提醒():
    d = _digest_of({
        "nodes/x/内存.md": node_md("内存", rels="- 包含:: [[堆内存]]\n"),
        "nodes/x/堆内存.md": node_md("堆内存"),
    })
    assert not d["links"] and not d["duplicates"], (d["links"], d["duplicates"])


@case
def digest_共享一个类别后缀不算同族():
    """单个「器」是中文的类别后缀，不是共同的意思——按它算，满图的 XX器 两两成"同族"。"""
    d = _digest_of({
        "nodes/x/寄存器.md": node_md("寄存器"),
        "nodes/x/控制器.md": node_md("控制器"),
    })
    assert not d["links"], d["links"]


@case
def digest_缩写两头各共一个字算同族():
    """`RAM` / `ROM` 共享的是 R…M，前后缀各一个字——合计够两个，不该被后缀长度那道闸误伤。"""
    d = _digest_of({
        "nodes/x/RAM.md": node_md("RAM"),
        "nodes/x/ROM.md": node_md("ROM"),
    })
    assert _links(d).get(("RAM", "ROM")) == "对比", d["links"]


@case
def digest_连边建议把孤点排在前面():
    """连边建议最大的用处是把 degree 0 的点接回图里，两端都孤的那条最该先连。"""
    d = _digest_of({
        "nodes/x/内存.md": node_md("内存"),
        "nodes/x/堆内存.md": node_md("堆内存"),
        "nodes/x/栈内存.md": node_md("栈内存", rels="- 相关:: [[别处]]\n"),
        "nodes/x/别处.md": node_md("别处"),
    })
    assert [h["lonely"] for h in d["links"]] == sorted((h["lonely"] for h in d["links"]), reverse=True), d["links"]
    assert d["links"][0]["lonely"] == 2, d["links"][0]


@case
def digest_孤点只算已经建出来的():
    """stub 和还没建的点没有正文，谈不上"该连谁"——把它们混进孤点，
    这份清单就成了"图里所有不完整的东西"，而不是"有内容却没接上的那些"。"""
    d = _digest_of({
        "nodes/x/独行侠.md": node_md("独行侠"),
        "nodes/x/甲.md": node_md("甲", rels="- 相关:: [[乙]]\n"),
        "nodes/x/乙.md": node_md("乙"),
        "nodes/x/丙.md": node_md("丙", rels="- 相关:: [[还没建的]]\n"),
    })
    ids = [x["id"] for x in d["lonely"]]
    assert ids == ["独行侠"], d["lonely"]          # 甲乙丙都连着；"还没建的"是 stub，不算
    assert d["counts"]["lonely"] == 1, d["counts"]


@case
def digest_缺year和年份可疑是两回事():
    """`bad_years` 是**算得出来的矛盾**（演化边两端倒挂），`no_year` 只是没填。
    没填不是错，但它是历史视图的开关——没有 year 的节点根本不出现在时间轴上。"""
    d = _digest_of({
        "nodes/x/早.md": node_md("早", extra="year: 1990\n"),
        "nodes/x/晚.md": node_md("晚"),
    })
    assert d["no_year"] == ["晚"], d["no_year"]
    assert d["counts"]["no_year"] == 1 and not d["bad_years"], (d["counts"], d["bad_years"])


@case
def coach_今日清单里的孤点带着现成建议():
    """今日清单里唯一一类"连"的任务。带上 digest 算出来的建议，点一下就能连；
    互为建议的一对只摆一个——连那条边两个一起脱离孤岛。"""
    vault, r = build({
        "nodes/x/内存.md": node_md("内存"),
        "nodes/x/堆内存.md": node_md("堆内存"),
    })
    layout = core.build_initial_layout(r.data)
    t = core.build_today(vault, r.data, layout, core.empty_projects(), _dt.date(2026, 9, 12))
    lonely = [it for it in t["items"] if it["kind"] == "lonely"]
    assert len(lonely) == 1, lonely                     # 两个互为建议，只占一个坑
    assert lonely[0]["id"] == "内存", lonely[0]
    assert lonely[0]["link"] == {"relation": "包含", "target": "堆内存"}, lonely[0]


@case
def digest_field和所在域对不上要报出来():
    """`field` 在 md、`group` 在 layout.json，改 md 不动画布——那条分界是对的，
    代价是两边能**悄悄走散**，而唯一的发现方式原来是肉眼看出"咦怎么没动"。

    这和「年份可疑」同一类：分组 id 就是 `g-<field>--<layer>`，矛盾算得出来。
    """
    vault, r = build({
        "nodes/x/甲.md": node_md("甲", field="AI", extra="layer: 理论\n"),
        "nodes/x/乙.md": node_md("乙", field="AI", extra="layer: 理论\n"),
    })
    layout = {"schema_version": 2, "revision": 1, "groups": {
        "g-AI": {"name": "AI", "x": 0, "y": 0, "w": 800, "h": 400},
        "g-AI--理论": {"name": "理论", "parent": "g-AI", "x": 24, "y": 44, "w": 300, "h": 200},
        "g-计算机系统": {"name": "计算机系统", "x": 0, "y": 600, "w": 800, "h": 400},
        "g-计算机系统--理论": {"name": "理论", "parent": "g-计算机系统", "x": 24, "y": 644, "w": 300, "h": 200},
    }, "nodes": {
        "甲": {"x": 40, "y": 60, "w": 196, "h": 64, "group": "g-AI--理论", "state": "final"},
        "乙": {"x": 40, "y": 660, "w": 196, "h": 64, "group": "g-计算机系统--理论", "state": "final"},
    }}
    d = core.build_digest(vault, r.data, layout, _dt.date(2026, 9, 12))
    ids = [m["id"] for m in d["misplaced"]]
    assert ids == ["乙"], d["misplaced"]                  # 甲在对的域里，不该报
    hit = d["misplaced"][0]
    assert hit["field"] == "AI" and hit["group_name"] == "计算机系统", hit
    assert hit["want"] == "g-AI--理论" and hit["want_exists"] is True, hit


@case
def digest_该去的那条道还没建时标出来():
    """道不存在时 by_field_and_layer 会退回领域大框——那时按钮得说"开一条道"而不是"挪过去"。"""
    vault, r = build({"nodes/x/甲.md": node_md("甲", field="AI", extra="layer: 理论\n")})
    layout = {"schema_version": 2, "revision": 1, "groups": {
        "g-AI": {"name": "AI", "x": 0, "y": 0, "w": 800, "h": 400},
        "g-计算机系统": {"name": "计算机系统", "x": 0, "y": 600, "w": 800, "h": 400},
        "g-计算机系统--理论": {"name": "理论", "parent": "g-计算机系统", "x": 24, "y": 644, "w": 300, "h": 200},
    }, "nodes": {"甲": {"x": 40, "y": 660, "w": 196, "h": 64, "group": "g-计算机系统--理论", "state": "final"}}}
    d = core.build_digest(vault, r.data, layout, _dt.date(2026, 9, 12))
    hit = d["misplaced"][0]
    assert hit["want"] == "g-AI" and hit["want_exists"] is False, hit


@case
def placement_并排的块排满了就另起一行():
    """AI 那个域下面是**并排的块**不是泳道。新开一条道要跟着已有排法走，
    右边塞得下就并上去，塞不下另起一行——而且只往下长父框，不动任何已有的框。"""
    layout = {"groups": {
        "g-AI": {"name": "AI", "x": 0, "y": 0, "w": 600, "h": 300},
        "g-AI--硬件": {"name": "硬件", "parent": "g-AI", "x": 24, "y": 44, "w": 248, "h": 128},
    }}
    before = {gid: dict(g) for gid, g in layout["groups"].items()}
    gid, lane, grown = core.plan_new_lane("g-AI", "理论", layout)
    assert gid == "g-AI--理论" and lane["parent"] == "g-AI", (gid, lane)
    assert lane["y"] == before["g-AI--硬件"]["y"], "右边还塞得下，应该并在同一行"
    assert lane["x"] > before["g-AI--硬件"]["x"] + before["g-AI--硬件"]["w"], lane
    assert layout["groups"]["g-AI--硬件"] == before["g-AI--硬件"], "不许动已有的框"


@case
def placement_长出来会压到隔壁的域就不建():
    """宁可让人自己拖，也不能为了塞一条新道把旁边的域挤变形。"""
    layout = {"groups": {
        "g-AI": {"name": "AI", "x": 0, "y": 0, "w": 300, "h": 200},
        "g-AI--硬件": {"name": "硬件", "parent": "g-AI", "x": 24, "y": 44, "w": 248, "h": 128},
        "g-隔壁": {"name": "隔壁", "x": 0, "y": 210, "w": 300, "h": 200},   # 紧贴在 AI 下面
    }}
    assert core.plan_new_lane("g-AI", "理论", layout) is None


@case
def digest_跨分组桥给重名分组带上父级():
    """布局里 `硬件` 有两个（计算机系统下一个、AI 下一个）。只取 name 的话，最有价值的
    那条桥会显示成"硬件 → 硬件"——看不出在说什么，等于把这条线藏了。"""
    groups = {
        "g-sys": {"name": "计算机系统"}, "g-ai": {"name": "AI"},
        "g-sys--hw": {"name": "硬件", "parent": "g-sys"},
        "g-ai--hw": {"name": "硬件", "parent": "g-ai"},
        "g-sys--sw": {"name": "系统软件", "parent": "g-sys"},
    }
    labels = core.group_labels(groups)
    assert labels["g-sys--hw"] == "计算机系统/硬件" and labels["g-ai--hw"] == "AI/硬件", labels
    assert labels["g-sys--sw"] == "系统软件", labels        # 不重名的不加前缀，加了反而啰嗦
    assert labels["g-sys"] == "计算机系统", labels           # 顶层没父级，照原样


@case
def 真实_vault_无错误():
    if not (REPO / "nodes").is_dir():
        print("    （跳过：仓库里没有 nodes/）", end="")
        return
    r = core.build_index(REPO)
    assert not core.validate_index(r.data), core.validate_index(r.data)
    assert r.stats["errors"] == 0, [d.render() for d in r.diags.errors]
    # 边数不做下限断言：2026-09-14 清空了全部关系，由人重新连一遍，
    # 这期间真实 vault 的边数会从 0 慢慢长回去。这里只守"节点没丢、契约没破"。
    assert r.stats["nodes"] > 50, r.stats


# ---------------------------------------------------------------- 导入：方案 → 变更集 + 待审边

def _plan_fixture() -> dict:
    return {
        "nodes": [
            {"id": "新甲", "name": "新甲", "desc": "新概念", "type": "概念",
             "body": "## 描述\n新甲是什么，见 [[a]] 和 [[没有的概念]]。\n\n## 关系\n- 依赖:: [[a]]",
             "relations": [
                 {"type": "依赖", "target": "a", "confidence": 0.95},
                 {"type": "对比", "target": "b", "confidence": 0.4, "note": "拿不准"},
                 {"type": "部件", "target": "新乙", "confidence": 0.2},
                 {"type": "依赖", "target": "不存在"},
             ]},
            {"id": "新乙", "name": "新乙", "desc": "另一个", "relations": []},
            {"id": "a", "name": "A", "desc": "已存在的", "relations": []},
        ],
        "stubs": [{"id": "壳", "name": "壳", "desc": "空壳", "why": "关系要用"}],
        "enrich": [{"existing": "b", "content": "补一段关于 B 的新理解。", "why": "文章讲到了"},
                   {"existing": "没这个", "content": "x"}],
        "summary": "测试方案",
    }


@case
def 导入翻译_三种产物各归其位():
    vault, r = build({"nodes/x/a.md": node_md("A"), "nodes/x/b.md": node_md("B")})
    rt = core.load_relation_types(vault)
    tr = core.translate(_plan_fixture(), r.data, rt, core.ImportTarget("测试", "某文章", today="2026-09-18"))
    kinds = [(c["type"], c["source"]) for c in tr.changes]
    assert ("create_node", "新甲") in kinds and ("create_node", "新乙") in kinds, kinds
    assert ("create_node", "a") not in kinds, "已存在的节点不能被覆盖"
    assert any("已存在" in w for w in tr.warnings), tr.warnings
    # 边：高置信连老节点直接写；低置信连老节点进待审；新↔新不看置信度直接写；目标不存在丢弃
    direct = {(c["relation"], c["target"]) for c in tr.changes if c["type"] == "add_edge" and c["source"] == "新甲"}
    assert direct == {("依赖", "a"), ("部件", "新乙")}, direct
    assert [(p["relation"], p["target"], p["confidence"]) for p in tr.pending] == [("对比", "b", 0.4)], tr.pending
    assert any("不存在" in w and "丢弃" in w for w in tr.warnings), tr.warnings
    # stub：方案给的 + 正文里链到的不存在 id 自动补壳；正文里的 [[a]] 已存在不补
    stub_paths = {c["path"] for c in tr.changes if c["type"] == "create_node" and c["fields"].get("status") == "stub"}
    assert stub_paths == {"nodes/_stubs/壳.md", "nodes/_stubs/没有的概念.md"}, stub_paths
    # 正文里带的 `## 关系` 段被截掉，边只从 relations 来
    body = next(c for c in tr.changes if c["source"] == "新甲")["body"]
    assert "## 关系" not in body and "[[没有的概念]]" in body, body
    # enrich：只追加、带来源引言；目标不存在的跳过
    enrich = [c for c in tr.changes if c["type"] == "append_body"]
    assert [c["source"] for c in enrich] == ["b"], enrich
    assert enrich[0]["body"].startswith("> 补充自《某文章》（2026-09-18）：文章讲到了\n\n补一段"), enrich[0]["body"]
    assert tr.counts() == {"nodes": 2, "stubs": 2, "enrich": 1, "edges": 2, "pending": 1}, tr.counts()


@case
def 导入翻译_旧字段merge_into仍认且没confidence当确定():
    vault, r = build({"nodes/x/a.md": node_md("A")})
    rt = core.load_relation_types(vault)
    plan = {"nodes": [{"id": "n", "name": "N", "desc": "d", "relations": [{"type": "依赖", "target": "a"}]}],
            "merge_into": [{"existing": "a", "content": "老写法"}]}
    tr = core.translate(plan, r.data, rt, core.ImportTarget("测试", "旧方案"))
    assert not tr.pending, "旧方案没有 confidence 字段，不能因此全进待审"
    assert [c["type"] for c in tr.changes] == ["create_node", "add_edge", "append_body"], tr.changes


@case
def 导入翻译_落盘后老节点只追加不改写且待审边进pending():
    vault, r = build({"nodes/x/a.md": node_md("A"), "nodes/x/b.md": node_md("B", rels="- 部件:: [[a]]")})
    before_b = core.read(vault / "nodes/x/b.md")
    rt = core.load_relation_types(vault)
    target = core.ImportTarget("测试", "某文章", today="2026-09-18")
    tr = core.translate(_plan_fixture(), r.data, rt, target)
    edits = core.plan(vault, tr.changes, r.data)
    core.commit(vault, edits)
    after_b = core.read(vault / "nodes/x/b.md")
    head, _, tail = after_b.partition("## 关系")
    assert "> 补充自《某文章》（2026-09-18）" in head and "补一段关于 B 的新理解" in head, after_b
    assert before_b.split("## 关系")[0].strip() in head, "老正文一个字都不能少"
    assert "- 部件:: [[a]]" in tail, "老节点自己的关系段要原样保留"
    new = core.read(vault / "nodes/测试/新甲.md")
    assert "- 依赖:: [[a]]" in new and "- 部件:: [[新乙]]" in new and "对比" not in new, new
    assert (vault / "nodes/_stubs/壳.md").exists() and (vault / "nodes/_stubs/没有的概念.md").exists()
    added = core.add_pending(vault, tr.pending, {"source": "某文章", "imported_at": "2026-09-18"})
    assert [a["id"] for a in added] == ["新甲->b#对比"], added
    doc = core.load_pending(vault)
    assert doc["edges"][0]["status"] == "pending" and doc["edges"][0]["origin"]["source"] == "某文章"
    # 再导一次同一条：不重复
    assert core.add_pending(vault, tr.pending, {"source": "又一篇"}) == []
    assert core.remove_pending(vault, ["新甲->b#对比"]) == 1 and core.load_pending(vault)["edges"] == []
    r2 = core.build_index(vault)
    assert not r2.diags.errors, [d.message for d in r2.diags.errors]


@case
def test_article_prompt_only_lists_linkable_subset():
    """提示词里的 id 不再是全量：相关节点 + 它们的一跳邻居 + 目标领域的节点，其余不列、封顶 limit。"""
    from core import article as art
    cards = [
        {"id": "attention", "name": "注意力", "aliases": [], "tags": [], "desc": "", "field": "AI",
         "edges": [("依赖", "softmax")]},
        {"id": "softmax", "name": "softmax", "aliases": [], "tags": [], "desc": "", "field": "数学", "edges": []},
        {"id": "gpu", "name": "GPU", "aliases": [], "tags": [], "desc": "", "field": "AI", "edges": []},
        {"id": "raft", "name": "Raft", "aliases": [], "tags": [], "desc": "", "field": "分布式", "edges": []},
        {"id": "ghost", "name": "幽灵", "aliases": [], "tags": [], "desc": "", "field": "AI",
         "edges": [("对比", "不存在的点")]},
    ]
    related = art.select_related(cards, "attention 机制的原理")
    assert [c["id"] for c in related] == ["attention"], related
    ids = art.select_linkable(cards, related, "AI")
    # 相关的 attention、它的邻居 softmax（跨领域也带上）、AI 领域的 gpu / ghost；raft 不在、悬空的邻居不在
    assert ids == ["attention", "ghost", "gpu", "softmax"], ids
    # 封顶时按优先级：相关 > 邻居 > 同领域
    assert art.select_linkable(cards, related, "AI", limit=2) == ["attention", "softmax"]
    # 领域为空时只剩相关 + 邻居
    assert art.select_linkable(cards, related, "") == ["attention", "softmax"]
    rt = core.load_relation_types(REPO)
    prompt = art.build_article_prompt(cards, rt, "attention 机制的原理", "AI")
    assert "图里共 5 个，这里只列 4 个" in prompt and "raft" not in prompt, prompt[:600]
    assert "{{" not in prompt, "模板占位符没替换干净"


@case
def test_sections_outline_skips_code_fences_and_extracts_by_title():
    """目录从 ## 标题现算，代码块里的 # 注释不算标题；按标题取一节要连子节一起带、到下一个同级为止。"""
    text = (
        "# 标题\n\n## 描述\n一句话。\n\n## 一、分界\n正文 A\n\n```python\n# 这不是标题\nx = 1\n```\n\n"
        "### 案例 1【路 A】细节\n子节 1\n\n### 案例 2\n子节 2\n\n## 二、粒度\n正文 B\n\n## 关系\n- 依赖:: [[x]]\n"
    )
    titles = [h.title for h in core.outline(text)]
    assert titles == ["标题", "描述", "一、分界", "案例 1【路 A】细节", "案例 2", "二、粒度", "关系"], titles
    toc = core.describe_outline(text)
    assert "这不是标题" not in toc and "- 一、分界" in toc and "    - 案例 2" in toc and "- 标题" not in toc, toc

    head, sec = core.extract_section(text, "一、分界")
    assert head.level == 2 and sec.startswith("## 一、分界") and "子节 2" in sec and "正文 B" not in sec, sec
    # 去标点空白的模糊匹配：写「案例1」能对上「案例 1【路 A】细节」，只取到下一个同级（案例 2）之前
    head, sec = core.extract_section(text, "案例1")
    assert head.title.startswith("案例 1") and "子节 1" in sec and "子节 2" not in sec, sec
    # 完全一致优先于包含：「案例 2」不该被「案例 1」抢走
    assert core.extract_section(text, "案例 2")[0].title == "案例 2"
    assert core.extract_section(text, "不存在的节") is None and core.find_heading(text, "") is None
    # 最后一节取到文件尾
    assert core.extract_section(text, "关系")[1].endswith("[[x]]")


# ---------------------------------------------------------------- 执行

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
