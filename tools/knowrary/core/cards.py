"""卡片流水：`.knowrary/cards.jsonl`，一行一个事件（提出 / 采纳）。

**为什么值得单独记一份**：账本（`llm-usage.json`）知道花了多少钱，md 知道最后写进去什么，
但中间那一段——**摆出来几张、我点了几张、几张被我丢了**——没有任何地方回答得了。
于是"这套教练到底值不值"只能靠感觉。采纳率是这个产品唯一真正的产出指标：
建了几个节点算不了数（可能是我自己在面板上建的），花了多少钱也算不了数（不知道换来什么）。

**为什么是事件流而不是在别处加字段**：卡片摆出来之后可能**几天都不点**（真实使用里
"三张卡还在桌上"是常态）。写进用量账本的调用行就得回头改历史行，写进对话留档就得重写那一行 jsonl，
两者都违反"只追加"。事件流天然支持"很久以后才发生的第二个事件"。

**这里不存钱。** 只存时间和身份，成本从 `llm-usage.json` 按天现算——
同日历那条纪律：**全部派生，不新增第二份真值**。存一份摊派过的金额，
下次改了摊派口径就再也对不上了。

和 `issues.jsonl` 同一条旁路纪律：记不上绝不能拖垮正经操作。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

KEEP = 2000          # 只留最近这么多条：它是用来看趋势的，不是审计账
KINDS = ("changes", "project", "points", "list_edit")


def cards_path(vault: Path) -> Path:
    return vault / ".knowrary" / "cards.jsonl"


def new_card_id() -> str:
    """给一张卡一个够用的 id。**不用 uuid**：它要出现在 SSE 事件、前端 DOM key
    和这份流水里，短一点人也看得懂。"""
    now = dt.datetime.now()        # 只取一次：两次 now() 会在毫秒边界上错位
    return f"c{now:%m%d%H%M%S}{now.microsecond // 1000:03d}"


def _write(vault: Path, row: dict) -> None:
    path = cards_path(vault)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return                                  # 记不上就算了，绝不拖垮正经操作
    _trim(path)


def _trim(path: Path) -> None:
    """只留最近 KEEP 条，**但「采纳」事件一条不裁**：对话留档读回卡片时，
    点没点全靠它现算（`server/chat.py::_revive_cards`）。裁掉了，几个月前点过的卡
    就会重新摆成"没点"。它一行几十字节，全留着也不值一提；统计只数还在的「提出」，口径不变。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > KEEP * 2:
            cut = len(lines) - KEEP
            kept = [ln for ln in lines[:cut] if '"event": "applied"' in ln] + lines[cut:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError:
        pass


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def proposed(vault: Path, card_id: str, kind: str, *, session: str = "", project: str = "",
             turn: str = "", detail: dict | None = None) -> None:
    """摆了一张卡。`turn` 是这一轮我那句话的留档 ts，用来把卡片和回合成本对上。"""
    _write(vault, {"ts": _now(), "id": card_id, "event": "proposed", "kind": kind,
                   "session": session, "project": project, "turn": turn, **(detail or {})})


def applied(vault: Path, card_id: str) -> None:
    """这张卡被我点了。**只在真落盘之后调**——点了没写成不算采纳。"""
    if card_id:
        _write(vault, {"ts": _now(), "id": card_id, "event": "applied"})


def audited(vault: Path, card_id: str, stamp: str, report: dict) -> None:
    """这张卡审过一次。`stamp` 是审的那份改法的指纹：卡上改过一个字，指纹就对不上，
    旧结论不能再拿来放行（`server/audit.py::gate`）。报告整份存下来——
    卡片折叠之后再展开、隔天再打开，都要看得到当时审出了什么。"""
    if card_id:
        _write(vault, {"ts": _now(), "id": card_id, "event": "audited", "stamp": stamp, "report": report})


def last_audit(vault: Path, card_id: str) -> dict | None:
    """这张卡最近一次审核（整行，带 stamp / report）。没审过是 None。"""
    if not card_id:
        return None
    hit = [r for r in load(vault) if r.get("id") == card_id and r.get("event") == "audited"]
    return hit[-1] if hit else None


def audits(vault: Path) -> dict[str, dict]:
    """每张卡最近一次审核，一次读完：读回一整段对话时别每张卡都把流水重读一遍。"""
    return {r["id"]: r for r in load(vault) if r.get("event") == "audited" and r.get("id")}


def load(vault: Path) -> list[dict]:
    path = cards_path(vault)
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue                        # 坏行跳过：旁路不该因为一行脏数据就瞎
    except OSError:
        return []
    return out


def stats(vault: Path, day: str | None = None) -> dict:
    """今天摆了几张、点了几张，以及**累计**的采纳率。

    采纳按**卡片自己的提出日**归档，不按点击日——"今天点了昨天那张"应该算昨天那张卡的产出，
    否则采纳率会在跨天时莫名其妙地超过 100%。
    """
    day = day or dt.date.today().isoformat()
    rows = load(vault)
    born = {r["id"]: r for r in rows if r.get("event") == "proposed" and r.get("id")}
    hit = {r["id"] for r in rows if r.get("event") == "applied" and r.get("id") in born}
    today_born = [cid for cid, r in born.items() if str(r.get("ts", ""))[:10] == day]
    return {"day": day,
            "proposed": len(today_born),
            "applied": sum(1 for cid in today_born if cid in hit),
            "total_proposed": len(born),
            "total_applied": len(hit)}
