"""Knowrary 核心库：md 解析、关系归一、索引生成、契约校验。

CLI（knowrary.py）与后续的本地服务（FastAPI）都只依赖这里，避免两套解析实现漂移。
"""
from .diagnostics import Diagnostic, Diagnostics
from .coach import build_today, current_stage, project_lines
from .calendar import MAX_DAYS as CALENDAR_MAX_DAYS, build_calendar, streak
from .digest import (build_digest, drafts, duplicates, group_labels, kinship, link_hints, lonely,
                     lonely_batches, misplaced, no_year,
                     off_canvas, project_layouts, squatted)
from .audit import THIN_BODY as AUDIT_THIN_BODY, precheck as audit_precheck
from .cards import (KINDS as CARD_KINDS, applied as card_applied, is_applied as card_is_applied, audited as card_audited,
                    audits as card_audits, cards_path, last_audit as card_last_audit, load as load_cards,
                    new_card_id, proposed as card_proposed, revised as card_revised,
                    revisions as card_revisions, stats as card_stats)
from .issues import (issues_path, load as load_issues, record as record_issue,
                     summary as issues_summary)
from .index import INDEX_SCHEMA_VERSION, IndexResult, build_index, content_hash, index_path, load_previous
from .layout import (CELL_H, LAYOUT_SCHEMA_VERSION, NODE_H, NODE_W, build_compare_layout, build_initial_layout, build_project_layout, empty_layout,
                     find_orphans, layout_path, stamp)
from .mdio import (NODE_DIRS, RE_ID_OK, RE_LINK, RE_REL_HEADER, dump_frontmatter, first_paragraph, json_safe,
                   load_json, read, split_frontmatter, strip_md, user_dir, walk_md, write, write_json_atomic,
                   yaml_scalar)
from .parser import (AGGREGATE_TYPES, LAYERS, OFF_CANVAS_TYPES, UNLAYERED, Node, digest_of,
                     is_aggregate, load_node, load_vault, validate_frontmatter)
from .placement import (by_field_and_layer, growth_blocker, inbox_ids, is_lane_stack, place_node,
                        place_or_grow, plan_field_group, plan_growth, plan_lane_growth, plan_new_lane, target_group,
                        decide_group, inbox_cluster, in_box)
from . import zones
from .projects import (DEFAULT_LEVEL, DEFAULT_LOAD, ID_OK, KINDS, LEVELS, level_of, exam_state, states_of, study_state, LOAD_HOURS, LOADS, MASTERY_ORDER, all_progress,
                       all_schedules, as_date, ascii_id, done_ids, empty_projects, legacy_plans_path,
                       list_field, lists_of, load_projects, mastery_of, merge_progress, point_ids,
                       progress_of, progress_of_project, projects_path, save_projects, schedule_of,
                       stage_points, upgrade_v1)
from .pool import (add as add_question, enrich as enrich_questions, for_nodes as pool_for_nodes,
                   load_pool, mark as mark_questions, norm as pool_norm, pool_stats)
from .quiz import append_answers, clear_open, load_open, load_quiz_log, save_open, wrong_nodes
from .settings import (DEFAULTS as SETTINGS_DEFAULTS, audit_force_allowed, audit_on,
                       load_settings, save_settings, review_in_chat, review_on)
from .usage import load_usage, note_prefix, record as record_usage, summary as usage_summary
from .articles import ArticleConflict, ArticlePlan, plan_article, render_article, safe_name, write_article
from .sources import (Resolver as SourceResolver, article_link, backlink_refs as source_backlink_refs,
                      backlinks as source_backlinks,
                      clean_sources, sources_of)
from .vault_config import (ArticlesNeedMove, VaultConfigRejected, articles_dir, candidates as articles_dir_candidates,
                           list_articles, load_vault_config, save_articles_dir, save_models_dir)
from .catalog import (COMMON as CATALOG_FIELDS, LINEAGE_FILE, SAMPLE as CATALOG_SAMPLE, SAMPLE_LINEAGE as CATALOG_SAMPLE_LINEAGE,
                      bare_lineage, is_family, is_lineage, link_models, load_catalog, parse_family, parse_lineage)
