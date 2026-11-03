"""术语表解析：`术语/*.md` → 一张合并的名词解释表（边学边记，2026-10-27）。

约定见设计文档《字段与术语》。要点：
- `术语/` 一个文件夹，**一个文件一类语境**：`_全局.md` 是全局，别的文件名即语境标签
  （多半取专题名）。文件即语境，省得每条都写 `语境::`。
- 一条义项一块：`## 词` 作词条（**同词多义 = 多个同名块**），正文第一段是解释；
  `- 语境::`（覆盖文件默认）、`- 同::`（别名/缩写，逗号/顿号分隔，行内匹配时一起认）、
  `- 详见::`（可选，链到知识点 `[[id]]`）。
- 合并时**不去重、保留各自出处**——语境只影响排序 / 高亮，从不隐藏。记错了语境也照样查得到，
  只是弹窗上标明出处（容错：认知有限分错也不丢）。
- 和 index.json 一样是**派生缓存**，术语真相只在 md。`术语/` 不在 `nodes/`、`fields/` 下，
  不会被当成知识点（见 mdio.walk_md）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .mdio import RE_LINK, read, write

GLOSSARY_DIR = "术语"
GLOBAL_SCOPE = "全局"
# 这些文件名都当「全局」：下划线开头是约定的"非专题"前缀，中英文各留一个别名
GLOBAL_FILES = ("_全局", "全局", "_global", "_glossary")

RE_FIELD = re.compile(r"^-\s*(语境|同|详见)\s*::\s*(.+?)\s*$")


def scope_of_file(stem: str) -> str:
    """文件名 → 默认语境。`_全局` 等 → 全局；别的文件名原样当语境标签（多半是专题名）。"""
    return GLOBAL_SCOPE if stem in GLOBAL_FILES else stem


def _split_aka(value: str) -> list[str]:
    """`同::` 的值拆成别名列表：逗号（中英文）/ 顿号分隔，去空去重保序。"""
    parts = [p.strip() for p in re.split(r"[,，、]", value)]
    return [p for p in dict.fromkeys(parts) if p]


def parse_glossary(text: str, default_scope: str = GLOBAL_SCOPE) -> list[dict]:
    """一份术语文件的正文 → 义项列表。每条 `{term, gloss, scope, aka[], see}`。

    `default_scope` 是这个文件的语境（由文件名定），逐条可被 `- 语境::` 覆盖。
    """
    entries: list[dict] = []
    cur: dict | None = None
    gloss_lines: list[str] = []

    def flush() -> None:
        if cur is None:
            return
        cur["gloss"] = "\n".join(gloss_lines).strip()
        entries.append(cur)

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## "):
            flush()
            cur = {"term": stripped[3:].strip(), "gloss": "", "scope": default_scope, "aka": [], "see": None}
            gloss_lines = []
            continue
        if cur is None:
            continue                       # 第一个 `## 词` 之前的（文件标题 / 说明）忽略
        field = RE_FIELD.match(line)
        if field:
            key, val = field.group(1), field.group(2).strip()
            if key == "语境":
                cur["scope"] = val or default_scope
            elif key == "同":
                cur["aka"] = _split_aka(val)
            elif key == "详见":
                link = RE_LINK.search(val)
                cur["see"] = (link.group(1).strip() if link else val) or None
            continue
        gloss_lines.append(line)
    flush()
    return [e for e in entries if e["term"]]


def glossary_files(vault: Path) -> list[Path]:
    """`术语/*.md` 的文件列表（跳过隐藏 / 临时 / README）。

    **索引指纹要带上它们**：`walk_md` 只扫 `nodes/`、`fields/`，不含 `术语/`，
    光靠它的话改术语不会触发索引重建，写进去的词刷不出来（见 index_service.fingerprint）。
    """
    root = vault / GLOSSARY_DIR
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.md")
                  if not (p.name.startswith((".", "~$")) or p.name == "README.md" or p.name.endswith(".tmp.md")))


def load_glossary(vault: Path) -> list[dict]:
    """`vault/术语/*.md` 全读出来合并成一张表；文件名定各文件的默认语境。

    没有 `术语/` 文件夹就返回空列表——这个特性纯加法，不建术语就当没有。
    """
    out: list[dict] = []
    for p in glossary_files(vault):
        out.extend(parse_glossary(read(p), scope_of_file(p.stem)))
    return out


class GlossaryRejected(Exception):
    """加词被拒（词条为空等）。"""


RE_UNSAFE = re.compile(r'[/\\:*?"<>|]')


def file_for_scope(scope: str) -> str:
    """语境 → `术语/` 下的文件名 stem。全局（或空）→ `_全局`；别的原样。

    清掉路径分隔符、再去首尾的点和空格——`..` 开头会被当隐藏文件读不回来，`/` 能往外写。
    """
    name = (scope or "").strip()
    if not name or name == GLOBAL_SCOPE:
        return GLOBAL_FILES[0]
    return RE_UNSAFE.sub("", name).strip(" .") or GLOBAL_FILES[0]


def _term_block(term: str, gloss: str, aka: list[str], see: str | None) -> str:
    """拼一块 `## 词` + 解释 + `- 同:: / - 详见::`。语境不写进块里——文件即语境。"""
    out = [f"## {term}"]
    if gloss.strip():
        out.append(gloss.strip())
    if aka:
        out.append(f"- 同:: {', '.join(aka)}")
    if see:
        out.append(f"- 详见:: [[{see}]]")
    return "\n".join(out) + "\n"


def add_term(vault: Path, term: str, gloss: str = "", scope: str = GLOBAL_SCOPE,
             aka: list[str] | None = None, see: str | None = None) -> dict:
    """追加一条义项到 `术语/<语境>.md`（没有就建）。**只追加**，不动已有内容，不做并发控制。

    返回这条义项（和 parse_glossary 出来的同形），前端拿去乐观上屏 / 存完刷新。
    """
    name = " ".join(str(term or "").split())      # 词条压成一行、去空
    if not name:
        raise GlossaryRejected("词条不能是空的")
    aka = [a.strip() for a in (aka or []) if a and a.strip()]
    see = (see or "").strip() or None
    stem = file_for_scope(scope)
    path = vault / GLOSSARY_DIR / f"{stem}.md"
    old = read(path) if path.is_file() else ""
    block = _term_block(name, str(gloss or ""), aka, see)
    write(path, (old.rstrip() + "\n\n" if old.strip() else "") + block)
    return {"term": name, "gloss": str(gloss or "").strip(), "scope": scope_of_file(stem), "aka": aka, "see": see}
