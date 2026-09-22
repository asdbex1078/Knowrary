"""知识库（vault）的选择、初始化与切换——**第一份脱离 vault 的契约**。

程序和知识库从此是两样东西：仓库里只有程序，知识库是你自己挑的一个目录。
于是"当前用哪个库"这件事**不可能存在 vault 里**（要先知道库在哪才读得到库里的设置），
它只能落在用户级配置 `~/.knowrary/config.json`：

    {"schema_version": 1, "current": "/abs/path", "recent": ["/abs/path", ...]}

优先级 **环境变量 > 用户级配置**：`KNOWRARY_VAULT` 是自测与脚本用的临时覆盖，最高；
没有它才读用户配置；两个都没有就明说"还没选库"，**不猜**——
2026-09-22 拆分前这里还会回落到仓库自身，数据搬走之后那条从兼容变成了陷阱
（在代码仓库里建个 `nodes/` 就被悄悄当成库）。

`KNOWRARY_HOME` 覆盖配置目录：自测绝不能写到真实的 `~/.knowrary`。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
from pathlib import Path

from .paths import REPO, core

CONFIG_NAME = "config.json"
SCHEMA_VERSION = 1
RECENT_MAX = 10
BROWSE_MAX = 400
SEED = REPO / "seed"
USER_SEED = REPO / "seed" / "user"     # 铺进 ~/.knowrary 的模板与说明
SAMPLE = REPO / "examples" / "sample-vault"
EXAMPLES = REPO / "examples"                          # 里面的示例库是放出来给人切过去随便改的
DEMO_MARK = ".knowrary/_demo.json"                    # 示例数据的锚点日期，铺完即删
DEMO_DATED = (".knowrary/review-log.json", ".knowrary/quiz-log.json")
DATE_HEAD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# 初始化时铺出来的目录骨架（空目录也建，人打开 Finder 就知道东西该往哪放）
SKELETON = ("nodes", "fields", "assets", ".knowrary/layouts", ".knowrary/imports", ".knowrary/coaches")


class VaultRejected(ValueError):
    """选的目录不能当库用——路径不存在、非空、越界。给人看的话都在异常消息里。"""


class NoVaultSelected(RuntimeError):
    """一个库都还没选。接口层转 409，前端据此把人送进「设置 → 知识库」。"""


# ---------------------------------------------------------------- 用户级配置

def home() -> Path:
    """用户级配置目录。和 `core.user_dir()` 是同一个地方——配置跟人，数据跟库。"""
    return core.user_dir()


def ensure_user_dir() -> Path:
    """第一次用时把模板铺到 `~/.knowrary/`（只补不覆盖）。

    里面是给人手写的那几份：`llm.example.json`、`coach.example.md`，外加一页说明
    讲清"这个目录是什么、和知识库怎么分工"。铺一次就够，之后随人改。
    """
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    if USER_SEED.is_dir():
        for src in sorted(p for p in USER_SEED.iterdir() if p.is_file()):
            dst = root / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)
    return root


def config_path() -> Path:
    return home() / CONFIG_NAME


def load() -> dict:
    """读用户级配置。**读坏了一律回落空配置**——配置文件烂掉不该让程序打不开。"""
    path = config_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "current": None, "recent": []}
    try:
        doc = core.load_json(path) or {}
    except (ValueError, OSError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    recent = [str(p) for p in (doc.get("recent") or []) if isinstance(p, str)]
    current = doc.get("current")
    return {"schema_version": SCHEMA_VERSION,
            "current": current if isinstance(current, str) and current else None,
            "recent": recent}


def save(cfg: dict) -> dict:
    core.write_json_atomic(config_path(), cfg)
    return cfg


# ---------------------------------------------------------------- 判定

def in_program_repo(path: Path) -> bool:
    """这个路径是不是 Knowrary 程序自己的目录（`examples/` 除外）。

    **程序目录永远不能当知识库。** 2026-09-22 栽过一次：旧版 dev.sh 把 `KNOWRARY_VAULT`
    钉在仓库上，服务照着往 `<仓库>/.knowrary/` 写了 index.json 和 layout.json——
    仓库于是"长得像库"，在目录选择器里显示成「已是库」，被选中之后
    `nodes/` 又不存在，解析器退回去扫整个仓库，把 `examples/` 里的示例当成了人的知识库。
    只堵"不是库的目录"堵不住这种：它确实**变成**了库。所以这里直接按身份拒绝。

    `examples/` 是唯一的例外：示例库就是**放出来给人切过去、随便改**的一个真库，
    它自带 `nodes/` 和 `fields/`，不会触发那个"退回去扫整个目录"的分支。
    代价是改了它 `git pull` 会冲突——那是明说的，不拦。
    """
    if path == EXAMPLES or EXAMPLES in path.parents:
        return False
    return path == REPO or REPO in path.parents


def is_vault(path: Path) -> bool:
    """有 `.knowrary/`，或有 `nodes/` / `fields/` 里的任意一个，就当它是库。

    判据故意松：手工建的库、从别处 clone 的库、只有笔记还没建过图的库，都得认。
    """
    return (path / ".knowrary").is_dir() or any((path / d).is_dir() for d in ("nodes", "fields"))


def _listable(path: Path) -> bool:
    try:
        next(path.iterdir(), None)
        return True
    except OSError:
        return False


def status(path: Path) -> str:
    """vault（已是库）/ empty（可初始化）/ occupied（有别的东西）/ program（程序自己）/ missing。"""
    if not path.is_dir():
        return "missing"
    if in_program_repo(path):
        return "program"
    if is_vault(path):
        return "vault"
    if not _listable(path):
        return "occupied"
    visible = [p for p in path.iterdir() if not p.name.startswith(".")]
    return "empty" if not visible else "occupied"


# ---------------------------------------------------------------- 当前库

def current_vault() -> Path:
    """当前库。没选过、或指着的地方根本不是库，都抛 `NoVaultSelected`。

    **"不是库"必须当场报错，不能凑合用。** 2026-09-22 踩过一次：旧版 dev.sh 把
    `KNOWRARY_VAULT` 钉在代码仓库上，而仓库的 `nodes/` 已经搬走——解析器一看没有
    `nodes/` 和 `fields/`，按老规矩退回去扫整个目录（`core.walk_md`），
    于是把 `examples/sample-vault/` 里的示例节点当成了人家的知识库，
    界面上显示 17 个节点 26 个错误，而人只会以为"切换没生效"。
    与其扫出一堆垃圾，不如直说这个目录不是库。
    """
    env = os.environ.get("KNOWRARY_VAULT")
    if env:
        return _checked(Path(env).expanduser().resolve(), f"环境变量 KNOWRARY_VAULT 指着 {env}")
    picked = load().get("current")
    if picked:
        return _checked(Path(picked).expanduser().resolve(),
                        f"上次选的库 {picked} 现在不在了（被删了？改名了？）")
    raise NoVaultSelected("还没有选择知识库：打开「设置 → 知识库」，选一个目录初始化，或指向已有的库")


def _checked(path: Path, why: str) -> Path:
    if in_program_repo(path):
        raise NoVaultSelected(f"{why}，可那是 Knowrary 程序自己的目录，不是知识库。"
                              "打开「设置 → 知识库」选一个别的地方"
                              "（旧版 server/dev.sh 会把 KNOWRARY_VAULT 钉在这里，重起一次服务即可）")
    if is_vault(path):
        return path
    raise NoVaultSelected(f"{why}，但那里不是知识库（没有 .knowrary/ 也没有 nodes/ fields/）。"
                          "打开「设置 → 知识库」重新选一个")


def switch(path: Path) -> dict:
    """切到某个已有的库。**不创建任何东西**——要建目录走 `init`。"""
    target = path.expanduser().resolve()
    state = status(target)
    if state == "missing":
        raise VaultRejected(f"目录不存在：{target}")
    if state == "program":
        raise VaultRejected(f"{target} 是 Knowrary 程序自己的目录，不能当知识库——另挑一个地方")
    if state != "vault":
        raise VaultRejected(f"这个目录还不是知识库：{target}（先用「选定并初始化」把它建成库）")
    cfg = load()
    cfg["current"] = str(target)
    cfg["recent"] = _remember(cfg["recent"], str(target))
    return save(cfg)


def _remember(recent: list[str], path: str) -> list[str]:
    """最近使用：刚用的排第一，去重，最多留 RECENT_MAX 条。"""
    return [path] + [p for p in recent if p != path][:RECENT_MAX - 1]


def forget(path: Path) -> dict:
    """从最近列表里移掉一条（当前库不动——那是 switch 的事）。"""
    cfg = load()
    cfg["recent"] = [p for p in cfg["recent"] if p != str(path.expanduser().resolve())]
    return save(cfg)


# ---------------------------------------------------------------- 初始化

def init(path: Path, sample: bool = False) -> Path:
    """按骨架把一个空目录建成库，然后铺一份出厂种子（可选再铺一份示例内容）。

    已经是库就**原样返回**（幂等：重复点「初始化」不该覆盖任何人的数据）；
    非空又不是库的目录一律拒绝——往别人的项目目录里撒十几个文件是不可逆的脏活。

    `sample` 决定要不要把 `examples/sample-vault/` 一起铺进去。默认不铺：
    示例是**给第一次打开的人看的**，不该强塞给每一个建库的人——那样所有人的第一件事都是删它。
    """
    target = path.expanduser().resolve()
    state = status(target)
    if state == "program":
        raise VaultRejected(f"{target} 是 Knowrary 程序自己的目录，不能当知识库——另挑一个地方")
    if state == "vault":
        return target
    if state == "occupied":
        raise VaultRejected(f"目录非空，且不像知识库：{target}（换一个空目录，或先清空）")
    if state == "missing" and not target.parent.is_dir():
        raise VaultRejected(f"上级目录不存在：{target.parent}")
    for rel in SKELETON:
        (target / rel).mkdir(parents=True, exist_ok=True)
    if sample:
        _copy_tree(SAMPLE, target)       # 先铺示例，种子再补缺——示例自带的那几份更贴题
        _rebase_demo(target)
    _copy_tree(SEED, target)
    return target


def _rebase_demo(target: Path) -> None:
    """把示例里的复习与答题日期整体平移到今天。

    **写死日期会烂**：示例是某一天造出来的，半年后新用户建库，看到的是「逾期 183 天」、
    日历热力图全挤在一个早就过去的月份——功能没坏，但看起来像坏了。
    所以示例记一个锚点日期，铺进新库时按「今天 − 锚点」把日期平移一遍，
    于是无论哪天初始化，看到的都是"昨天刚学过、今天有一个到期、有一个忘过"。
    """
    mark = target / DEMO_MARK
    try:
        anchor = dt.date.fromisoformat((core.load_json(mark) or {}).get("anchor", ""))
    except (OSError, ValueError, TypeError):
        mark.unlink(missing_ok=True)
        return
    delta = dt.date.today() - anchor
    if delta.days:
        for rel in DEMO_DATED:
            path = target / rel
            if path.exists():
                core.write_json_atomic(path, _shift(core.load_json(path), delta))
    mark.unlink(missing_ok=True)


def _shift(value, delta: dt.timedelta):
    """递归平移：凡是以 `YYYY-MM-DD` 开头的字符串就挪 delta 天，后缀（时间、Z）原样留着。"""
    if isinstance(value, dict):
        return {k: _shift(v, delta) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift(v, delta) for v in value]
    if isinstance(value, str):
        m = DATE_HEAD.match(value)
        if m:
            moved = dt.date(int(m[1]), int(m[2]), int(m[3])) + delta
            return moved.isoformat() + value[10:]
    return value


def _copy_tree(src_root: Path, target: Path) -> None:
    """只补不覆盖：已经存在的同名文件保持原样。派生缓存不跟着搬（重建一次就有）。"""
    if not src_root.is_dir():
        return
    for src in sorted(p for p in src_root.rglob("*") if p.is_file()):
        rel = src.relative_to(src_root)
        if rel.parts and rel.parts[0] == "user":     # seed/user/ 是铺给 ~/.knowrary 的，不进库
            continue
        if rel.as_posix() == ".knowrary/index.json":      # 派生缓存，重建一次就有
            continue
        dst = target / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


# ---------------------------------------------------------------- 目录浏览

def _roots() -> list[Path]:
    """能逛的地盘：家目录，外加已经在用 / 用过的库的上级目录。

    只锁家目录是不够的——库放在外置盘或 `/Volumes` 下，人就再也点不到它。
    已经选过的库说明这台机器的主人信任那个位置，把它的**上级**一并放行，
    既够用又不至于把整块盘摊开。
    """
    roots = [Path.home().resolve()]
    cfg = load()
    for raw in [cfg.get("current"), *cfg.get("recent", []), os.environ.get("KNOWRARY_VAULT")]:
        if not raw:
            continue
        parent = Path(raw).expanduser().resolve().parent
        if parent not in roots:
            roots.append(parent)
    return roots


def _inside_roots(path: Path) -> bool:
    return any(path == r or r in path.parents for r in _roots())


def _start() -> Path:
    """不给路径时从哪儿开始逛：**当前库的旁边**，没有当前库才回家目录。

    开在家目录是不够的——库放在别的分支上（比如 `/Volumes/…`）时，
    从家目录往上走会撞到放行边界，人就再也点不过去了。
    """
    roots = _roots()
    try:
        parent = current_vault().parent
    except NoVaultSelected:
        return roots[0]
    return parent if parent in roots or _inside_roots(parent) else roots[0]


def _root_shortcuts(roots: list[Path]) -> list[dict]:
    """选择器顶上的几个落脚点。第一个永远是家目录，其余是已选过的库的上级。"""
    out = []
    for i, r in enumerate(roots):
        out.append({"name": "家目录" if i == 0 else (r.name or str(r)),
                    "path": str(r), "status": status(r)})
    return out


def browse(raw: str | None) -> dict:
    """列出一个目录下的子目录，供前端画文件夹选择器。

    **越界一律弹回落脚点**：这个接口能枚举本机目录，服务虽然只听 127.0.0.1，
    也没有理由把整块盘摊开。`resolve()` 先把 `..` 和符号链接摊平，再判边界，
    所以 `~/x/../../etc` 这种写法进不来。
    """
    roots = _roots()
    here = Path(raw).expanduser().resolve() if raw else _start()
    if not _inside_roots(here):
        here = _start()
    if not here.is_dir():
        raise VaultRejected(f"目录不存在：{here}")
    entries = []
    try:
        for child in sorted(here.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            entries.append({"name": child.name, "path": str(child), "status": status(child)})
            if len(entries) >= BROWSE_MAX:
                break
    except OSError as exc:
        raise VaultRejected(f"读不了这个目录：{here}（{exc.strerror or exc}）") from exc
    parent = str(here.parent) if _inside_roots(here.parent) and here.parent != here else None
    return {"path": str(here), "parent": parent, "root": str(roots[0]),
            "roots": _root_shortcuts(roots), "status": status(here), "entries": entries}


# ---------------------------------------------------------------- 给接口层的读

def describe(path: Path | str) -> dict:
    p = Path(path).expanduser()
    return {"path": str(p), "name": p.name or str(p), "status": status(p)}


def read() -> dict:
    """设置页要的全部：当前库、最近列表、家目录、当前库是不是被环境变量钉住的。"""
    ensure_user_dir()
    cfg = load()
    pinned = bool(os.environ.get("KNOWRARY_VAULT"))
    try:
        current = str(current_vault())
    except NoVaultSelected:
        current = None
    recent = [describe(p) for p in cfg["recent"] if p != current]
    return {"current": describe(current) if current else None, "recent": recent,
            "root": str(_roots()[0]), "pinned": pinned}
