"""按标题切正文：给 `read_node` 的 `section` 参数用，也给截断时的目录用。

长笔记（上万字）整篇读会被 READ_CHARS 截断，模型看到的是前半篇；有了目录和按节读，
它就能先看目录、再只读要改的那一节，不用为一段话把整篇背进上下文。
代码块里的 `# 注释` 不是标题，必须跳过——Prompt-Caching 那篇里就有好几行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RE_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
RE_FENCE = re.compile(r"^\s*(```|~~~)")
# 匹配标题时忽略的字符：空白、标点、装饰符号——「案例 3」和「案例3【…】」应该能对上
RE_NOISE = re.compile(r"[\s#*`_「」『』【】\[\]（）()“”\"'：:、，,。.；;!！?？—-]+")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int          # 0 起的行号


def outline(text: str) -> list[Heading]:
    """正文里的标题（跳过 fenced code）。"""
    heads: list[Heading] = []
    fence = None
    for i, ln in enumerate(text.splitlines()):
        m = RE_FENCE.match(ln)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark == fence:
                fence = None
            continue
        if fence is not None:
            continue
        h = RE_HEADING.match(ln)
        if h:
            heads.append(Heading(len(h.group(1)), h.group(2).strip(), i))
    return heads


def describe_outline(text: str, max_level: int = 3, limit: int = 40) -> str:
    """给模型看的目录：默认只到三级，一行一个，按层级缩进。"""
    rows = [f"{'  ' * (h.level - 1)}- {h.title}" for h in outline(text) if 1 < h.level <= max_level]
    if len(rows) > limit:
        rows = rows[:limit] + [f"  …（还有 {len(rows) - limit} 个标题没列）"]
    return "\n".join(rows)


def _norm(s: str) -> str:
    return RE_NOISE.sub("", s).lower()


def find_heading(text: str, query: str) -> Heading | None:
    """先找标题完全一致的，再找包含的；都按去掉标点空白后比。"""
    q = _norm(query)
    if not q:
        return None
    heads = [h for h in outline(text) if h.level > 1]
    exact = [h for h in heads if _norm(h.title) == q]
    if exact:
        return exact[0]
    partial = [h for h in heads if q in _norm(h.title)]
    return partial[0] if partial else None


def extract_section(text: str, query: str) -> tuple[Heading, str] | None:
    """按标题取一节：从该标题起，到下一个同级或更高级标题之前（子节一起带上）。"""
    head = find_heading(text, query)
    if head is None:
        return None
    lines = text.splitlines()
    end = len(lines)
    for h in outline(text):
        if h.line > head.line and h.level <= head.level:
            end = h.line
            break
    return head, "\n".join(lines[head.line:end]).rstrip()
