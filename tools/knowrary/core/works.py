"""成品（HTML 深度文档）→ 节点能链的来源 + 能喂给模型的纯文本。

成品是在学习现场（通常是别的仓库）让模型产出的交互式 HTML（讲解页 / 实验台 / 演示器），
**由人自己整理进成品目录**（vault_config 的 `works_dir`）。导入拆点时「成品即原文」
（2026-10-22 拍板）：不往 articles/ 存剥过的副本，节点 sources 直接链 `[[works/….html#锚点]]`。

抽取零第三方依赖（html.parser）：剥掉 script/style/svg 这些不可读的部分，标题降成
Markdown `#` 行——`from_section` 的口径就能和 md 原文共用 `find_heading`；同时记下每个
标题的页面锚点（标题自己的 id，或最近一层带 id 的祖先容器），写 sources 时把小节名换成锚点。
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from .articles import ArticlePlan
from .mdio import read
from .sections import outline
from .vault_config import load_vault_config

WORK_SUFFIXES = (".html", ".htm")
# 整段剥掉的标签：对「读」没有贡献（代码、样式、图形、交互脚手架）
SKIP = {"script", "style", "svg", "noscript", "template", "head", "iframe", "canvas", "object", "datalist"}
HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# 各自成块的标签：开闭时把攒着的文字冲成一行（行内标签如 span/a/code/b 不在这儿，照常接着拼）
BLOCKS = {"p", "div", "section", "article", "li", "ul", "ol", "pre", "blockquote", "tr", "table",
          "figure", "figcaption", "header", "footer", "main", "aside", "nav", "dt", "dd", "dl",
          "details", "summary", "form", "fieldset"}
# 没有闭合标签的：不进栈，否则栈会越积越歪
VOID = {"br", "hr", "img", "input", "meta", "link", "source", "track", "wbr", "area", "base", "col", "embed"}


class WorkRejected(ValueError):
    """这个文件不能当成品导入。消息是给人看的：成品要自己整理进成品目录。"""


class _Extract(HTMLParser):
    """HTML → 行块 + {标题: 锚点}。对不配平的标签尽量宽容：找不到对应开标签的闭标签直接忽略。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.stack: list[tuple[str, str, bool]] = []    # (tag, id, 是否在被剥的区域里)
        self.skip = 0
        self.anchors: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in VOID:
            if not self.skip and tag in ("br", "hr"):
                self._flush()
            return
        a = dict(attrs)
        skipped = tag in SKIP or "hidden" in a or a.get("aria-hidden") == "true"
        self.stack.append((tag, a.get("id") or "", skipped))
        if skipped:
            self.skip += 1
        elif not self.skip and (tag in BLOCKS or tag in HEADING_LEVEL):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        i = next((j for j in range(len(self.stack) - 1, -1, -1) if self.stack[j][0] == tag), None)
        if i is None:
            return
        popped = self.stack[i:]
        del self.stack[i:]
        self.skip -= sum(1 for _, _, skipped in popped if skipped)
        if self.skip:
            return
        if tag in HEADING_LEVEL:
            text = " ".join(" ".join(self.buf).split())
            self.buf = []
            if text:
                near = (popped[0][1], *[fid for _, fid, _ in reversed(self.stack)])
                anchor = next((x for x in near if x), "")
                if anchor:
                    self.anchors.setdefault(text, anchor)
                self.lines.append(f"{'#' * HEADING_LEVEL[tag]} {text}")
        elif tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.skip and data:
            self.buf.append(data)

    def _flush(self) -> None:
        text = " ".join(" ".join(self.buf).split())
        self.buf = []
        if text:
            self.lines.append(text)

    def result(self) -> tuple[str, dict[str, str]]:
        self._flush()
        return "\n\n".join(self.lines), self.anchors


def extract_work(html: str) -> tuple[str, dict[str, str]]:
    """HTML → (Markdown 味的纯文本, {小节标题: 页面锚点 id})。"""
    p = _Extract()
    p.feed(html)
    p.close()
    return p.result()


def work_text(path: Path) -> str:
    """读一个成品，只要文字。导入面板预览、拼提示词都用它。"""
    return extract_work(read(path))[0]


def plan_work(vault: Path, rel: str) -> ArticlePlan:
    """成品怎么进导入：永远就地引用（它本来就在库里），节点 sources 链 `[[成品#锚点]]`。
    不在成品目录里的 html 直接拒——成品要自己整理进去，这是成品层的铁则。"""
    clean = (rel or "").strip().strip("/")
    root = load_vault_config(vault)["works_dir"]
    if not clean.lower().endswith(WORK_SUFFIXES):
        raise WorkRejected(f"`{clean}` 不是 html，当不了成品")
    if not clean.startswith(root + "/"):
        raise WorkRejected(f"`{clean}` 不在成品目录 `{root}/` 里：成品要自己整理进去再导入"
                           "（库不自动同步别的仓库里的活文件）")
    path = vault / clean
    if not path.is_file():
        raise WorkRejected(f"成品不存在：{clean}")
    text, anchors = extract_work(read(path))
    return ArticlePlan(path=clean, action="in_place", text=text,
                       headings=[h.title for h in outline(text) if h.level > 1], anchors=anchors)
