"""登记新的关系类型（2026-09-29）：把一个类型写进库根的 `relation-types.json`。

来源有两处：导入时模型提的（`pending.json` 的 `types`），和 md 里已经在用、却没登记的
（索引会给一条 `unknown_type` 警告、边被当成默认族）。两种都走这一个出口。

**不整份重写那个文件**：它是手排版的——按族分块、块间空一行、冒号后列对齐。
`json.dumps` 一遍排版全没，git diff 成了整个文件。所以这里在**同族最后一行后面插一行**，
照着邻行的样子写；插完整份再解析一遍，和预期逐项对得上才落盘，对不上就拒绝（不猜）。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from .mdio import read, write
from .relations import HERE

RE_ENTRY = re.compile(r'^(\s*)"([^"]+)":(\s*)(\{.*\})(\s*,?)\s*$')
RE_BAD = re.compile(r'[\s\[\]:|#/\\"]')
MAX_NAME = 12


class TypeRejected(ValueError):
    """这个类型登记不进去：名字不合法、已经有了、族不存在、互逆类型冲突。"""


def table_path(vault: Path) -> Path:
    return vault / "relation-types.json"


def _load(vault: Path) -> tuple[Path, str, dict]:
    """库根没有这份表（用的是仓库自带的默认表）时，先把默认表抄一份进库再改——只改库里的。"""
    path = table_path(vault)
    if not path.exists():
        src = next((p for p in (vault / ".knowrary" / "relation-types.json", HERE / "relation-types.v2.json")
                    if p.exists()), None)
        if src is None:
            raise TypeRejected("找不到关系类型表")
        shutil.copy2(src, path)
    text = read(path)
    return path, text, json.loads(text)


def _check(table: dict, name: str, family: str, inverse: str, symmetric: bool) -> None:
    types = table.get("types") or {}
    for label, value in (("类型名", name), ("互逆类型名", inverse)):
        if value and (RE_BAD.search(value) or len(value) > MAX_NAME):
            raise TypeRejected(f"{label} `{value}` 不合法：不能有空格和 [ ] : | # / \\ \"，最多 {MAX_NAME} 个字")
    if not name:
        raise TypeRejected("类型名不能是空的")
    if name in types:
        raise TypeRejected(f"`{name}` 已经登记过了")
    if family not in (table.get("families") or []):
        raise TypeRejected(f"关系族 `{family}` 不存在（只有 {'、'.join(table.get('families') or [])}）")
    if symmetric and inverse:
        raise TypeRejected("无方向的类型没有互逆类型：两个只能选一个")
    if inverse == name:
        raise TypeRejected("互逆类型不能和它自己同名")
    if inverse and inverse in types:
        raise TypeRejected(f"互逆类型 `{inverse}` 已经登记过了（它有自己的互逆关系）——换一个名字，或者不填")


def _entries(name: str, family: str, inverse: str, symmetric: bool) -> dict[str, dict]:
    """要加的条目。填了互逆类型就两条一起加：`inverse` 互指，反向那条 `canonical` 归一到正向。"""
    main: dict = {"family": family}
    if inverse:
        main["inverse"] = inverse
    if symmetric:
        main["symmetric"] = True
    out = {name: main}
    if inverse:
        out[inverse] = {"family": family, "inverse": name, "canonical": name}
    return out


def _render(indent: str, name: str, value: dict, pad_to: int) -> str:
    body = ", ".join(f'"{k}": {json.dumps(v, ensure_ascii=False)}' for k, v in value.items())
    key = f'"{name}":'
    return f"{indent}{key}{' ' * max(1, pad_to - len(key))}{{ {body} }}"


def _insert(text: str, table: dict, add: dict[str, dict], family: str) -> str:
    """在同族最后一行后面插。找不到同族（这族一个类型都还没有）就插在 types 的最后一条后面。"""
    lines = text.split("\n")
    entries = []
    for i, ln in enumerate(lines):
        m = RE_ENTRY.match(ln)
        if m and m.group(2) in (table.get("types") or {}):
            entries.append((i, m))
    if not entries:
        raise TypeRejected("看不懂 relation-types.json 的排版，没敢改——请手动登记")
    same = [(i, m) for i, m in entries if table["types"][m.group(2)].get("family") == family]
    at, m = (same or entries)[-1]
    indent = m.group(1)
    pad_to = len(f'"{m.group(2)}":') + len(m.group(3))
    new = [_render(indent, n, v, pad_to) for n, v in add.items()]
    last_entry = at == entries[-1][0]
    if last_entry and not m.group(5).strip():
        lines[at] = lines[at].rstrip() + ","           # 原来是最后一条：补上逗号，新的最后一条不带
        new = [ln + "," for ln in new[:-1]] + [new[-1]]
    else:
        new = [ln + "," for ln in new]
    return "\n".join(lines[:at + 1] + new + lines[at + 1:])


def register_type(vault: Path, name: str, family: str, inverse: str = "", symmetric: bool = False) -> dict:
    """登记一个类型（填了互逆就连互逆一起）。写前把原表备份到 `.knowrary/backup/`。返回 {added, backup}。"""
    name, inverse, family = (name or "").strip(), (inverse or "").strip(), (family or "").strip()
    path, text, table = _load(vault)
    _check(table, name, family, inverse, bool(symmetric))
    add = _entries(name, family, inverse, bool(symmetric))
    after = _insert(text, table, add, family)
    expected = {**table, "types": {**table["types"], **add}}
    try:
        got = json.loads(after)
    except ValueError as exc:
        raise TypeRejected(f"插完之后 relation-types.json 解析不了（{exc}），没写——请手动登记") from exc
    if got != expected:
        raise TypeRejected("插完之后的表和预期对不上，没写——请手动登记")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = vault / ".knowrary" / "backup" / f"relation-types-{stamp}.json"
    n = 2
    while backup.exists():               # 同一秒里连着登记两个：别让第二份备份盖掉第一份（那才是原样）
        backup = backup.with_name(f"relation-types-{stamp}-{n}.json")
        n += 1
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    write(path, after)
    return {"added": list(add), "backup": backup.relative_to(vault).as_posix()}
