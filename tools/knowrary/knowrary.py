#!/usr/bin/env python3
"""Knowrary · 迁移 / 导入 / 校验工具（第二版数据规范）

子命令：
  vault    把旧 vault（`- 类型 → [[目标]]`、frontmatter 只有 tags/source）迁移成第二版结构
  check    按《Markdown 文档规范》校验一个 vault，输出诊断
  context  输出类型表 / 全部 id / 与文章相关的节点（供 knowrary-import skill 使用）
  apply    把方案 JSON 校验后写入 vault（供 knowrary-import skill 使用）
  article  无人值守版：脚本自己调 LLM 把文章拆成节点并写入（提示词在 prompts/article.md）
  llm      查看 / 测试 LLM 配置（.knowrary/llm.local.json，见 llm.example.json）

零第三方依赖（有 pyyaml 时用它解析 frontmatter，没有则用内置的简易解析）。
LLM 后端见 llm_backend.py：配置文件里定义多个 provider（claude-cli / anthropic / openai 兼容），
按角色（learn 学习、review 审核）选用；没有配置文件时退回 `claude -p`。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import llm_backend  # 同目录模块
from core import (Diagnostics, Edge, Node, RelationTypes, build_digest, build_index,
                  build_initial_layout, due_nodes, dump_frontmatter, find_orphans, first_paragraph,
                  index_path, layout_path, load_json, load_log, load_previous, load_relation_types,
                  load_vault, read, record_review, stamp, validate_index, write, write_json_atomic)
from core import (build_project_layout, legacy_plans_path, load_projects, projects_path,
                  save_projects, upgrade_v1)
from core import (ChangeRejected, ImportTarget, Translation, WriteConflict, add_home, add_pending, commit,
                  rename_in_plan, translate)
from core import plan as plan_changes
from core import build_article_prompt, cards_from_index, cards_from_nodes, describe_related, select_linkable, select_related
from core import check_length, isolated as isolated_blocks, near_misses, normalize_claims, project_points
from core import parse_json
from core.mdio import RE_LINK

HERE = Path(__file__).resolve().parent
TODAY = dt.date.today().isoformat()


# ---------------------------------------------------------------- vault 迁移

@dataclass
class MigrateReport:
    nodes: int = 0
    stubs_created: list[str] = field(default_factory=list)
    type_map: Counter = field(default_factory=Counter)
    unmapped: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    flipped: int = 0
    dedup_dropped: list[str] = field(default_factory=list)
    body_links_missing: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    warnings: list[str] = field(default_factory=list)


def map_legacy_edge(e: Edge, legacy: dict, rep: MigrateReport) -> Edge:
    """旧类型 → 新类型；flip 时交换方向。"""
    rule = legacy.get(e.type)
    rep.type_map[f"{e.type} → {rule['to'] if rule else e.type}"] += 1
    if not rule:
        rep.unmapped[e.type].append(f"{e.source} → {e.target}")
        return Edge(e.source, e.type, e.target, e.year, e.note, origin=e.type)
    if rule.get("flip"):
        rep.flipped += 1
        return Edge(e.target, rule["to"], e.source, e.year, e.note, origin=e.type)
    return Edge(e.source, rule["to"], e.target, e.year, e.note, origin=e.type)


def dedupe_edges(edges: list[Edge], rt: RelationTypes, rep: MigrateReport) -> list[Edge]:
    """去掉：完全重复；互逆重复（保留 canonical 一侧）；对称重复（保留 id 小的一侧）。"""
    seen: set[tuple] = set()
    keep: list[Edge] = []
    index = {(e.source, e.type, e.target) for e in edges}
    for e in edges:
        key = (e.source, e.type, e.target)
        if key in seen:
            continue
        seen.add(key)
        canon = rt.canonical(e.type)
        inv = rt.inverse(e.type)
        if canon and (e.target, canon, e.source) in index:
            rep.dedup_dropped.append(f"{e.source} {e.type} {e.target}（已有 {e.target} {canon} {e.source}）")
            continue
        if not canon and inv and rt.canonical(inv) == e.type:
            pass  # 我是正向，保留
        if rt.symmetric(e.type) and (e.target, e.type, e.source) in index and e.target < e.source:
            rep.dedup_dropped.append(f"{e.source} {e.type} {e.target}（对称边保留在 {e.target}）")
            continue
        keep.append(e)
    return keep


def build_frontmatter(node: Node, folder: str, field_name: str, rep: MigrateReport) -> dict:
    old = node.fm
    tags = [t for t in (old.get("tags") or []) if t not in ("待学", "MOC")]
    h1 = re.search(r"^# (.+)$", node.body, re.M)
    fm: dict = {
        "name": h1.group(1).strip() if h1 else node.id,
        "field": field_name,
    }
    if "待学" in (old.get("tags") or []):
        fm["status"] = "stub"
    if tags:
        fm["tags"] = tags
    fm["desc"] = first_paragraph(node.body) or "待补充"
    if fm["desc"] == "待补充" and fm.get("status") != "stub":
        rep.warnings.append(f"{node.id}: 正文为空或过短，desc 置为“待补充”")
    if old.get("source"):
        fm["source"] = old["source"]
    if folder:
        fm.setdefault("tags", [])
        sub = re.sub(r"^\d+-", "", folder)
        if sub not in fm["tags"]:
            fm["tags"].append(sub)
    return fm


def render_node(fm: dict, body: str, edges: list[Edge], rel_tail: str = "") -> str:
    """frontmatter + 正文 + `## 关系` + 关系段之后的原文（`## 参考资料` / `## 待办` 等，逐字保留）。"""
    body = body.rstrip() + "\n"
    rel = "\n## 关系\n" + ("\n".join(e.line() for e in edges) + "\n" if edges else "")
    tail = ("\n" + rel_tail.lstrip("\n")) if rel_tail.strip() else ""
    return dump_frontmatter(fm) + body + rel + tail


def stub_text(target: str, referrers: list[str], field_name: str) -> str:
    fm = {"name": target, "field": field_name, "status": "stub", "desc": "待补充"}
    body = (f"# {target}\n\n> 空壳节点（stub）：被 {', '.join(f'[[{r}]]' for r in referrers[:5])} 引用，"
            f"尚未学习补充。\n")
    return render_node(fm, body, [])


@dataclass
class MigratePaths:
    """一次迁移的源 / 目标 vault 与统一顶层领域。"""

    src: Path
    dst: Path
    field_name: str


def write_migrated_nodes(nodes: dict[str, Node], by_source: dict[str, list[Edge]],
                         paths: MigratePaths, rep: MigrateReport) -> None:
    """写节点：MOC（tags 含 MOC）落 fields/，其余按原子目录落 nodes/<子目录>/。"""
    for n in nodes.values():
        rel_folder = str(n.path.parent.relative_to(paths.src)) if n.path.parent != paths.src else ""
        is_moc = "MOC" in (n.fm.get("tags") or [])
        fm = build_frontmatter(n, "" if is_moc else rel_folder, paths.field_name, rep)
        if is_moc:
            fm["type"] = "领域总览"
            out = paths.dst / "fields" / f"{n.id}.md"
        else:
            out = paths.dst / "nodes" / rel_folder / f"{n.id}.md"
        write(out, render_node(fm, n.body, by_source.get(n.id, []), n.rel_tail))


def cmd_vault(args: argparse.Namespace) -> None:
    src, dst = Path(args.src).resolve(), Path(args.dst).resolve()
    if dst.exists() and any(dst.iterdir()) and not args.force:
        raise SystemExit(f"目标目录非空：{dst}（加 --force 覆盖其中的 nodes/ fields/）")
    rt = RelationTypes(load_json(HERE / "relation-types.v2.json"))
    legacy = load_json(HERE / "legacy-types.json")["map"]
    nodes, diags = load_vault(src)
    rep = MigrateReport(nodes=len(nodes))
    rep.warnings.extend(d.render() for d in diags.items)

    # 1. 边：映射 + 翻转 + 去重
    mapped: list[Edge] = []
    for n in nodes.values():
        mapped.extend(map_legacy_edge(e, legacy, rep) for e in n.edges)
    mapped = dedupe_edges(mapped, rt, rep)
    by_source: dict[str, list[Edge]] = defaultdict(list)
    for e in mapped:
        by_source[e.source].append(e)

    # 2. stub：所有边目标 + 正文链接里不存在的节点
    referrers: dict[str, list[str]] = defaultdict(list)
    for e in mapped:
        if e.target not in nodes:
            referrers[e.target].append(e.source)
    for n in nodes.values():
        for link in set(RE_LINK.findall(n.body)):
            if link not in nodes and link not in referrers:
                rep.body_links_missing[n.id].append(link)

    # 3. 写节点与 stub
    write_migrated_nodes(nodes, by_source, MigratePaths(src, dst, args.field), rep)
    for target, refs in sorted(referrers.items()):
        write(dst / "nodes" / "_stubs" / f"{target}.md", stub_text(target, refs, args.field))
        rep.stubs_created.append(target)

    # 4. 其他文件
    if (src / "assets").exists():
        shutil.copytree(src / "assets", dst / "assets", dirs_exist_ok=True)
    (dst / ".knowrary" / "backup").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "relation-types.v2.json", dst / "relation-types.json")
    report = dst / ".knowrary" / "MIGRATION-REPORT.md"
    write(report, migrate_report_md(rep, src, dst, mapped))
    print(read(report))


def migrate_report_md(rep: MigrateReport, src: Path, dst: Path, edges: list[Edge]) -> str:
    fam = Counter()
    rt = RelationTypes(load_json(HERE / "relation-types.v2.json"))
    for e in edges:
        fam[rt.family(e.type)] += 1
    L = [f"# 迁移报告 {TODAY}", "", f"- 源：`{src}`", f"- 目标：`{dst}`",
         f"- 节点：{rep.nodes} 个；新建 stub：{len(rep.stubs_created)} 个；边：{len(edges)} 条"
         f"（方向翻转 {rep.flipped} 条，去重丢弃 {len(rep.dedup_dropped)} 条）", "",
         "## 边按族分布", ""]
    L += [f"- {f}：{c}" for f, c in fam.most_common()]
    L += ["", "## 未映射的旧类型（原样保留，归弱关联族，需人工决定）", ""]
    L += [f"- `{t}`（{len(v)} 条）：{'；'.join(v[:4])}" for t, v in sorted(rep.unmapped.items(), key=lambda x: -len(x[1]))] or ["- 无"]
    L += ["", "## 新建的 stub", "", "- " + ("、".join(rep.stubs_created) or "无")]
    L += ["", "## 去重丢弃的边", ""] + ([f"- {d}" for d in rep.dedup_dropped] or ["- 无"])
    L += ["", "## 正文链接到不存在的节点（未建 stub，仅提示）", ""]
    L += [f"- {n}: {', '.join(v)}" for n, v in rep.body_links_missing.items()] or ["- 无"]
    L += ["", "## 其他警告", ""] + ([f"- {w}" for w in rep.warnings] or ["- 无"])
    L += ["", "## 类型映射明细", ""] + [f"- {k}: {c}" for k, c in rep.type_map.most_common()]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- check

def check_secret_leak(vault: Path) -> list[str]:
    """本地 LLM 配置（含密钥）不允许被 git 跟踪；仓库开源时这是最后一道闸。"""
    try:
        proc = subprocess.run(["git", "-C", str(vault), "ls-files", "--", "*.local.json", "**/*.local.json"],
                              capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [f"{f}: 本地配置被 git 跟踪，可能泄露密钥。执行 `git rm --cached {f}` 并确认 .gitignore 含 `*.local.json`"
            for f in proc.stdout.split()]


def print_diagnostics(diags: Diagnostics, extra_errors: list[str], max_warn: int) -> int:
    """统一的诊断输出（index / check 共用）。返回错误条数。"""
    errors = [d.render() for d in sorted(diags.errors, key=lambda d: (d.file, d.code))] + extra_errors
    warns = [d.render() for d in sorted(diags.warnings, key=lambda d: (d.file, d.code))]
    for e in errors:
        print("  \u2717", e)
    for w in warns[:max_warn]:
        print("  \u26a0", w)
    if len(warns) > max_warn:
        print(f"  \u2026 还有 {len(warns) - max_warn} 条警告（--max-warn 调整）")
    return len(errors)


def cmd_index(args: argparse.Namespace) -> None:
    """全量重建 index.json：内容不变则不落盘、不动 revision。"""
    vault = Path(args.vault).resolve()
    out = Path(args.out).resolve() if args.out else index_path(vault)
    result = build_index(vault, load_previous(out))
    problems = validate_index(result.data)
    if args.stdout:
        json.dump(result.data, sys.stdout, ensure_ascii=False, indent=2)
        print()
    written = False
    if not args.stdout and (result.changed or not out.exists() or args.force):
        write_json_atomic(out, result.data)
        written = True
    s = result.stats
    state = "内容有变化" if result.changed else "内容未变化"
    print(f"节点 {s['nodes']}（stub {s['stubs']}，缺失目标 {s['missing_targets']}），边 {s['edges']}，"
          f"错误 {s['errors']}，警告 {s['warnings']}")
    print(f"revision {result.data['revision']}（{state}），"
          f"{'已写入 ' + str(out.relative_to(vault) if out.is_relative_to(vault) else out) if written else '未写盘'}")
    print("  边分族：" + "，".join(f"{k} {v}" for k, v in s["by_family"].items()))
    errors = print_diagnostics(result.diags, [], args.max_warn)
    for prob in problems:
        print("  \u2717 索引契约：", prob)
    sys.exit(1 if problems or (args.strict and errors) else 0)


def _safe_layout(path: Path) -> dict:
    try:
        doc = load_json(path)
        return doc if isinstance(doc, dict) else {}
    except (ValueError, OSError):
        return {}


def cmd_layout(args: argparse.Namespace) -> None:
    """layout init：按 field / 子目录生成初始布局；layout check：校验引用列出孤立记录。

    `--layout <项目 id>` 查项目画布（`.knowrary/layouts/<项目>.json`），不给就是全局图。
    """
    vault = Path(args.vault).resolve()
    name = getattr(args, "layout", None) or "layout"
    path = layout_path(vault, name)
    index = build_index(vault, load_previous(index_path(vault))).data
    if args.action == "init":
        if path.exists() and not args.force:
            raise SystemExit(f"{path.relative_to(vault)} 已存在（加 --force 重新生成，会丢弃现有位置）")
        keep = _safe_layout(path) if path.exists() else {}
        if name != "layout":
            project = (load_projects(vault).get("projects") or {}).get(name)
            if project is None:
                raise SystemExit(f"没有 `{name}` 这个项目")
            doc = build_project_layout(project, index)
        else:
            doc = stamp(build_initial_layout(index, by=args.by))
        # **重排的是分组和位置，不该顺手把便签 / 贴图 / 引用卡 / 手工拐点一起清掉**——
        # 那些是另一类用户数据，和"按什么分组"没有关系。
        for k in ("refs", "notes", "images", "edges"):
            if keep.get(k):
                doc[k] = keep[k]
        if keep.get("viewport"):
            doc["viewport"] = keep["viewport"]
        write_json_atomic(path, doc)
        carried = ", ".join(f"{k} {len(keep[k])}" for k in ("refs", "notes", "images", "edges")
                            if keep.get(k))
        print(f"已生成 {path.relative_to(vault)}：分组 {len(doc['groups'])}，节点 {len(doc['nodes'])}，"
              f"revision {doc['revision']}" + (f"；保留了 {carried}" if carried else ""))
        return
    if not path.exists():
        raise SystemExit(f"{path.relative_to(vault)} 不存在，先跑 `layout init` 或启动服务")
    doc = load_json(path)
    orphans = find_orphans(doc, index, vault)
    ghosts = [k for k, v in (doc.get("nodes") or {}).items() if (v or {}).get("state") == "ghost"]
    inbox = ([] if name != "layout"
             else sorted({n["id"] for n in index["nodes"] if not n.get("virtual")} - set(doc.get("nodes", {}))))
    print(f"[{name}] revision {doc.get('revision')}，分组 {len(doc.get('groups', {}))}，"
          f"已放置节点 {len(doc.get('nodes', {}))}，幽灵占位 {len(ghosts)}，"
          f"Inbox {len(inbox)}，孤立记录 {len(orphans)}")
    for o in orphans[: args.max_warn]:
        print(f"  ⚠ [{o['kind']}] {o['id']}：{o['reason']}")
    for nid in inbox[:10]:
        print(f"  · Inbox：{nid}")
    for stray in _stray_layouts(vault) if name == "layout" else []:
        print(f"  ⚠ 孤儿画布：{stray} 没有对应的项目（项目删掉了，画布没人管）")
    sys.exit(0)


def _stray_layouts(vault: Path) -> list[str]:
    """`.knowrary/layouts/` 里没有对应项目的画布文件。

    删项目的路径现在会把画布挪进 backup（server/projects.py 的 retire_layouts），
    但**在那之前删掉的项目留下的孤儿还在盘上**，而且谁也不会主动去翻那个目录。
    全局 `layout check` 顺手报一句，是唯一会被跑到的地方。
    """
    folder = vault / ".knowrary" / "layouts"
    if not folder.is_dir():
        return []
    known = set(load_projects(vault).get("projects") or {})
    return sorted(f"{f.parent.name}/{f.name}" for f in folder.glob("*.json") if f.stem not in known)


def _layout_doc(vault: Path, index: dict) -> dict:
    """读 layout.json；还没有就现算一份初始布局（只在内存里用，不落盘）。"""
    path = layout_path(vault)
    return load_json(path) if path.exists() else build_initial_layout(index)


def cmd_review(args: argparse.Namespace) -> None:
    """review due：今天该复习什么；review done <id>：记一次复习（只写 review-log.json）。"""
    vault = Path(args.vault).resolve()
    index = build_index(vault, load_previous(index_path(vault))).data
    if args.action == "done":
        if not args.node:
            raise SystemExit("用法：review done <节点 id>")
        if not any(n["id"] == args.node and not n.get("virtual") for n in index["nodes"]):
            raise SystemExit(f"节点 `{args.node}` 不在索引里")
        entry = record_review(vault, args.node)
        print(f"已记录第 {len(entry['reviews'])} 次复习，下次到期 {entry['next_due']}")
        return
    due = due_nodes(index, load_log(vault))
    print(f"今天（{TODAY}）该复习 {len(due)} 个节点：" if due else f"今天（{TODAY}）没有到期的节点")
    for item in due[: args.max_warn]:
        overdue = f"逾期 {item['overdue_days']} 天" if item["overdue_days"] else "今天到期"
        print(f"  · {item['name']}（{item['id']}）— {overdue}，已复习 {item['reviews']} 次")
    sys.exit(0)


def cmd_digest(args: argparse.Namespace) -> None:
    """图谱欠账清单：Inbox / 草稿 / 待复习 / stub / 跨分组桥 / 连边建议 / 重复候选 / 方向矛盾。"""
    vault = Path(args.vault).resolve()
    index = build_index(vault, load_previous(index_path(vault))).data
    d = build_digest(vault, index, _layout_doc(vault, index))
    c = d["counts"]
    print(f"{d['generated_at']} 的欠账：Inbox {c['inbox']}，草稿 {c['drafts']}"
          f"（放久了 {c['stale_drafts']}），待复习 {c['due']}，stub {c['stubs']}，"
          f"跨分组桥 {c['bridges']}，孤点 {c['lonely']}（整批 {c['lonely_batches']}），"
          f"缺 year {c['no_year']}，连边建议 {c['links']}，"
          f"重复候选 {c['duplicates']}，域/层不符 {c['misplaced']}，框压人 {c['squatted']}，不该上图 {c['off_canvas']}，方向矛盾 {c['cycles']}")
    n = args.top
    for nid in d["inbox"][:n]:
        print(f"  · Inbox：{nid}")
    for item in d["drafts"][:n]:
        print(f"  · 草稿：{item['id']}（放了 {item['days']} 天{'，该定稿了' if item['stale'] else ''}）")
    for item in d["due"][:n]:
        print(f"  · 待复习：{item['id']}（逾期 {item['overdue_days']} 天）")
    for b in d["bridges"][:n]:
        print(f"  · 跨分组桥：{b['from_name']} → {b['to_name']}（{b['count']} 条）")
    if d["lonely"]:
        names = "、".join(x["id"] for x in d["lonely"][:6])
        print(f"  · 孤点：{c['lonely']} 个一条关系都没有（{names}{' …' if c['lonely'] > 6 else ''}）")
    # 整批孤点排在散点后面：它指向的往往不是"还没连"，而是那次导入的边没落盘
    for b in d["lonely_batches"][:n]:
        why = "，多半是那次导入的边没落盘" if b["whole"] else ""
        print(f"  · 整批孤点：《{b['source']}》拆出的 {b['total']} 个里 "
              f"{b['lonely']} 个一条边都没有{why}（{'、'.join(b['ids'][:4])}"
              f"{' …' if b['lonely'] > 4 else ''}）")
    for h in d["links"][:n]:
        alone = "（两端都还是孤点）" if h["lonely"] == 2 else "（有一端是孤点）" if h["lonely"] else ""
        print(f"  · 连边建议：{h['source']} {h['relation']} → {h['target']}{alone} — {h['reason']}")
    for o in d["off_canvas"][:n]:
        print(f"  ⚠ 不该上图：{o['id']}（{o['type']}）还摆在全局画布上 —— "
              f"改 md 不动画布，所以迁类型之后这份旧条目没人清")
    for sq in d["squatted"][:n]:
        who = "、".join(f"{v['id']}（{v['group']}）" for v in sq["victims"][:3])
        print(f"  ⚠ 框压人：「{sq['group_name']}」这个框盖住了别的域的 {sq['count']} 个点：{who}"
              f"{' …' if sq['count'] > 3 else ''} —— 它往里放东西时会避开这些点，"
              f"于是永远放不进去，报出来却是「塞不下了」")
    for m in d["misplaced"][:n]:
        lane = f"{m['field']}/{m['layer']}" if m["layer"] else m["field"]
        tail = "（那条道还没建）" if not m["want_exists"] else ""
        # 域不符和层不符要分开说：一个是「归错了领域」，一个是「领域对但躺错了道」，
        # 前者多半是改过 field 没挪画布，后者多半是摆的时候那条道放不下退回了大框
        what = "域不符" if m.get("why") == "域" else "层不符"
        key = f"field={m['field']}" if m.get("why") == "域" else f"layer={m['layer']}"
        print(f"  · {what}：{m['id']} {key}，却摆在 {m['group_name']} — 该去 {lane}{tail}")
    for x in d["duplicates"][:n]:
        print(f"  · 重复候选：{x['a']} / {x['b']} — {x['reason']}")
    for msg in d["cycles"][:n]:
        print(f"  ⚠ 方向矛盾：{msg}")
    sys.exit(0)


def cmd_check(args: argparse.Namespace) -> None:
    """按规范校验 vault：复用 index 的解析与诊断，额外查密钥泄露与索引契约。"""
    vault = Path(args.vault).resolve()
    result = build_index(vault)
    s = result.stats
    extra = check_secret_leak(vault) + [f"索引契约：{p}" for p in validate_index(result.data)]
    print(f"节点 {s['nodes']}（stub {s['stubs']}），边 {s['edges']}，"
          f"错误 {s['errors'] + len(extra)}，警告 {s['warnings']}"
          + (f"，方向矛盾（环）{s['cycles']} 处" if s.get("cycles") else ""))
    sys.exit(1 if print_diagnostics(result.diags, extra, args.max_warn) else 0)


# ---------------------------------------------------------------- LLM 输出解析

def extract_json(text: str, vault: Path | None = None, context: str = "") -> dict:
    """模型那段话 → dict。容错与留痕在 `core.llmjson`，和服务端同一份。

    **命令行以前是另一套**（只剥首尾围栏、失败直接退出、不进问题流），
    于是同一次模型抽风，网页上查得到、命令行查不到。
    """
    plan = parse_json(text, context, vault=vault)
    if not plan:
        raise SystemExit("LLM 输出中找不到可用的 JSON"
                         + ("（原文已记进 .knowrary/issues.jsonl）" if vault else "：\n" + (text or "")[:800]))
    return plan


# ---------------------------------------------------------------- article（提示词拼装在 core.article）

def apply_plan(plan: dict, vault: Path, target: ImportTarget, dry_run: bool,
               index: dict | None = None) -> Translation:
    """方案 JSON → 三种产物（新建 / 补充老节点 / 待审边），走和网页同一条写回通道。

    以前这里自己渲染文件、自己 write，和 `/api/changes` 是两套写法；现在翻译成 ChangeSet
    交给 `core.plan` / `core.commit`：dry-run 能看到每个文件的 diff，落盘前自动备份，
    补充老节点只追加不覆盖。待审边只在真正落盘时记进 pending.json。

    返回 `Translation`：调用方要拿它算三级匹配（`tr.pending` 是"够到体系"的证据之一）。
    `index` 传进来是为了别重建第二遍——`cmd_article` 早就为了拼提示词建过一次了。
    """
    rt = load_relation_types(vault)
    index = index if index is not None else build_index(vault).data
    tr = translate(plan, index, rt, target)
    try:
        edits = plan_changes(vault, tr.changes, index)
    except (ChangeRejected, WriteConflict) as exc:
        raise SystemExit(f"方案写不进去：{exc}")
    print_import_report(tr, edits, dry_run)
    if dry_run:
        for e in edits:
            print(f"\n{'=' * 70}\n{e.rel}\n{'=' * 70}\n{_diff_text(e)}")
        return tr
    snapshot = commit(vault, edits) if edits else ""
    origin = {"source": target.source, "imported_at": target.date}
    added = add_pending(vault, tr.pending, origin) if tr.pending else []
    home = plan.get("suggest_home")
    if isinstance(home, dict):
        # 只记这次真建出来、又被模型点名孤立的那几个：Inbox 上显示，不自动建域 / 建项目。
        # 和 /api/import 同一条口径——命令行导进来的点不该在 Inbox 上少一半。
        add_home(vault, [i for i in (home.get("isolated") or []) if i in tr.new_ids], home, origin)
    stem = re.sub(r"[^\w一-鿿-]+", "-", target.source)[:60]
    log = vault / ".knowrary" / "imports" / f"{TODAY}-{stem}.json"
    write(log, json.dumps({"plan": plan, "changes": tr.changes, "pending": tr.pending,
                           "warnings": tr.warnings}, ensure_ascii=False, indent=2))
    print(f"\n已写入 {len(edits)} 个文件" + (f"（备份 {snapshot}）" if snapshot else "")
          + f"，待审边 {len(added)} 条；方案存于 {log.relative_to(vault)}")
    return tr


def _diff_text(edit) -> str:
    """新文件给全文，改老文件给统一 diff——和服务端 curation.diff_of 同一口径。"""
    if not edit.before:
        return edit.after
    import difflib
    return "\n".join(difflib.unified_diff(edit.before.splitlines(), edit.after.splitlines(),
                                          fromfile=edit.rel, tofile=edit.rel, lineterm=""))


def print_import_report(tr: Translation, edits: list, dry: bool) -> None:
    c = tr.counts()
    print(f"\n{'(dry-run) ' if dry else ''}新建节点 {c['nodes']} 个，stub {c['stubs']} 个，"
          f"补充老节点 {c['enrich']} 处，直接写入的边 {c['edges']} 条，待审边 {c['pending']} 条")
    if tr.summary:
        print("摘要：", tr.summary)
    for e in edits:
        print("  +" if not e.before else "  ~", e.rel, "｜", "；".join(e.notes[:3]))
    for pe in tr.pending:
        print(f"  ? 待审 {pe['source']} {pe['relation']} → {pe['target']}（置信度 {pe['confidence']:.2f}）")
    for w in tr.warnings:
        print("  ⚠", w)


def cmd_context(args: argparse.Namespace) -> None:
    """给 skill / 人用的上下文：类型表、全部 id、与文章最相关的节点。"""
    vault = Path(args.vault).resolve()
    rt = load_relation_types(vault)
    nodes, _ = load_vault(vault)
    text = read(Path(args.article)) if args.article else ""
    cards = cards_from_nodes(nodes)
    related = select_related(cards, text) if text else []
    # 给了文章就只列可链的子集（和 article 提示词同一份口径）；没给文章时是人在翻全图，照旧全量
    ids = select_linkable(cards, related, args.field or "") if text else sorted(nodes)
    print("## 可用关系类型\n" + rt.describe())
    print(f"\n## 已有节点（图里 {len(nodes)} 个，列出 {len(ids)} 个，链接时必须精确使用）\n"
          + ("、".join(ids) or "(空)"))
    if related:
        print("\n## 与文章最相关的已有节点")
        print("\n".join(describe_related(related)))


def cmd_apply(args: argparse.Namespace) -> None:
    plan = extract_json(read(Path(args.plan)))
    source = args.source or f"plan {Path(args.plan).stem} {TODAY}"
    apply_plan(plan, Path(args.vault).resolve(), ImportTarget(args.field, source, args.folder), args.dry_run)


def cmd_article(args: argparse.Namespace) -> None:
    """无人值守导入：拼提示词 → 一次 LLM 调用 → 三级匹配 → 翻译写回。

    **和网页那条 `/api/import/propose` 是同一套**：同一份提示词、同一个长度闸、
    同一套认领 / 撞脸 / 孤立判定、同一份 JSON 容错。差别只有出口——
    这边打在终端上，那边进审核卡。
    """
    vault = Path(args.vault).resolve()
    art_path = Path(args.article).resolve()
    rt = load_relation_types(vault)
    index = build_index(vault).data
    try:
        article = check_length(read(art_path))
    except ValueError as exc:
        raise SystemExit(str(exc))
    points = project_points(vault, index, args.project)
    prompt = build_article_prompt(cards_from_index(index), rt, article, args.field, points)
    if args.show_prompt:
        print(prompt)
        return
    cfg, _ = llm_backend.load_config(vault)
    name, provider = llm_backend.resolve_provider(cfg, "learn", args.llm)
    model = args.model or provider.get("model") or "默认模型"
    print(f"图谱 {len(index['nodes'])} 个节点，文章 {len(article)} 字，待认领的清单点 {len(points)} 个，"
          f"调用 LLM {name}（{provider['type']} / {model}）…", file=sys.stderr)
    plan = extract_json(llm_backend.ask(prompt, provider, args.model), vault,
                        f"article {art_path.name}")
    plan, claims = normalize_claims(plan, points, rename_in_plan)
    target = ImportTarget(args.field, f"article {art_path.name} {TODAY}", args.folder)
    tr = apply_plan(plan, vault, target, args.dry_run, index)
    print_match_report(plan, points, claims, tr, index)


def print_match_report(plan: dict, points: list[dict], claims: list[dict], tr: Translation,
                       index: dict) -> None:
    """三级匹配的终端版：认领了谁、哪些名字撞脸、哪几块和体系断开。

    **撞脸和孤立都不替人拍板**，只列出来——认领要改 id、孤立要么补边要么就让它进 Inbox，
    两件事都得人看过才算数。
    """
    claimed = {c["node_id"] for c in claims}
    for c in claims:
        print(f"  ✓ 认领清单点 {c['point_name']}（{c['point_id']}）")
    for n in near_misses(plan, points, claimed):
        print(f"  ≈ {n['node_id']} 和清单点 {n['point_name']}（{n['point_id']}）像 {n['ratio']}，"
              f"是同一个东西的话改成同一个 id")
    lonely = isolated_blocks(plan, tr.pending, index, claimed)
    if lonely:
        print(f"  ○ 和体系断开（会掉进 Inbox）：{'、'.join(lonely)}")


# ---------------------------------------------------------------- llm

def cmd_projects(args: argparse.Namespace) -> None:
    """plans.json（v1，学习计划）→ projects.json（v2，项目）。

    一份计划升成"一个项目 + 一份清单"。**先备份再写**，`--dry-run` 只看不动。
    id 压成 ASCII（它会成为对话留档的目录名），旧 id 记在 `legacy_id` 里，
    对话目录跟着迁。
    """
    vault = Path(args.vault).resolve()
    legacy = legacy_plans_path(vault)
    target = projects_path(vault)
    if target.exists() and not args.force:
        raise SystemExit(f"{target.relative_to(vault)} 已存在（加 --force 覆盖）")
    if not legacy.exists():
        raise SystemExit(f"没有 {legacy.relative_to(vault)}，没什么可迁的")

    doc = upgrade_v1(load_json(legacy))
    index = build_index(vault, load_previous(index_path(vault))).data
    fields = {n.get("field") for n in index["nodes"] if n.get("field")}

    print(f"{len(doc['projects'])} 个项目：")
    for pid, pr in doc["projects"].items():
        n = sum(len(st.get("points") or []) for ls in pr["lists"] for st in ls["stages"])
        old = pr.get("legacy_id")
        tag = f"（原 id `{old}`）" if old != pid else ""
        print(f"  {pid:<14} {pr['name']:<16} {len(pr['lists'])} 份清单 · {n} 个点{tag}")
        if pr.get("field") and pr["field"] not in fields:
            print(f"    ⚠ 领域 `{pr['field']}` 在图里已经不存在了——"
                  f"「建」按钮会算错落脚点，迁完自己改一下（脚本不擅自改）")
    if args.dry_run:
        print("\n--dry-run：什么都没写")
        return

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = vault / ".knowrary" / "backup" / f"migrate-projects-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, root / legacy.name)
    chat_root = vault / ".knowrary" / "chat"
    moved = []
    for pid, pr in doc["projects"].items():                 # 对话留档跟着 id 迁
        old = pr.get("legacy_id")
        src = chat_root / old if old else None
        if src and src.is_dir() and old != pid and not (chat_root / pid).exists():
            src.rename(chat_root / pid)
            moved.append(f"{old} → {pid}")
    save_projects(vault, doc)
    print(f"\n已写入 {target.relative_to(vault)}（备份在 {root.relative_to(vault)}）")
    if moved:
        print("对话目录迁移：" + "、".join(moved))
    print(f"旧的 {legacy.relative_to(vault)} 没有删——确认无误后自己删")


def cmd_llm(args: argparse.Namespace) -> None:
    vault = Path(args.vault).resolve()
    cfg, path = llm_backend.load_config(vault)
    print(llm_backend.describe(cfg, path))
    if path is None:
        example = vault / ".knowrary" / llm_backend.EXAMPLE_NAME
        print(f"提示：复制 {example} 到 {llm_backend.config_path(vault)} 后编辑，即可切换 provider / 模型")
    if args.action == "list":
        return
    # 只取真正的角色，`_说明` 那类注释键不算——否则会拿一整段说明去当 provider 名
    roles = {r: n for r, n in cfg["roles"].items() if not str(r).startswith("_")}
    targets = [args.llm] if args.llm else sorted(set(roles.values()))
    run = _probe_one if args.action == "probe" else _ping_one
    failed = sum(run(name, llm_backend.resolve_provider(cfg, "learn", name)[1], args)
                 for name in targets)
    sys.exit(1 if failed else 0)


def _ping_one(name: str, provider: dict, args: argparse.Namespace) -> int:
    """通不通：一个极短的请求，要求模型只回 OK。返回 1 = 这个 provider 没过。"""
    try:
        reply = llm_backend.ping(provider, args.model)
    except SystemExit as e:
        print(f"  ✗ {name}: {e}")
        return 1
    ok = "OK" in reply.upper()
    print(f"  {'✓' if ok else '⚠'} {name}: {reply[:80]!r}")
    return 0 if ok else 1


def _probe_one(name: str, provider: dict, args: argparse.Namespace) -> int:
    """能不能等：发一个要想很久的请求，看首字节几秒、断在第几秒。

    `test` 测不出 2026-09-21 那类故障——断的不是连通性，是模型静默思考期间连接被掐，
    短请求永远碰不到那堵墙。**首字节那个数是重点**：它卡在一个整数附近（60 秒之类），
    就说明路径上某一跳有空闲超时，跟模型本身没关系。
    """
    row = llm_backend.probe(provider, args.model, args.pad_chars)
    first = f"{row['first_byte']:.1f}s" if row["first_byte"] is not None else "一个字都没到"
    if row["ok"]:
        print(f"  ✓ {name}（{row['model']}）：首字节 {first}，读完 {row['total']:.1f}s，"
              f"{row['chars']} 字")
        return 0
    print(f"  ✗ {name}（{row['model']}）：第 {row['total']:.1f} 秒断了，首字节 {first}"
          f"\n      {row['error']}")
    return 1


# ---------------------------------------------------------------- CLI

def add_data_parsers(sub: argparse._SubParsersAction) -> None:
    """数据层命令：迁移、索引、校验。"""
    v = sub.add_parser("vault", help="旧 vault → 第二版结构")
    v.add_argument("src")
    v.add_argument("dst")
    v.add_argument("--field", default="计算机体系结构", help="迁移节点统一的顶层领域")
    v.add_argument("--force", action="store_true")
    v.set_defaults(fn=cmd_vault)

    i = sub.add_parser("index", help="全量重建 .knowrary/index.json")
    i.add_argument("--vault", required=True)
    i.add_argument("--out", help="输出路径，默认 <vault>/.knowrary/index.json")
    i.add_argument("--stdout", action="store_true", help="只打印 index JSON，不写盘")
    i.add_argument("--force", action="store_true", help="内容未变化也重写文件")
    i.add_argument("--strict", action="store_true", help="有 error 时退出码 1")
    i.add_argument("--max-warn", type=int, default=20)
    i.set_defaults(fn=cmd_index)

    y = sub.add_parser("layout", help="初始布局生成 / 引用校验")
    y.add_argument("action", choices=["init", "check"], nargs="?", default="check")
    y.add_argument("--vault", required=True)
    y.add_argument("--layout", help="项目 id：查那个项目的画布；不给就是全局图")
    y.add_argument("--by", choices=["dir", "layer"], default="dir",
                   help="init 时二级分组按什么分：dir=nodes/ 子目录（默认），layer=抽象层")
    y.add_argument("--force", action="store_true", help="init 时覆盖已有 layout.json")
    y.add_argument("--max-warn", type=int, default=20)
    y.set_defaults(fn=cmd_layout)

    c = sub.add_parser("check", help="按规范校验 vault")
    c.add_argument("vault")
    c.add_argument("--max-warn", type=int, default=40)
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("review", help="到期复习列表 / 记一次复习")
    r.add_argument("action", choices=["due", "done"], nargs="?", default="due")
    r.add_argument("node", nargs="?", help="review done 的节点 id")
    r.add_argument("--vault", required=True)
    r.add_argument("--max-warn", type=int, default=20)
    r.set_defaults(fn=cmd_review)

    g = sub.add_parser("digest", help="图谱欠账清单（Inbox / 草稿 / 待复习 / 桥 / 连边建议 / 重复）")
    g.add_argument("--vault", required=True)
    g.add_argument("--top", type=int, default=5, help="每类最多列几条")
    g.set_defaults(fn=cmd_digest)


def add_llm_parsers(sub: argparse._SubParsersAction) -> None:
    """LLM 链路命令：文章拆节点、上下文、写入、配置。"""
    a = sub.add_parser("article", help="文章 → 节点（LLM）")
    a.add_argument("article")
    a.add_argument("--vault", required=True)
    a.add_argument("--field", required=True, help="这批节点的顶层领域")
    a.add_argument("--folder", help="写入 nodes/ 下的子目录，默认同 field")
    a.add_argument("--project", help="项目 id：把该项目清单里还没建的点给模型认领")
    a.add_argument("--llm", help="临时指定 provider 名（默认用配置里 roles.learn）")
    a.add_argument("--model", help="临时覆盖模型名")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--show-prompt", action="store_true", help="只打印提示词，不调用 LLM")
    a.set_defaults(fn=cmd_article)

    x = sub.add_parser("context", help="输出类型表 / 可链 id / 相关节点（供 skill 使用）")
    x.add_argument("--vault", required=True)
    x.add_argument("--article", help="文章路径，用于筛选相关节点")
    x.add_argument("--field", help="导入目标领域：给了文章时，该领域的节点 id 也会列进可链子集")
    x.set_defaults(fn=cmd_context)

    p = sub.add_parser("apply", help="把方案 JSON 校验后写入 vault（供 skill 使用）")
    p.add_argument("plan")
    p.add_argument("--vault", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--folder")
    p.add_argument("--source", help="写入 frontmatter source 字段，如文章名")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_apply)

    pj = sub.add_parser("projects", help="plans.json → projects.json 迁移")
    pj.add_argument("action", choices=["migrate"], nargs="?", default="migrate")
    pj.add_argument("--vault", required=True)
    pj.add_argument("--dry-run", action="store_true", help="只打印迁成什么样，不写盘")
    pj.add_argument("--force", action="store_true", help="projects.json 已存在也覆盖")
    pj.set_defaults(fn=cmd_projects)

    l = sub.add_parser("llm", help="查看 / 测试 LLM 配置")
    l.add_argument("action", choices=["list", "test", "probe"], nargs="?", default="list")
    l.add_argument("--vault", required=True)
    l.add_argument("--llm", help="只测试这个 provider（默认测试各角色用到的）")
    l.add_argument("--model")
    l.add_argument("--pad-chars", type=int, default=20000,
                   help="probe 用多长的上下文把静默期撑起来（默认 2 万字，约等于出事那轮的量级）")
    l.set_defaults(fn=cmd_llm)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_data_parsers(sub)
    add_llm_parsers(sub)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