from .article import (build_article_prompt, cards_from_index, cards_from_nodes, describe_points,
                      describe_related, select_linkable, select_related)
from .sections import Heading, describe_outline, extract_section, find_heading, outline
from .facts import (COMPARE_HEADING, FACTS_HEADING, FM_DIMENSIONS, Fact, bare_compare_headings,
                    compare_targets, facts_of, parse_facts, stray_facts)
from .compare import (COMPARE_TYPE, GAP_RATIO, gaps as compare_gaps, groups as compare_groups,
                      table as compare_table)
from .importing import (CONFIDENCE_DIRECT, ENRICH_MARK, ImportTarget, Translation, promote_in_plan,
                        rename_in_plan, translate)
from .llmjson import carve as carve_json, parse_json
from .proposal import (MAX_ARTICLE_CHARS, NEAR_MISS_RATIO, check_length, isolated, near_misses,
                       normalize_claims, project_points)
from .merge import MergeRejected, apply_merge, plan_merge
from .type_register import TypeRejected, register_type
from .edge_checks import (VERDICTS as EDGE_VERDICTS, fix_changes as edge_fix_changes, fresh_checks as fresh_edge_checks,
                          mark_check as mark_edge_check, record_checks as record_edge_checks)
from .near import (candidates as near_candidates, for_changes as near_for_changes, relink as relink_changes,
                   relink_in_plan)
from .pending import (LOCK as PENDING_LOCK, add_curated, load_curated, add_home, add_pending, add_type_proposals, edge_key, settle_type, load_homes, load_pending, pending_path, reject_pending,
                      remove_pending, restore_rejected)
from .rename import RenameRejected, RolledBack, apply_rename, backup_rename, guarded, plan_rename
from .review import GRADES, due_nodes, load_log, next_due_for, record_review
from .schools import DOMAIN_TYPE, LINE_TYPES, SCHOOL_TYPE, line_homes, school_of, schools
from .writer import (CHANGE_TYPES, EDITABLE_FIELDS, ChangeRejected, FileEdit, WriteConflict,
                     apply_to_text, backup, commit, plan, split_sections)
from .relations import Edge, NormalizedEdge, RelationTypes, load_relation_types, normalize_direction, parse_relations
from .schema import validate_index

