// 与本地服务的三个接口对话。index 只读，layout 读写，Markdown 永不从这里改。

async function request(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const err = new Error(`${options?.method || 'GET'} ${url} → ${res.status}`)
    err.status = res.status
    try { err.body = await res.json() } catch { err.body = null }
    throw err
  }
  return res.json()
}

export const fetchIndex = () => request('/api/index')
/** layout 有多份：不给 name 就是全局图，给项目 id 就是那个项目的画布。 */
export const fetchLayout = (name = null) =>
  request(`/api/layout${name ? `?layout=${encodeURIComponent(name)}` : ''}`)

/** 把项目里已建成、还没上全局图的点同步过去（落 draft，坐标不搬）。 */
export const postSyncToGlobal = (project, baseRevision) =>
  request(`/api/projects/${encodeURIComponent(project)}/sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_revision: baseRevision }),
  })
export const fetchHealth = () => request('/api/health')

export const fetchNode = (id) => request(`/api/node/${encodeURIComponent(id)}`)

/** Markdown 写回：dry_run=true 只预览，false 才落盘（服务端写前自动备份）。 */
export const postChanges = (body) => request('/api/changes', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const patchLayout = (body, name = null) =>
  request(`/api/layout${name ? `?layout=${encodeURIComponent(name)}` : ''}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// —— 阶段 4：Inbox / 放置 / 欠账清单 / 复习 ——
export const fetchInbox = () => request('/api/inbox')
export const fetchDigest = () => request('/api/digest')
export const fetchDue = () => request('/api/review/due')

/** 把 Inbox 节点放上画布。不给 group/at 就由服务端按邻居投票找位置。 */
export const postPlace = (body) => request('/api/place', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 导入：文章 → 方案（一次 LLM）；方案 → 预览 / 落盘；仓库里可当素材的文件。 */
const postJSON = (url, body) => request(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})
export const postImportPropose = (body) => postJSON('/api/import/propose', body)
export const postImport = (body) => postJSON('/api/import', body)
export const fetchImportSources = () => request('/api/import/sources')
/** 把几个点概括成一个上位节点：让模型起草名字 / 摘要 / 正文（提议，不写盘）。 */
export const postSummarize = (body) => postJSON('/api/summarize', body)
export const fetchImportSource = (path) => request(`/api/import/source?path=${encodeURIComponent(path)}`)

/** 记一次复习：只写 review-log.json，不碰 md，也不碰 layout。grade 三档：记得 / 模糊 / 忘了。 */
export const postReview = (id, grade = '记得') => request(`/api/review/${encodeURIComponent(id)}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ grade }),
})

/** 出题：调 LLM（review 角色），可能耗时数秒。只读，不写任何文件。 */
export const postQuiz = (body) => request('/api/quiz', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** year 批量回填：一次调用把缺 year 的节点一起问完（走 LLM 的 review 角色）。
 *  只提议不写盘——写回仍然走 postChanges 的 update_frontmatter。 */
export const postYearsPropose = (body = {}) => request('/api/years/propose', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 把落在父框里的草稿挪进它那一层的泳道（`layer` 是后加的字段，早先的点没有）。 */
export const postRegroup = (body) => request('/api/place/regroup', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 给一段对话改名（留空 = 回到自动取的标题）。 */
export const renameChatSession = (session, title, project) => request(
  `/api/chat/sessions/${encodeURIComponent(session)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, project: project || null }),
  })

/** 上次出了还没交卷的那份题（出题花过钱，刷新一下不该就没了）。 */
export const fetchOpenQuiz = () => request('/api/quiz/open')

/** 明确放弃那份没答完的卷子。 */
export const dropOpenQuiz = () => request('/api/quiz/open', { method: 'DELETE' })

