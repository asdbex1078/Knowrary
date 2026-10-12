"""谱系目录的服务层（2026-09-30，2026-10-12 起一级子目录一张谱系图）：读家族文件、把技术点对到知识点、型号之间连线。

解析在 `core.catalog`（纯函数，CLI 的 check 也用）；这里多做的是**对着索引**的那一步：
`[[MLA]]` 是不是图里的知识点、知识点被哪些型号用到（检查器里反查）。每次请求现读——
家族文件就几个，读一遍毫秒级，不值得进索引、再多一份失效逻辑。
"""
from __future__ import annotations

from pathlib import Path

from .contracts import ModelsRead
from .index_service import current_index
from .paths import core


class ModelsDirRejected(ValueError):
    """模型目录填得不对（绕出库、落进 nodes/ 等）。"""


def read(vault: Path) -> ModelsRead:
    catalog = core.load_catalog(vault)
    links = core.link_models(catalog)
    lookup = _node_lookup(current_index(vault))
    used_by: dict[str, list[str]] = {}
    families = []
    for fam in catalog["families"]:
        models = []
        for m in fam["models"]:
            mid = f"{fam['id']}/{m['name']}"
            tech = [{**t, "node": lookup.get((t["link"] or t["text"]).strip().lower())} for t in m["tech"]]
            for t in tech:
                if t["node"] and mid not in used_by.setdefault(t["node"], []):
                    used_by[t["node"]].append(mid)
            models.append({**{k: v for k, v in m.items() if k != "tech"}, "id": mid, "family": fam["id"],
                           "tech": tech})
        families.append({**fam, "models": models})
    return ModelsRead(dir=catalog["dir"], configured=catalog["configured"], exists=catalog["exists"],
                      lineages=catalog["lineages"], families=families, links=links, warnings=catalog["warnings"],
                      used_by=used_by, candidates=[] if catalog["families"] else core.articles_dir_candidates(vault),
                      sample=core.CATALOG_SAMPLE, sample_lineage=core.CATALOG_SAMPLE_LINEAGE,
                      fields=list(core.CATALOG_FIELDS))


def _node_lookup(index: dict) -> dict[str, str]:
    """知识点的 id / 名字 / 别名（小写）→ id。纯文字的技术点也试着对一下：写「MoE」没加括号也能点过去。"""
    out: dict[str, str] = {}
    for n in index["nodes"]:
        if n.get("virtual"):
            continue
        for key in (n["id"], n.get("name"), *(n.get("aliases") or [])):
            if key:
                out.setdefault(str(key).strip().lower(), n["id"])
    return out


def set_dir(vault: Path, raw: str, dry_run: bool = False) -> ModelsRead:
    try:
        core.save_models_dir(vault, raw, dry_run=dry_run)
    except core.VaultConfigRejected as exc:
        raise ModelsDirRejected(str(exc)) from exc
    return read(vault)
