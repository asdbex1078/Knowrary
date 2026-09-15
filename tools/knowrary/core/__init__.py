"""Knowrary 核心库：md 解析、关系归一、索引生成、契约校验。

CLI（knowrary.py）与后续的本地服务（FastAPI）都只依赖这里，避免两套解析实现漂移。
"""
from .diagnostics import Diagnostic, Diagnostics
from .coach import build_today, current_stage
from .digest import build_digest
from .index import INDEX_SCHEMA_VERSION, IndexResult, build_index, content_hash, index_path, load_previous
from .layout import (CELL_H, LAYOUT_SCHEMA_VERSION, NODE_H, NODE_W, build_initial_layout, empty_layout,
                     find_orphans, layout_path, stamp)
from .mdio import (NODE_DIRS, RE_LINK, RE_REL_HEADER, dump_frontmatter, first_paragraph, json_safe,
                   load_json, read, split_frontmatter, strip_md, walk_md, write, write_json_atomic,
                   yaml_scalar)
from .parser import Node, digest_of, load_node, load_vault, validate_frontmatter
from .placement import inbox_ids, place_node, place_or_grow, plan_growth, target_group
from .plans import (DEFAULT_LOAD, LOAD_HOURS, LOADS, MASTERY_ORDER, all_progress, all_schedules,
                    as_date, empty_plans, load_plans, mastery_of, plans_path, point_ids,
                    progress_of, save_plans, schedule_of)
from .quiz import append_answers, load_quiz_log, wrong_nodes
from .usage import load_usage, record as record_usage, summary as usage_summary
from .merge import MergeRejected, apply_merge, plan_merge
from .rename import RenameRejected, apply_rename, backup_rename, plan_rename
from .review import GRADES, due_nodes, load_log, next_due_for, record_review
from .writer import (ChangeRejected, FileEdit, WriteConflict, apply_to_text, backup, commit, plan,
                     split_sections)
from .relations import Edge, NormalizedEdge, RelationTypes, load_relation_types, normalize_direction, parse_relations
from .schema import validate_index

__all__ = [
    "ChangeRejected", "Diagnostic", "Diagnostics", "Edge", "FileEdit", "INDEX_SCHEMA_VERSION",
    "DEFAULT_LOAD", "GRADES", "LOADS", "LOAD_HOURS", "MASTERY_ORDER", "all_progress", "all_schedules", "as_date", "append_answers", "build_digest", "build_today",
    "current_stage", "due_nodes",
    "empty_plans", "inbox_ids", "load_log", "load_plans", "load_quiz_log", "mastery_of",
    "next_due_for", "place_node", "place_or_grow", "plan_growth", "plans_path", "point_ids",
    "progress_of", "schedule_of", "record_review", "record_usage", "load_usage", "usage_summary",
    "MergeRejected", "RenameRejected", "apply_merge", "apply_rename", "backup_rename",
    "plan_merge", "plan_rename",
    "save_plans", "target_group", "wrong_nodes",
    "CELL_H", "LAYOUT_SCHEMA_VERSION", "NODE_H", "NODE_W", "WriteConflict", "apply_to_text", "backup", "commit", "digest_of", "plan",
    "split_sections",
    "build_initial_layout", "empty_layout", "find_orphans", "layout_path", "stamp", "IndexResult", "NODE_DIRS", "Node",
    "NormalizedEdge", "RE_LINK", "RE_REL_HEADER", "RelationTypes", "build_index", "content_hash",
    "dump_frontmatter", "first_paragraph", "index_path", "json_safe", "load_json", "load_node",
    "load_previous", "load_relation_types", "load_vault", "normalize_direction", "parse_relations", "read",
    "split_frontmatter", "strip_md", "validate_frontmatter", "validate_index", "walk_md", "write",
    "write_json_atomic", "yaml_scalar",
]