/** 整轮比对：我写的答案 vs 标准答案 → 漏掉点 / 记错点 / 建议档位。只读，不写盘。 */
export const postQuizDiagnose = (body) => request('/api/quiz/diagnose', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 交卷：答题明细进 quiz-log.json，每个考点按最差档位推进一次复习。仍然不碰 md。 */
export const postQuizGrade = (body) => request('/api/quiz/grade', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** AI 建议：关系、去重、分类。调用 LLM（review 角色），可能耗时数秒。 */
export const postSuggest = (body) => request('/api/suggest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 改一个知识点的 id，并把 [[链接]] / 画布位置 / 复习记录 / 学习计划一起迁走。
 *  默认 dry_run=true 只算影响面，确认后再发一次 dry_run=false。 */
export const postRename = (body) => request('/api/rename', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 把两张重复的卡并成一张。默认 dry_run=true 只算影响面。 */
export const postMerge = (body) => request('/api/merge', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 模型调用账本：今天 / 累计 / 分功能 + 最近明细。只读。 */
export const fetchUsage = () => request('/api/llm/usage')

// —— 阶段 10 / 一期：今日清单 + 项目 ——
/** 今天可以动手的事，按固定优先级排。纯排序，不调 LLM。 */
export const fetchToday = (project) =>
  request(`/api/coach/today${project ? `?project=${encodeURIComponent(project)}` : ''}`)

export const fetchProjects = () => request('/api/projects')

/** 整份替换。base_revision 对不上会 409，拿 current_revision 重新拉取后再提交。 */
export const putProjects = (body) => request('/api/projects', {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

/** 目标 → 知识点清单（LLM，learn 角色，可能耗时数秒）。只提议，不写盘。 */
export const postPlanPropose = (body) => request('/api/projects/propose', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// —— 阶段 5：vault 的 assets/ 图片 ——
export const fetchAssets = () => request('/api/assets')

/** 原始 body 直传（服务端不依赖 multipart）。name 会成为 assets/ 下的文件名。 */
export const uploadAsset = (name, file) => request(
  `/api/asset/${encodeURIComponent(name)}`,
  { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file },
)

/** 学习日历：每天建了几个、复习了几次、答了几道、烧了多少钱。纯读。 */
export const fetchCalendar = (days = 120) => request(`/api/calendar?days=${days}`)

/** 最近几轮对话：刷新页面后接着聊（纯读 .knowrary/chat/ 的留档）。 */
export const fetchChatHistory = (project, session = null) => {
  const q = new URLSearchParams()
  if (project) q.set('project', project)
  if (session) q.set('session', session)
  return request(`/api/chat/history${q.toString() ? `?${q}` : ''}`)
}

/** 聊过几段：从留档行聚合，不存会话表。每段带 `tidied`（梳理游标）。 */
export const fetchChatSessions = (project) =>
  request(`/api/chat/sessions${project ? `?project=${encodeURIComponent(project)}` : ''}`)

/** 推进梳理游标。**只在变更卡真写进 md 之后调**：梳理过但没采纳不算整理过。 */
export const markChatTidied = ({ session, upto, turns, project }) => request('/api/chat/tidied', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session, upto, turns: turns || 0, project: project || null }),
})

/** 对话式教练：SSE 流式。事件形状见 server/chat.py，onEvent 每收到一条就调一次。
 *  用 fetch + ReadableStream 而不是 EventSource：EventSource 只能 GET，发不了整段对话。 */
export async function streamChat(messages, onEvent, signal, project = null, session = null,
                                 stance = '教练') {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, project, session, stance }),
    signal,
  })
  if (!res.ok || !res.body) {
    const err = new Error(`POST /api/chat → ${res.status}`)
    err.status = res.status
    try { err.body = await res.json() } catch { err.body = null }
    throw err
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const chunks = buf.split('\n\n')
    buf = chunks.pop() || ''            // 最后一段可能只收了一半，留到下一轮
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try { onEvent(JSON.parse(line.slice(6))) } catch { /* 半条 JSON，丢掉 */ }
    }
  }
}
