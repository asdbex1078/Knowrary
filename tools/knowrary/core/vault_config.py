"""库级配置（`<库>/.knowrary/vault.json`）：**这个库长什么样**，跟着库进 git。

和两份用户级配置的分界（2026-09-23）：
- `~/.knowrary/settings.json` 是「我想要什么行为」（复习开关、审核开关），跟人走，换库不变；
- `~/.knowrary/config.json` 是「这台机器上用哪个库」；
- 这一份是「这个库的东西放在哪」。它必须跟库走——原文目录要是记在用户级，
  库推给别人之后，节点上的 `sources: [[…]]` 在他那儿全部指向空。

**只存库内相对路径**（「把机器名从仓库里摘出去」那条纪律）。原文目录不许落进
`nodes/` / `fields/`：那两处的 md 全被当成节点扫进图，原文就成了图上的一个点。
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path, PurePosixPath

from .mdio import NODE_DIRS, load_json, write_json_atomic

SCHEMA_VERSION = 1
DEFAULTS = {
    "articles_dir": "articles",     # 原文层：长文原样放这儿，不上图、不进复习
}
# 原文目录不能放的地方：节点层（会被扫成节点）、机器区、附件区、版本库与编辑器的目录
RESERVED = ("nodes", "fields", ".knowrary", "assets", ".git", ".obsidian")
CANDIDATE_DEPTH = 2
CANDIDATE_MAX = 60


class VaultConfigRejected(ValueError):
    """配置值不能用。消息是给人看的：哪里不对、该怎么填。"""


class ArticlesNeedMove(VaultConfigRejected):
    """换目录时旧目录里还有原文，而调用方没说要搬。不是"填错了"，是"得先答一句"——接口层转 409。"""

    def __init__(self, old: str, count: int):
        super().__init__(f"旧目录 `{old}/` 里还有 {count} 篇原文。要改就一起搬过去，"
                         "不然这批文章在界面上就认不出是原文了")
        self.old, self.count = old, count


def config_path(vault: Path) -> Path:
    return vault / ".knowrary" / "vault.json"


def load_vault_config(vault: Path) -> dict:
    """读库级配置。文件不在、读坏了、值不合法，一律回落默认——
    **这份文件坏掉不该让库打不开**，最差也就是原文回到默认的 `articles/`。"""
    out = dict(DEFAULTS)
    path = config_path(vault)
    if path.exists():
        try:
            data = load_json(path)
        except (ValueError, OSError):
            data = {}
        if isinstance(data, dict):
            try:
                out["articles_dir"] = normalize_articles_dir(vault, data.get("articles_dir"))
            except VaultConfigRejected:
                pass
    return out


def normalize_articles_dir(vault: Path, raw) -> str:
    """把人填的路径收成库内相对的 posix 路径，不合法就抛 VaultConfigRejected。"""
    text = str(raw or "").strip().replace("\\", "/")
    # 先判绝对路径再剥斜杠：反过来的话 `/tmp/x` 剥完成了库里的 `tmp/x`，还真给建出来
    if text.startswith(("/", "~")) or ":" in text.split("/")[0]:
        raise VaultConfigRejected("只能填库里的相对路径（比如 articles 或 day-info/长文）——"
                                  "库推给别人时，绝对路径在他那儿是断的")
    text = text.strip("/")
    if not text:
        raise VaultConfigRejected("原文目录不能是空的，也不能是库根（库根下面就是 nodes/，原文会被当成节点）")
    rel = PurePosixPath(text)
    if any(p in ("..", ".") for p in rel.parts):
        raise VaultConfigRejected("路径里不能有 `..` 或 `.`：原文目录必须在这个库里面")
    if rel.parts[0] in RESERVED:
        raise VaultConfigRejected(f"`{rel.parts[0]}/` 下面不能放原文："
                                  + ("那里的 md 全会被当成节点扫进图" if rel.parts[0] in ("nodes", "fields")
                                     else "那是程序自己的目录"))
    resolved = (vault / rel.as_posix()).resolve()
    if vault.resolve() not in resolved.parents:
        raise VaultConfigRejected("这个路径绕出了库（可能经过了符号链接）")
    if resolved.exists() and not resolved.is_dir():
        raise VaultConfigRejected(f"`{rel.as_posix()}` 是一个文件，不是目录")
    return rel.as_posix()


def articles_dir(vault: Path) -> Path:
    return vault / load_vault_config(vault)["articles_dir"]


def list_articles(folder: Path) -> list[Path]:
    """一个目录里的原文：所有 md，含子目录。"""
    return sorted(p for p in folder.rglob("*.md") if p.is_file()) if folder.is_dir() else []


def candidates(vault: Path) -> list[str]:
    """库里现有的、能当原文目录的目录（给设置页的下拉候选）。浅扫两层，跳过保留目录和隐藏目录。"""
    out: list[str] = []

    def walk(folder: Path, depth: int) -> None:
        for child in sorted(folder.iterdir()):
            if len(out) >= CANDIDATE_MAX:
                return
            if not child.is_dir() or child.name.startswith("."):
                continue
            rel = child.relative_to(vault).as_posix()
            if rel.split("/")[0] in RESERVED:
                continue
            out.append(rel)
            if depth < CANDIDATE_DEPTH:
                walk(child, depth + 1)

    if vault.is_dir():
        walk(vault, 1)
    return out


def save_articles_dir(vault: Path, raw, move: bool = False, dry_run: bool = False) -> dict:
    """改原文目录。旧目录里还有文章时**必须明说要搬**（`move=True`），否则拒绝：
    只改指向的话，旧的那批在界面上就认不出是原文了。

    搬之前把旧目录整份快照到 `.knowrary/backup/articles-move-<时间>/`；
    目标位置已有同名文件就整批不搬（不覆盖任何一篇）。返回 `{config, moved, backup}`。
    `dry_run` 只校验（设置页边打边问）：不建目录、不搬、不写配置，返回的是"要是存下去会怎样"。
    """
    new = normalize_articles_dir(vault, raw)
    old = load_vault_config(vault)["articles_dir"]
    plan: list[tuple[Path, Path]] = []
    if new != old:
        files = list_articles(vault / old)
        if files and not move:
            raise ArticlesNeedMove(old, len(files))
        plan = _move_plan(vault, vault / old, vault / new, files) if files else []
    if dry_run:
        return {"config": {**load_vault_config(vault), "articles_dir": new}, "moved": [], "backup": ""}
    backup = _move(vault, old, new, plan) if plan else ""
    doc = {"schema_version": SCHEMA_VERSION, **load_vault_config(vault), "articles_dir": new}
    (vault / new).mkdir(parents=True, exist_ok=True)
    write_json_atomic(config_path(vault), doc)
    return {"config": load_vault_config(vault), "moved": [t.relative_to(vault).as_posix() for _, t in plan],
            "backup": backup}


def _linking(vault: Path, old: str) -> list[Path]:
    """节点里链到旧原文目录的那些 md（frontmatter 的 sources 和正文里的 [[old/…]] 都算）。"""
    needle = f"[[{old}/"
    out = []
    for root in NODE_DIRS:
        folder = vault / root
        if folder.is_dir():
            out += [p for p in folder.rglob("*.md") if needle in p.read_text(encoding="utf-8")]
    return out


def _move_plan(vault: Path, src: Path, dst: Path, files: list[Path]) -> list[tuple[Path, Path]]:
    """整批搬的计划。目录互相套着、目标里有同名文件，都整批拒绝——一篇都不动。"""
    old, new = src.relative_to(vault).as_posix(), dst.relative_to(vault).as_posix()
    if dst.resolve() in src.resolve().parents or src.resolve() in dst.resolve().parents:
        raise VaultConfigRejected(f"`{old}` 和 `{new}` 一个套着另一个，没法整批搬——先换一个不相交的目录")
    plan = [(f, dst / f.relative_to(src)) for f in files]
    clash = [t.relative_to(vault).as_posix() for _, t in plan if t.exists()]
    if clash:
        raise VaultConfigRejected(f"新目录里已经有同名文件，一篇都没搬：{'、'.join(clash[:5])}")
    return plan


def _move(vault: Path, old: str, new: str, plan: list[tuple[Path, Path]]) -> str:
    """先快照（旧目录整份 + 要改链接的节点），再逐篇搬，最后把节点里的 `[[old/…` 改成 `[[new/…`。

    链接是完整路径写法（见 core/sources.py），不跟着改的话搬完全部指向空。返回快照目录（库内相对）。
    """
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = vault / ".knowrary" / "backup" / f"articles-move-{stamp}"
    shutil.copytree(vault / old, root / old)
    linking = _linking(vault, old)
    for p in linking:
        snap = root / p.relative_to(vault)
        snap.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, snap)
    for f, t in plan:
        t.parent.mkdir(parents=True, exist_ok=True)
        f.rename(t)
    for p in linking:
        p.write_text(p.read_text(encoding="utf-8").replace(f"[[{old}/", f"[[{new}/"), encoding="utf-8")
    return root.relative_to(vault).as_posix()
