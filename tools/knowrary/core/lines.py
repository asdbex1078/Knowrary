"""流派 / 领域线的归属字段（2026-10-12 归属整理第 ③ 项）。

以前一个点属于哪派，靠它 md 里的 `- 属于:: [[CISC]]` 边挂上去：流派是一种「特殊节点」，
归属藏在关系列表里，跟依赖、演化这些真正的知识边混在一起，检查器里也没法直接勾。
现在归属是**点自己的 frontmatter 字段**：

    流派: ["[[CISC]]"]
    领域线: ["[[NLP]]", "[[CV]]"]       # 写在前面的是主道（真身画在那条道上）

- 值写成 `[[链接]]`：改名工具会改写文件里所有 `[[旧名]]`（frontmatter 也算），
  纯文字的话流派一改名就悄悄悬空（对比组那边吃过这个亏）。读的时候纯文字也认。
- 流派文档（`fields/流派/CISC.md`）还在：它是说明页，也是 `start_year` / `end_year` 的家。
- 原来边上的注释（「这一派的语言载体」）迁到正文末尾 `## 归属` 小节，一派一行——
  入库不许精简，注释是他写的话。

旧写法（点里的 `- 属于::`、流派文档里的 `- 包含::`）照读，所以没迁的库不会坏；
`plan_migration` / `apply_migration` 把旧写法一次迁成字段。
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

from .mdio import RE_LINK, read, split_frontmatter, write

SCHOOL_KEY = "流派"
DOMAIN_KEY = "领域线"
KEY_OF = {"流派": SCHOOL_KEY, "领域线": DOMAIN_KEY}       # 线的 type → 点上的字段名
RE_MEMBER = re.compile(r"^\s*[-*]\s*(?P<type>属于|包含)\s*::\s*\[\[(?P<target>[^\]|#]+)(?:[^\]]*)\]\](?P<rest>.*)$")
RE_NOTE = re.compile(r"^\s*(?:\(\d{3,4}\))?\s*(?:—|--|-)\s*(?P<note>.+?)\s*$")


def line_names(value) -> list[str]:
    """frontmatter 里的 `流派` / `领域线` → 名字列表。认 `[[CISC]]`、`CISC`、单个字符串、列表。"""
    # Obsidian 里不加引号写 `流派: [[CISC]]`，YAML 读出来是嵌套列表 [["CISC"]]——拍平了照认
    items = _flat(value)
    out = []
    for item in items:
        text = str(item or "").strip()
        hit = RE_LINK.search(text)
        name = (hit.group(1) if hit else text).strip()
        if name and name not in out:
            out.append(name)
    return out


def _flat(value) -> list:
    if isinstance(value, list):
        return [x for v in value for x in _flat(v)]
    return [value] if value else []


# ---------------------------------------------------------------- 一次性迁移

def plan_migration(vault: Path, index: dict) -> list[dict]:
    """把旧写法迁成字段：返回 `[{path, before, after, moved: [{line, note}]}]`，不写盘。

    - 点里的 `- 属于:: [[线]]`：删掉这一行，线按书写顺序进字段（顺序 = 主道口径，不能乱）；
    - 线文档里的 `- 包含:: [[点]]`：删掉这一行，线追加到那个点的字段末尾；
    - 边上的注释进 `## 归属` 小节。
    """
    by_id = {n["id"]: n for n in index["nodes"]}
    lines = {n["id"]: n for n in index["nodes"] if n.get("type") in KEY_OF and not n.get("virtual")}
    if not lines:
        return []
    names = _name_lookup(lines)
    adds: dict[str, list[tuple[str, str]]] = {}         # 点的路径 → [(线 id, 注释)]
    drops: dict[str, set[int]] = {}                     # 文件路径 → 要删的行号
    for n in index["nodes"]:
        if n.get("virtual") or not n.get("path"):
            continue
        for i, line in enumerate(read(vault / n["path"]).splitlines()):
            hit = RE_MEMBER.match(line)
            target = names.get(hit.group("target").strip().lower()) if hit else None
            if not hit:
                continue
            if hit.group("type") == "属于" and target and n["id"] not in lines:
                adds.setdefault(n["path"], []).append((target, _note(hit.group("rest"))))
                drops.setdefault(n["path"], set()).add(i)
            elif hit.group("type") == "包含" and n["id"] in lines:
                member = by_id.get(hit.group("target").strip())
                if member and member.get("path") and member["id"] not in lines:
                    adds.setdefault(member["path"], []).append((n["id"], _note(hit.group("rest"))))
                    drops.setdefault(n["path"], set()).add(i)
    out = []
    for path in sorted(set(adds) | set(drops)):
        before = read(vault / path)
        after = _rewrite(before, drops.get(path, set()), adds.get(path, []), lines)
        if after != before:
            out.append({"path": path, "before": before, "after": after,
                        "moved": [{"line": lid, "note": note} for lid, note in adds.get(path, [])]})
    return out


def apply_migration(vault: Path, plan: list[dict]) -> str:
    """写前整份快照到 `.knowrary/backup/lines-<时间>/`，再逐个写。返回快照目录（库内相对路径）。"""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = vault / ".knowrary" / "backup" / f"lines-{stamp}"
    for item in plan:
        dst = root / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vault / item["path"], dst)
    for item in plan:
        write(vault / item["path"], item["after"])
    return root.relative_to(vault).as_posix()


def _name_lookup(lines: dict) -> dict[str, str]:
    out = {}
    for lid, n in lines.items():
        for key in (lid, n.get("name"), *(n.get("aliases") or [])):
            if key:
                out.setdefault(str(key).strip().lower(), lid)
    return out


def _note(rest: str) -> str:
    hit = RE_NOTE.match(rest or "")
    return hit.group("note").strip() if hit else ""


def _rewrite(text: str, drop: set[int], add: list[tuple[str, str]], lines: dict) -> str:
    rows = [ln for i, ln in enumerate(text.splitlines()) if i not in drop]
    out = "\n".join(rows) + ("\n" if text.endswith("\n") else "")
    if add:
        out = _add_fields(out, add, lines)
        out = _add_notes(out, [(lines[lid].get("name") or lid, note) for lid, note in add if note])
    return out


def _add_fields(text: str, add: list[tuple[str, str]], lines: dict) -> str:
    """frontmatter 里补 `流派` / `领域线`：已有的值保留在前，新的按书写顺序接在后面。

    不整份重新 dump frontmatter（那会把他手排的键序、引号风格全改掉，diff 一大片），
    只动这两个键所在的行：有就替换那一行（连同它下面的块列表），没有就接在最后一个键后面。
    """
    fm, _ = split_frontmatter(text)
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    head, rest = (m.group(1), text[m.end():]) if m else ("", text)
    for kind, key in KEY_OF.items():
        mine = [lid for lid, _ in add if lines[lid].get("type") == kind]
        if not mine:
            continue
        have = line_names(fm.get(key))
        merged = have + [lid for lid in dict.fromkeys(mine) if lid not in have]
        row = f"{key}: [" + ", ".join(f'"[[{x}]]"' for x in merged) + "]"
        pat = re.compile(rf"^{re.escape(key)}\s*:.*(?:\n[ \t]+-.*)*", re.M)
        head = pat.sub(lambda _: row, head, count=1) if pat.search(head) else (f"{head}\n{row}" if head else row)
    return f"---\n{head}\n---\n{rest}"


def _add_notes(text: str, notes: list[tuple[str, str]]) -> str:
    """注释进 `## 归属`：放在 `## 关系` 前面；已经有这一节就接在它末尾。"""
    if not notes:
        return text
    rows = [f"- {name}：{note}" for name, note in notes]
    if re.search(r"^## 归属\s*$", text, re.M):
        return re.sub(r"(^## 归属\s*\n(?:(?!^## ).*\n?)*)", lambda m: m.group(1).rstrip("\n") + "\n" + "\n".join(rows) + "\n\n",
                      text, count=1, flags=re.M)
    block = "## 归属\n" + "\n".join(rows) + "\n\n"
    if re.search(r"^## 关系\s*$", text, re.M):
        return re.sub(r"^## 关系\s*$", lambda m: block + m.group(0), text, count=1, flags=re.M)
    return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"
