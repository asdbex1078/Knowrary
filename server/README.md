# server —— Knowrary 本地服务（FastAPI）

只做三件事：**只读** index、**读写** layout、静态托管前端产物。
**改 md 只能走 ChangeSet**（`POST /api/changes`，默认只预览）；复习与测验只写 `.knowrary/` 下的记录，一律不碰 Markdown。

## 启动

```bash
python3 -m venv .venv                                  # 只需一次
.venv/bin/pip install -r server/requirements-dev.txt   # 运行期依赖 + 自测依赖
./server/dev.sh            # 默认 127.0.0.1:8765，vault = 仓库根目录
KNOWRARY_VAULT=/别的/vault ./server/dev.sh 9000
```

浏览器打开 <http://127.0.0.1:8765/>（托管 `web/dist`）。改前端时另开一个终端：
`cd web && npm run dev`（5173，`/api` 代理到 8765）。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | vault 路径、index / layout revision、节点边统计 |
| GET | `/api/index` | 派生索引全量（阶段 1 的 core 生成，md 变化时自动重建并落盘） |
| GET | `/api/layout` | `{layout, orphans, index_revision, generated}`；没有 layout.json 时按 field / 目录自动生成 |
| PATCH | `/api/layout` | 部分文档合并写入，带 `base_revision` 乐观并发 |
| GET | `/api/node/:id` | md 原文 + 元数据 + 出入边 + Obsidian 链接 |
| GET | `/api/inbox` · POST `/api/place` | 未上画布的节点 / 放上画布（只写 layout） |
| GET | `/api/digest` | 欠账清单：草稿 / 桥 / 重复 / stub / 环 |
| GET / PUT | `/api/projects` | 项目（一组 node_id + N 份清单）：整份替换 + `base_revision`；进度五档与时间账现算不落盘 |
| POST | `/api/projects/propose` | 目标 → 知识点清单（LLM **learn** 角色，三种口径），只提议不落盘 |
| GET | `/api/calendar` | 学习日历：每天建了多少 / 复习多少 / 答题多少 / 花了多少，**纯读、全派生** |
| GET | `/api/coach/today` | 今日清单：错题 > 到期 > 未建 > 只有壳 > Inbox，**不调 LLM**；`?project=` 只过滤建设项，复习仍是全局的 |
| POST | `/api/changes` | **Markdown 写回唯一入口**，默认 `dry_run=true` 只出 diff |
| POST | `/api/suggest` | AI 建议关系 / 去重 / 分类，走 LLM review 角色 |
| GET | `/api/review/due` · POST `/api/review/:id` | 到期复习 / 记一次复习（body 可选 `grade` 三档） |
| POST | `/api/quiz` · `/api/quiz/diagnose` · `/api/quiz/grade` | 出题（LLM）/ 整轮比对作答（LLM，只读）/ 交卷 |
| GET/POST | `/api/asset(s)` | vault `assets/` 下的图片 |

`PATCH` 语义（JSON Merge Patch 风格）：

```jsonc
{
  "base_revision": 7,
  "nodes":  { "cpu": { "x": 120, "y": 40 },     // 字段级合并，其余字段保留
              "旧节点": null },                  // 条目置 null = 删除该条目
  "groups": { "g-JVM": { "collapsed": true } },
  "edges":  { "cpu->寄存器#部件": { "vertices": [{ "x": 1, "y": 2 }] } },
  "refs": [], "notes": [], "images": [],        // 带 id 的小集合：给出即整体替换
  "viewport": { "zoom": 0.6, "cx": 1200, "cy": 800 }
}
```

- `base_revision` 与服务端不一致 → **409**，响应带 `current_revision`，客户端重新 GET 后重放。
- 新增条目字段不全、引用不存在的分组、未知字段 → **422**，不写盘。
- 写盘走临时文件 + rename（原子），崩溃不会留半个 JSON。
- 引用不到的节点 / 分组 / 图片 / 边只进 `orphans` 报告，**绝不自动删除**用户数据。

## 文件

| 文件 | 作用 |
| --- | --- |
| `paths.py` | vault 解析（`KNOWRARY_VAULT`）与 `tools/knowrary/core` 注入 |
| `contracts.py` | 契约的 pydantic v2 模型：index（只读）、layout + LayoutPatch、ChangeSet、Suggest、Quiz |
| `llm_call.py` | 按角色取 provider + 解析回答 JSON，suggest / quiz 共用 |
| `suggest.py` · `quiz.py` · `projects.py` · `chat.py` | AI 建议 / 出题与交卷 / 项目编排 / 对话教练，都只提议或只写自己的记录 |
| `index_service.py` | 按 md 文件指纹缓存索引，变化即重建并写 `.knowrary/index.json` |
| `layout_store.py` | layout 读写：初始生成、部分合并、revision 校验、原子写、孤立引用 |
| `app.py` | FastAPI 路由与静态托管 |
| `tests/run.py` | 服务层自测（TestClient + 临时 vault，42 个用例） |

初始布局生成与孤立引用判定住在 `tools/knowrary/core/layout.py`（零第三方依赖），
所以 `python3 tools/knowrary/knowrary.py layout init/check` 不需要 .venv 也能用。

## 自测

```bash
.venv/bin/python server/tests/run.py          # 42 个用例
.venv/bin/python server/tests/run.py revision # 只跑名字含 revision 的
```
