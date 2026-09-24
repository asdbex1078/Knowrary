"""原文进库：导入时把长文放进原文目录（`vault_config.articles_dir`）。

三种情况（2026-09-23 拍板）：
- **就地引用**：导入的就是原文目录里的一篇 → 不动它，节点直接链过去；
- **复用**：原文目录里已有同名、正文也一模一样的 → 不再存第二份；
- **复制进来**：粘贴的、本地上传的、库里别处的 → 存一份，顶上加 `imported` / `origin`
  两个字段（原文自己有 frontmatter 就并进去），**正文一字不改**。

同名但正文不同就拒绝（`ArticleConflict`），让人改个文章名——不自动加 `-2`，
免得原文目录里慢慢长出一堆长得很像的文件。

这里只算方案（`plan_article`）和写一份文件；什么时候写由调用方定：
预览阶段一个字节都不碰；真写入时**先放原文再过审核**（原文不在的话确定性检查会报一串
「来源指不到原文」），挡下了或节点没写成就撤掉（server/importing.py）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .mdio import read, split_frontmatter, write
from .sections import find_heading, outline
from .sources import article_link
from .vault_config import load_vault_config

RE_BAD_NAME = re.compile(r'[\\/:*?"<>|\n\r\t]+')
MAX_NAME = 80


class ArticleConflict(ValueError):
    """原文目录里已有同名、但正文不一样的另一篇。消息是给人看的：改个文章名再来。"""


@dataclass
class ArticlePlan:
    """这篇原文怎么进库。`action`：`in_place`（就地引用）/ `reuse`（已有一模一样的）/ `copy`（要存一份）。"""

    path: str                          # 库内相对路径，如 articles/BPE全景.md
    action: str
    text: str                          # 原文正文（不含我们加的那两个字段）
    headings: list[str] = field(default_factory=list)

    @property
    def link(self) -> str:
        return article_link(self.path)

    def link_to(self, section: str | None) -> str:
        """链到某一节：模型给的小节名要能在原文里找到（模糊匹配），链接里用原文的原样标题；找不到就链整篇。"""
        hit = find_heading(self.text, section) if section else None
        return f"{self.link[:-2]}#{hit.title}]]" if hit else self.link


def safe_name(name: str) -> str:
    """文章名 → 文件名：去掉路径分隔符和 Windows 不认的字符，截短。"""
    clean = RE_BAD_NAME.sub("-", (name or "").strip()).strip(" .-")
    return clean[:MAX_NAME] or "未命名文章"


def _body(text: str) -> str:
    """比较"是不是同一篇"时只看正文：我们加的 imported / origin 不算，行尾空白不算。"""
    try:
        _, body = split_frontmatter(text)
    except ValueError:
        body = text
    return "\n".join(ln.rstrip() for ln in body.strip().splitlines())


def plan_article(vault: Path, name: str, text: str, file: str | None = None) -> ArticlePlan:
    """算出这篇原文该怎么进库。不写盘。`file` 是库内路径（从「仓库里的文件」选的那种）。"""
    root = load_vault_config(vault)["articles_dir"]
    if file and (file == root or file.startswith(root + "/")):
        return ArticlePlan(path=file, action="in_place", text=text, headings=_titles(text))
    rel = f"{root}/{safe_name(name)}.md"
    target = vault / rel
    if target.exists():
        if _body(read(target)) != _body(text):
            raise ArticleConflict(f"原文目录里已经有一篇 `{rel}`，内容和这篇不一样——换个文章名再导入")
        return ArticlePlan(path=rel, action="reuse", text=text, headings=_titles(text))
    return ArticlePlan(path=rel, action="copy", text=text, headings=_titles(text))


def _titles(text: str) -> list[str]:
    return [h.title for h in outline(text) if h.level > 1]


def render_article(text: str, origin: str, date: str) -> str:
    """复制进来的那一份：顶上补 `imported` / `origin`，正文原样。原文已有 frontmatter 就插进去，
    **不重新序列化**它——人手写的 frontmatter 格式、注释、顺序都原样留着。已有同名字段不覆盖。"""
    stamp = {"imported": date, "origin": origin or "粘贴"}
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if m:
        try:
            have, _ = split_frontmatter(text)
        except ValueError:
            have = {}
        extra = "".join(f"{k}: {_scalar(v)}\n" for k, v in stamp.items() if k not in have)
        return f"---\n{m.group(1)}\n{extra}---\n{text[m.end():]}"
    head = "".join(f"{k}: {_scalar(v)}\n" for k, v in stamp.items())
    return f"---\n{head}---\n{text}"         # 原样接上：不补空行、不去开头的空白


def _scalar(v: str) -> str:
    """frontmatter 里的值：带冒号、井号这类会被 YAML 误读的，加引号。"""
    s = str(v)
    return f'"{s}"' if re.search(r'[:#\[\]{}"\',&*?|<>=!%@`]', s) or s != s.strip() else s


def write_article(vault: Path, plan: ArticlePlan, origin: str, date: str) -> bool:
    """需要存的才存（`copy`）。返回这次是否真新建了文件——写失败回滚时只删自己建的那份。"""
    if plan.action != "copy":
        return False
    target = vault / plan.path
    target.parent.mkdir(parents=True, exist_ok=True)
    write(target, render_article(plan.text, origin, date))
    return True
