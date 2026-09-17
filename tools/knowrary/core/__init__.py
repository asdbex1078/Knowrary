"""Knowrary 核心库：md 解析、关系归一、索引生成、契约校验。

CLI（knowrary.py）与后续的本地服务（FastAPI）都只依赖这里，避免两套解析实现漂移。
"""
from .diagnostics import Diagnostic, Diagnostics
from .coach import build_today, current_stage, project_lines
from .calendar import MAX_DAYS as CALENDAR_MAX_DAYS, build_calendar, streak
from .digest import build_digest, duplicates, group_labels, kinship, link_hints, lonely, misplaced, no_year
from .issues import (issues_path, load as load_issues, record as record_issue,
                     summary as issues_summary)
from .index import INDEX_SCHEMA_VERSION, IndexResult, build_index, content_hash, index_path, load_previous
from .layout import (CELL_H, LAYOUT_SCHEMA_VERSION, NODE_H, NODE_W, build_initial_layout, build_project_layout, empty_layout,
                     find_orphans, layout_path, stamp)
from .mdio import (NODE_DIRS, RE_LINK, RE_REL_HEADER, dump_frontmatter, first_paragraph, json_safe,
                   load_json, read, split_frontmatter, strip_md, walk_md, write, write_json_atomic,
                   yaml_scalar)
from .parser import (LAYERS, UNLAYERED, Node, digest_of, load_node, load_vault,
                     validate_frontmatter)
from .placement import (by_field_and_layer, inbox_ids, is_lane_stack, place_node,
                        place_or_grow, plan_growth, plan_lane_growth, plan_new_lane, target_group)
from .projects import (DEFAULT_LEVEL, DEFAULT_LOAD, ID_OK, KINDS, LEVELS, level_of, exam_state, states_of, study_state, LOAD_HOURS, LOADS, MASTERY_ORDER, all_progress,
                       all_schedules, as_date, ascii_id, done_ids, empty_projects, legacy_plans_path,
                       list_field, lists_of, load_projects, mastery_of, merge_progress, point_ids,
                       progress_of, progress_of_project, projects_path, save_projects, schedule_of,
                       stage_points, upgrade_v1)
from .pool import (add as add_question, enrich as enrich_questions, for_nodes as pool_for_nodes,
                   load_pool, mark as mark_questions, norm as pool_norm, pool_stats)
from .quiz import append_answers, clear_open, load_open, load_quiz_log, save_open, wrong_nodes
from .usage import load_usage, record as record_usage, summary as usage_summary
from .merge import MergeRejected, apply_merge, plan_merge
from .rename import RenameRejected, apply_rename, backup_rename, plan_rename
from .review import GRADES, due_nodes, load_log, next_due_for, record_review
from .writer import (CHANGE_TYPES, EDITABLE_FIELDS, ChangeRejected, FileEdit, WriteConflict,
                     apply_to_text, backup, commit, plan, split_sections)
from .relations import Edge, NormalizedEdge, RelationTypes, load_relation_types, normalize_direction, parse_relations
from .schema import validate_index

__all__ = [
    "ChangeRejected", "Diagnostic", "Diagnostics", "Edge", "FileEdit", "INDEX_SCHEMA_VERSION",
    "DEFAULT_LOAD", "GRADES", "ID_OK", "KINDS", "LAYERS", "UNLAYERED", "exam_state", "states_of", "study_state", "LOADS", "LOAD_HOURS", "MASTERY_ORDER", "all_progress",
    "all_schedules", "as_date", "ascii_id", "CALENDAR_MAX_DAYS", "append_answers", "build_calendar", "issues_path", "load_issues", "record_issue", "issues_summary", "build_digest", "duplicates", "group_labels", "kinship", "link_hints", "lonely", "misplaced", "no_year", "streak", "build_today",
    "current_stage", "done_ids", "project_lines", "due_nodes", "legacy_plans_path", "list_field", "lists_of",
    "empty_projects", "inbox_ids", "load_log", "load_projects", "load_quiz_log", "mastery_of",
    "merge_progress", "next_due_for", "place_node", "place_or_grow", "plan_growth",
    "plan_lane_growth", "plan_new_lane", "is_lane_stack",
    "projects_path", "point_ids", "by_field_and_layer", "add_question", "enrich_questions", "pool_norm", "pool_for_nodes", "load_pool", "mark_questions", "pool_stats", "save_open", "load_open", "clear_open", "LEVELS", "DEFAULT_LEVEL", "level_of", "progress_of", "progress_of_project", "stage_points",
    "schedule_of", "upgrade_v1", "record_review", "record_usage", "load_usage", "usage_summary",
    "MergeRejected", "RenameRejected", "apply_merge", "apply_rename", "backup_rename",
    "plan_merge", "plan_rename",
    "save_projects", "target_group", "wrong_nodes",
    "CELL_H", "LAYOUT_SCHEMA_VERSION", "build_project_layout", "NODE_H", "NODE_W", "WriteConflict", "apply_to_text", "backup", "commit", "digest_of", "plan",
    "split_sections",
    "build_initial_layout", "empty_layout", "find_orphans", "layout_path", "stamp", "IndexResult", "NODE_DIRS", "Node",
    "NormalizedEdge", "RE_LINK", "RE_REL_HEADER", "RelationTypes", "build_index", "content_hash",
    "dump_frontmatter", "first_paragraph", "index_path", "json_safe", "load_json", "load_node",
    "load_previous", "load_relation_types", "load_vault", "normalize_direction", "parse_relations", "read",
    "split_frontmatter", "strip_md", "validate_frontmatter", "validate_index", "walk_md", "write",
    "write_json_atomic", "yaml_scalar",
]
