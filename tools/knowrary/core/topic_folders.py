"""专题的文件夹（2026-10-14）：`.knowrary/topics/` 下就是真目录，文件夹可以套文件夹。

    topics/
      AI/                  ← 文件夹（没有 canvas.json），里面放一个 .keep，空着也跟着 git 走
        LLM/
          Transformer/     ← 专题（有 canvas.json），永远是最末一层
      文件系统/             ← 也可以不进文件夹

规矩（他 2026-10-14 定的）：
- **专题名全库唯一**，所以挪文件夹不改名——中心点 md 里的 `专题: Transformer` 一个字不用动。
- 专题是叶子：专题目录里不放文件夹，也不能把东西挪进专题里。
- **非空文件夹不让删**，先清空（里面只剩 .keep 算空）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .mdio import RE_ID_OK, write_json_atomic

CANVAS = "canvas.json"
KEEP = ".keep"


class FolderRejected(Exception):
    """文件夹 / 移动请求不合法：名字非法、撞名、挪进自己里面、删非空文件夹……"""


def root(vault: Path) -> Path:
    return vault / ".knowrary" / "topics"


def _is_topic(path: Path) -> bool:
    return (path / CANVAS).is_file()


def find_topic(vault: Path, topic: str) -> Path | None:
    """按名字找专题目录（在哪一层都行）。名字全库唯一，找到第一个就是它。"""
    for canvas in sorted(root(vault).rglob(CANVAS)):
        if canvas.parent.name == topic:
            return canvas.parent
    return None


def topic_names(vault: Path) -> list[str]:
    return sorted(p.parent.name for p in root(vault).rglob(CANVAS))


def folder_of(vault: Path, topic_path: Path) -> str:
    rel = topic_path.parent.relative_to(root(vault)).as_posix()
    return "" if rel == "." else rel


def folders(vault: Path) -> list[str]:
    """所有文件夹（库内相对 topics/ 的路径，`AI/LLM` 这种），专题目录不算。"""
    base = root(vault)
    if not base.is_dir():
        return []
    out = [p for p in base.rglob("*") if p.is_dir() and not _is_topic(p) and not _inside_topic(base, p)]
    return sorted(p.relative_to(base).as_posix() for p in out)


def _inside_topic(base: Path, path: Path) -> bool:
    return any(_is_topic(a) for a in path.parents if a != base and base in a.parents)


def check_name(name: str, what: str = "名字") -> str:
    name = (name or "").strip()
    if not name or not RE_ID_OK.match(name) or name.startswith("."):
        raise FolderRejected(f"{what}「{name}」不能为空，也不能含空格或 / \\ : * ? \" < > |，不能以 . 开头")
    return name


def folder_dir(vault: Path, folder: str) -> Path:
    """文件夹路径 → 目录。空串 = 顶层。每一段都过名字校验，走不出 topics/。"""
    path = root(vault)
    for seg in [s for s in (folder or "").split("/") if s]:
        path = path / check_name(seg, "文件夹名")
    if folder and (not path.is_dir() or _is_topic(path)):
        raise FolderRejected(f"没有「{folder}」这个文件夹")
    return path


def new_topic_dir(vault: Path, topic: str, folder: str, canvas: dict) -> Path:
    """新专题：名字全库唯一，落在 `folder` 里，**连画布一起写**——
    专题是按「哪个目录里有 canvas.json」认的，先建空目录再写画布的话，中间那一刻按名字找不到它。"""
    topic = check_name(topic, "专题名")
    if find_topic(vault, topic) is not None:
        raise FolderRejected(f"专题「{topic}」已经有了（专题名全库唯一）")
    target = folder_dir(vault, folder) / topic
    if target.exists():
        raise FolderRejected(f"「{folder or '顶层'}」里已经有一个叫「{topic}」的文件夹")
    target.mkdir(parents=True)
    write_json_atomic(target / CANVAS, canvas)
    return target


def make_folder(vault: Path, parent: str, name: str) -> str:
    base = folder_dir(vault, parent)
    target = base / check_name(name, "文件夹名")
    if target.exists():
        raise FolderRejected(f"「{parent or '顶层'}」里已经有「{name}」了")
    target.mkdir(parents=True)
    (target / KEEP).write_text("", encoding="utf-8")     # 空文件夹 git 不跟踪：放个占位，clone 之后还在
    return target.relative_to(root(vault)).as_posix()


def _move(src: Path, dest_parent: Path, what: str) -> Path:
    target = dest_parent / src.name
    if target == src:
        return src
    if target.exists():
        raise FolderRejected(f"目标里已经有「{src.name}」了")
    if src == dest_parent or src in dest_parent.parents:
        raise FolderRejected(f"不能把{what}挪进它自己里面")
    dest_parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(target))
    return target


def move_topic(vault: Path, topic: str, folder: str) -> str:
    src = find_topic(vault, topic)
    if src is None:
        raise FolderRejected(f"没有「{topic}」这个专题")
    return folder_of(vault, _move(src, folder_dir(vault, folder), "专题"))


def move_folder(vault: Path, path: str, to: str) -> str:
    src = folder_dir(vault, path)
    if not path:
        raise FolderRejected("顶层不能挪")
    return _move(src, folder_dir(vault, to), "文件夹").relative_to(root(vault)).as_posix()


def rename_folder(vault: Path, path: str, name: str) -> str:
    src = folder_dir(vault, path)
    if not path:
        raise FolderRejected("顶层不能改名")
    target = src.parent / check_name(name, "文件夹名")
    if target.exists():
        raise FolderRejected(f"同一层已经有「{name}」了")
    src.rename(target)
    return target.relative_to(root(vault)).as_posix()


def delete_folder(vault: Path, path: str) -> None:
    """只删空文件夹（只剩 .keep 也算空）。里面还有专题 / 文件夹就拒，先清空。"""
    src = folder_dir(vault, path)
    if not path:
        raise FolderRejected("顶层不能删")
    left = [p.name for p in src.iterdir() if p.name != KEEP]
    if left:
        raise FolderRejected(f"「{path}」里还有 {len(left)} 样东西（{'、'.join(sorted(left)[:5])}），先挪走或删掉再删文件夹")
    shutil.rmtree(src)
