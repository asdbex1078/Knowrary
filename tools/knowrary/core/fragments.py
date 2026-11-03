"""碎片记忆（2026-10-23）：接不同的活时学到的零散点子，先记下来，攒多了再归拢成体系。

**碎片不是节点**：太碎的东西直接进图会成毛线球（拆分粒度的老规矩），所以碎片住在 vault 的
`fragments/` 里、按天一个 md（`fragments/2026-10-23.md`），一条碎片一个带时间的 bullet。
目录不在 NODE_DIRS，索引不扫它；Obsidian 可见、git 走、人手也能直接写。

**出口重于入口**：碎片系统的死法从来都是只进不出。所以积压数和最老碎片年龄常显，
到量（条数或天数）就催归拢；归拢时模型分簇，每簇三选一——并进已有节点（走 enrich 追加）、
长成新节点（丢给导入流水线）、继续躺着。归拢后的碎片**不删**，行尾标去向
（`→ [[节点]]（日期）` / `→ 导入《标题》（日期）`），留档可溯源。

一行的形状（RE_LINE）：

    - 09:14 Caffeine 的 evictionLock 和 CHM 桶锁会叠成双重卡死
    - 14:02 PLT 是惰性绑定的蹦床 → [[动态链接]]（2026-10-25）

碎片 id = `日期 时间 文本短哈希`：按内容认，不按行号——人手在 Obsidian 里插一行、删一行，
已经发出去的 id 照样对得上。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

from .mdio import read, write

FRAGMENTS_DIR = "fragments"
RE_LINE = re.compile(r"^- (\d{2}:\d{2}) (.+?)\s*$")
RE_DONE = re.compile(r"\s*→\s*(.+?)（(\d{4}-\d{2}-\d{2})）$")
RE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
# 催归拢的两道线：攒到这么多条、或最老的躺了这么多天，就该归了
NAG_OPEN = 10
NAG_DAYS = 14


def fragments_dir(vault: Path) -> Path:
    return vault / FRAGMENTS_DIR


def _fid(date: str, time: str, text: str) -> str:
    return f"{date} {time} {hashlib.sha1(text.encode()).hexdigest()[:8]}"


def _parse_line(date: str, line: str) -> dict | None:
    m = RE_LINE.match(line)
    if not m:
        return None
    time, rest = m.group(1), m.group(2)
    done = RE_DONE.search(rest)
    text = rest[:done.start()].rstrip() if done else rest
    return {"id": _fid(date, time, text), "date": date, "time": time, "text": text,
            "target": done.group(1) if done else "", "done_at": done.group(2) if done else ""}


def list_fragments(vault: Path) -> list[dict]:
    """全部碎片，新的在前（天倒序、天内按文件顺序倒过来）。认不出形状的行跳过——
    那是人手写的注释或空行，不是碎片。"""
    folder = fragments_dir(vault)
    if not folder.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(folder.glob("*.md"), reverse=True):
        m = RE_FILE.match(path.name)
        if not m:
            continue
        day = [f for ln in read(path).splitlines() if (f := _parse_line(m.group(1), ln))]
        out.extend(reversed(day))
    return out


def add_fragment(vault: Path, text: str, now: dt.datetime | None = None) -> dict:
    """记一条进今天的文件。碎片是一句话的事：换行压成空格，空的拒收。"""
    clean = " ".join(str(text or "").split())
    if not clean:
        raise ValueError("碎片不能是空的")
    now = now or dt.datetime.now()
    date, time = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    path = fragments_dir(vault) / f"{date}.md"
    old = read(path) if path.is_file() else ""
    body = (old.rstrip() + "\n" if old.strip() else "") + f"- {time} {clean}\n"
    write(path, body)
    return _parse_line(date, f"- {time} {clean}")


def mark_fragments(vault: Path, ids: list[str], target: str, today: dt.date | None = None) -> int:
    """给一批碎片标去向（行尾 `→ 目标（日期）`）。已标过的不重标。返回标了几条。
    按 id（日期+时间+内容哈希）找行，人手挪过行也认得。"""
    wanted = set(ids)
    stamp = (today or dt.date.today()).isoformat()
    done = 0
    for path in fragments_dir(vault).glob("*.md"):
        m = RE_FILE.match(path.name)
        if not m:
            continue
        lines = read(path).splitlines()
        changed = False
        for i, ln in enumerate(lines):
            f = _parse_line(m.group(1), ln)
            if f and f["id"] in wanted and not f["target"]:
                lines[i] = f"{ln.rstrip()} → {target}（{stamp}）"
                changed = True
                done += 1
        if changed:
            write(path, "\n".join(lines) + "\n")
    return done


def build_consolidate_prompt(cards: list[dict], open_fragments: list[dict]) -> str:
    """归拢提示词：全部未归拢碎片 + 和它们最相关的已有节点（口径同导入：select_related）。"""
    from .article import PROMPTS_DIR, describe_related, select_related
    related = select_related(cards, "\n".join(f["text"] for f in open_fragments))
    frags = "\n".join(f"- [{f['id']}]（{f['date']}）{f['text']}" for f in open_fragments)
    tpl = read(PROMPTS_DIR / "fragments.md")
    return (tpl.replace("{{related}}", "\n".join(describe_related(related)) or "（图里还没有相关节点）")
               .replace("{{fragments}}", frags))


def check_clusters(data, open_ids: set[str], node_ids: set[str]) -> tuple[list[dict], list[str]]:
    """校验模型交回的簇，口径同审图（topic_review.check_items）：改法不完整的降级、指不到真东西的丢，
    丢了什么记 issues 给人看，不默默吞。碎片 id 只认未归拢的，一条只许进一个簇。"""
    issues: list[str] = []
    out: list[dict] = []
    taken: set[str] = set()
    for c in (data.get("clusters") if isinstance(data, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        ids = [i for i in (c.get("ids") or []) if isinstance(i, str) and i in open_ids and i not in taken]
        if not ids:
            issues.append(f"簇「{c.get('title') or '?'}」指不到任何未归拢的碎片，丢掉")
            continue
        action = str(c.get("action") or "").strip()
        node = str(c.get("node") or "").strip() or None
        text = str(c.get("text") or "").strip()
        if action == "enrich" and (not node or node not in node_ids):
            issues.append(f"簇「{c.get('title') or '?'}」要并进的 `{node}` 不在图里，降成 keep")
            action, node = "keep", None
        if action in ("enrich", "new") and not text:
            issues.append(f"簇「{c.get('title') or '?'}」没给整理后的内容，降成 keep")
            action = "keep"
        if action not in ("enrich", "new", "keep"):
            issues.append(f"簇「{c.get('title') or '?'}」的处置 `{action}` 不认识，丢掉")
            continue
        taken.update(ids)
        out.append({"title": str(c.get("title") or "").strip() or "未命名",
                    "ids": ids, "action": action, "node": node,
                    "summary": str(c.get("summary") or "").strip(), "text": text})
    return out, issues


def stats(entries: list[dict], today: dt.date | None = None) -> dict:
    """积压统计：没归拢的有几条、最老的躺了几天、该不该催。"""
    today = today or dt.date.today()
    open_ = [f for f in entries if not f["target"]]
    oldest = max(((today - dt.date.fromisoformat(f["date"])).days for f in open_), default=0)
    return {"open": len(open_), "oldest_days": oldest,
            "nag": len(open_) >= NAG_OPEN or oldest >= NAG_DAYS}
