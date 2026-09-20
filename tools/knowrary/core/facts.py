"""`## 速查` 与 `## 对比项`：节点正文里可入表的那部分（《横向对比规范》3）。

和 `sections.py` 的分工：那边按标题切文本，这边把切出来的那一节读成结构化的键值。
纯函数，不碰文件，也不碰 index——取数规则在上层（`compare_table`），这里只负责
"这一节里写了什么"。

两节各有各的形状，别混：

- **`## 速查`** 是**属性**：`- 核心方法:: 子词切分` 这样的键值对，键取自对比组的
  `dimensions`，值一句话。它是对比表格的一格。
- **`## 对比项`** 是**差异**：`### 与 [[jieba]]` 这样按目标分的小节。一句话结论写在
  `对比` 边的说明里，这一节只放展开不下的长版本。

v1 方案把两者装进同一个 `## 对比项`，于是键空间自相矛盾（"与RNN对比" vs "年份"），
按维度取数永远取不到东西。拆开是这一版最实质的改动。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .mdio import RE_LINK
from .sections import Heading, outline

FACTS_HEADING = "速查"
COMPARE_HEADING = "对比项"

# 这些维度的真相在 frontmatter，**不许在 `## 速查` 里再写一份**。
# 双源一定会漂，而且错的 year 比空的 year 难发现——它把节点摆到时间轴上一个
# 看起来很正常的位置。取数时按这张表回 frontmatter 拿，写在正文里的会被 check 警告。
FM_DIMENSIONS = {"年份": "year", "参数量": "params", "抽象层": "layer"}

# `- 键:: 值`。用 `::` 而不是单冒号：规范 1.4 要求兼容 Dataview 内联字段，
# 而它不会被关系解析器误读（那边只切 `## 关系` 那一段）。
RE_FACT = re.compile(r"^\s*[-*]\s+(?P<key>[^:：]+?)\s*::\s*(?P<value>\S.*?)\s*$")
# 写成单冒号 / 中文冒号的：这是最容易犯的错，必须能认出来才好提示，不能当没看见
RE_FACT_LOOSE = re.compile(r"^\s*[-*]\s+(?P<key>[^:：]+?)\s*[:：]\s*(?P<value>\S.*?)\s*$")


@dataclass(frozen=True)
class Fact:
    key: str
    value: str


def section_bounds(text: str, title: str) -> tuple[int, int] | None:
    """这一节占正文的哪几行：`[标题行, 下一个同级标题行)`，0 起。没有就返回 None。

    **只认完全一致的标题**，不走 `find_heading` 的"包含"回退。这两个标题是规范定死的，
    模糊匹配只会帮倒忙：`## 快速查阅` 里含着"速查"、`## 对比项目清单` 里含着"对比项"，
    被当成锚点之后解析出来的东西看着像真的。

    返回行号而不是文本，是因为 `set_fact` 要**改其中一行**：拿文本回去再查找替换，
    正文里恰好有同样一行时就会改错地方。
    """
    heads = outline(text or "")
    for i, h in enumerate(heads):
        if h.level < 2 or h.title.strip() != title:
            continue
        end = next((x.line for x in heads[i + 1:] if x.level <= h.level),
                   len((text or "").splitlines()))
        return h.line, end
    return None


def _section(text: str, title: str) -> tuple[Heading, str] | None:
    span = section_bounds(text, title)
    if span is None:
        return None
    lines = (text or "").splitlines()
    head = next(h for h in outline(text or "") if h.line == span[0])
    return head, "\n".join(lines[span[0]:span[1]]).rstrip()


def parse_facts(section_text: str) -> list[Fact]:
    """一节文本里的所有 `- 键:: 值`，按出现顺序。标题行和散文行都跳过。"""
    out = []
    for ln in (section_text or "").splitlines():
        m = RE_FACT.match(ln)
        if m:
            out.append(Fact(m.group("key").strip(), m.group("value").strip()))
    return out


def facts_of(text: str) -> dict[str, str]:
    """节点正文（`Node.body` 或整篇原文都行）→ `## 速查` 里的 {键: 值}。没有这一节就是空字典。

    **重复键保留第一条**：后写的那条多半是复制粘贴忘了改，静默用后者会让你
    在表格里看到一个自己没印象写过的值。重复本身由 `stray_facts` 报出来。
    """
    hit = _section(text, FACTS_HEADING)
    if hit is None:
        return {}
    out: dict[str, str] = {}
    for f in parse_facts(hit[1]):
        out.setdefault(f.key, f.value)
    return out


def stray_facts(text: str) -> tuple[list[str], list[str]]:
    """`## 速查` 里写得不对的地方：(格式不对的行, 重复的键)。

    格式不对主要指单冒号（`- 核心方法: 子词切分`）——它在 Dataview 里不是内联字段，
    取数时会被整行忽略，而你看着它明明写了。空行、子标题、普通散文不算错。
    """
    hit = _section(text, FACTS_HEADING)
    if hit is None:
        return [], []
    bad, seen, dup = [], set(), []
    for ln in hit[1].splitlines():
        if RE_FACT.match(ln):
            key = RE_FACT.match(ln).group("key").strip()
            (dup.append(key) if key in seen else seen.add(key))
            continue
        if RE_FACT_LOOSE.match(ln):
            bad.append(ln.strip())
    return bad, dup


def compare_targets(text: str) -> list[str]:
    """`## 对比项` 下每个 `###` 小节标题里链接到的目标 id，按出现顺序去重。

    标题写成 `### 与 [[jieba]]`：必须是 `[[链接]]`，纯文字标题取不到目标，
    也就没法和 `对比` 边对上——那条 check 正是靠这个函数判的。
    """
    hit = _section(text, COMPARE_HEADING)
    if hit is None:
        return []
    head, body = hit
    out: list[str] = []
    for h in outline(body):
        if h.level <= head.level:
            continue
        for link in RE_LINK.findall(h.title):
            if link not in out:
                out.append(link)
    return out


def bare_compare_headings(text: str) -> list[str]:
    """`## 对比项` 下没写 `[[链接]]` 的小节标题——对不上边，也就进不了任何视图。"""
    hit = _section(text, COMPARE_HEADING)
    if hit is None:
        return []
    head, body = hit
    return [h.title for h in outline(body)
            if h.level > head.level and not RE_LINK.findall(h.title)]