__all__ = [
    "ChangeRejected", "Diagnostic", "Diagnostics", "Edge", "FileEdit", "INDEX_SCHEMA_VERSION",
    "DEFAULT_LOAD", "GRADES", "ID_OK", "KINDS", "LAYERS", "UNLAYERED", "exam_state", "states_of", "study_state", "LOADS", "LOAD_HOURS", "MASTERY_ORDER", "all_progress",
    "all_schedules", "as_date", "ascii_id", "CALENDAR_MAX_DAYS", "append_answers", "build_calendar", "issues_path", "load_issues", "record_issue", "issues_summary", "build_digest", "drafts", "duplicates", "group_labels", "kinship", "link_hints", "lonely", "lonely_batches", "growth_blocker", "SCHOOL_TYPE", "DOMAIN_TYPE", "LINE_TYPES", "schools", "school_of", "line_homes", "misplaced", "no_year", "squatted", "off_canvas", "project_layouts", "streak", "build_today",
    "current_stage", "done_ids", "project_lines", "due_nodes", "legacy_plans_path", "list_field", "lists_of",
    "empty_projects", "inbox_ids", "load_log", "load_projects", "load_quiz_log", "mastery_of",
    "merge_progress", "next_due_for", "place_node", "place_or_grow", "plan_growth",
    "plan_lane_growth", "plan_new_lane", "is_lane_stack",
    "projects_path", "point_ids", "by_field_and_layer", "in_box", "zones", "add_question", "enrich_questions", "pool_norm", "pool_for_nodes", "load_pool", "mark_questions", "pool_stats", "save_open", "load_open", "clear_open", "LEVELS", "DEFAULT_LEVEL", "level_of", "progress_of", "progress_of_project", "stage_points",
    "schedule_of", "upgrade_v1", "record_review", "record_usage", "note_prefix", "load_usage", "usage_summary",
    "load_settings", "save_settings", "review_in_chat", "review_on", "audit_on", "audit_force_allowed",
    "SETTINGS_DEFAULTS", "ArticleConflict", "ArticlePlan", "plan_article", "render_article", "safe_name",
    "write_article", "SourceResolver", "article_link", "source_backlink_refs", "source_backlinks", "clean_sources", "sources_of",
    "ArticlesNeedMove", "VaultConfigRejected", "articles_dir", "articles_dir_candidates", "list_articles",
    "load_vault_config", "save_articles_dir", "save_models_dir",
    "CATALOG_FIELDS", "CATALOG_SAMPLE", "CATALOG_SAMPLE_LINEAGE", "LINEAGE_FILE", "bare_lineage", "is_family", "is_lineage",
    "link_models", "load_catalog", "parse_family", "parse_lineage",
    "audit_precheck", "AUDIT_THIN_BODY",
    "CARD_KINDS", "card_applied", "card_is_applied", "card_audited", "card_audits", "card_last_audit", "card_proposed", "card_revised", "card_revisions", "card_stats", "cards_path",
    "load_cards", "new_card_id",
    "build_article_prompt", "cards_from_index", "cards_from_nodes", "describe_points", "describe_related",
    "select_linkable", "select_related",
    "Heading", "describe_outline", "extract_section", "find_heading", "outline",
    "AGGREGATE_TYPES", "OFF_CANVAS_TYPES", "is_aggregate",
    "COMPARE_HEADING", "FACTS_HEADING", "FM_DIMENSIONS", "Fact", "bare_compare_headings",
    "compare_targets", "facts_of", "parse_facts", "stray_facts",
    "COMPARE_TYPE", "GAP_RATIO", "compare_gaps", "compare_groups", "compare_table",
    "CONFIDENCE_DIRECT", "ENRICH_MARK", "ImportTarget", "Translation", "translate", "rename_in_plan",
    "promote_in_plan",
    "carve_json", "parse_json",
    "MAX_ARTICLE_CHARS", "NEAR_MISS_RATIO", "check_length", "isolated", "near_misses",
    "normalize_claims", "project_points",
    "EDGE_VERDICTS", "edge_fix_changes", "fresh_edge_checks", "mark_edge_check", "record_edge_checks",
    "near_candidates", "near_for_changes", "relink_changes", "relink_in_plan", "PENDING_LOCK", "add_curated", "load_curated", "add_pending", "add_type_proposals", "settle_type", "TypeRejected", "register_type", "edge_key", "load_pending", "pending_path", "reject_pending", "remove_pending", "restore_rejected", "add_home", "load_homes", "plan_field_group",
    "MergeRejected", "RenameRejected", "RolledBack", "guarded", "apply_merge", "apply_rename", "backup_rename",
    "plan_merge", "plan_rename",
    "save_projects", "target_group", "decide_group", "inbox_cluster", "wrong_nodes",
    "CELL_H", "LAYOUT_SCHEMA_VERSION", "build_project_layout", "NODE_H", "NODE_W", "WriteConflict", "apply_to_text", "backup", "commit", "digest_of", "plan",
    "split_sections",
    "build_compare_layout", "build_initial_layout", "empty_layout", "find_orphans", "layout_path", "stamp", "IndexResult", "NODE_DIRS", "Node",
    "NormalizedEdge", "RE_ID_OK", "RE_LINK", "RE_REL_HEADER", "RelationTypes", "build_index", "content_hash",
    "dump_frontmatter", "first_paragraph", "index_path", "json_safe", "load_json", "load_node",
    "load_previous", "load_relation_types", "load_vault", "normalize_direction", "parse_relations", "read",
    "split_frontmatter", "strip_md", "validate_frontmatter", "validate_index", "walk_md", "write",
    "user_dir", "write_json_atomic", "yaml_scalar",
]
