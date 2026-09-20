/**
 * 对话式教练：会话、留档回放、流式收发、梳理游标。
 *
 * **会话状态在前端**：每次把整段对话发给服务端，它不持有会话。刷新丢的是这一段，
 * 但每一轮都已经留档在 `.knowrary/chat/<项目>/YYYY-MM.jsonl`（F10.7）。
 *
 * 这里只管"话怎么来回"。**卡片落地不在这儿**——写 md、上画布、补进清单那几件事
 * 要同时动图谱和项目，留在 App.vue 编排；这里只负责把卡片收进这一轮的回复里。
 */
import { computed, nextTick, reactive, ref, shallowRef } from 'vue'
import { fetchChatHistory, fetchChatSessions, markChatTidied, renameChatSession, streamChat } from '../api.js'
import { highlightPath } from '../canvas/render.js'

const TOOL_LABEL = {
  search_nodes: '在图里搜了一下', read_node: '读了一个节点', overview: '看了图谱概况',
  today: '看了今日清单', plans: '看了学习计划', quiz: '出了几道题',
  record_review: '记了一次复习', propose_changes: '拟了一张变更卡',
  propose_project: '拟了一张项目卡', propose_points: '拆了一份清单',
  propose_list_edit: '拟了一张清单卡',
}

/** 工具调用走原生协议（tool_calls），正文里本不该再出现这种块——**这是兜底**：
 *  模型见过太多围栏写法，偶尔仍会手写一个出来，它不会被执行，但也不该在屏幕上闪过。
 *  和 server/chat.py 的 strip_tools 同一个形状。流式时块可能只到一半，未闭合的也要藏掉。 */
export function stripToolBlocks(text) {
  return text.replace(/```knowrary[\s\S]*?```/g, '').replace(/```knowrary[\s\S]*$/, '').trim()
}

/**
 * @param deps graph / currentProject —— 响应式引用
 *             setBanner / pushToast / onReview —— 提示与"记了一次复习"之后要刷的东西
 */
export function useChat(deps) {
  const { graph, currentProject, setBanner, pushToast, onReview } = deps

  const chatLog = ref([])                  // [{ role, content, trace?, cards?, streaming? }]
  const chatBusy = ref(false)
  const chatSessions = ref([])             // 会话列表：从留档行聚合出来的，不是一张表
  const chatSession = ref(newSessionId())
  // 梳理游标：这一段整理到哪一条为止了（`{ upto, turns, at }`，没梳理过是 null）。
  // **它只在变更卡真写进 md 之后才推进**——梳理是这个应用里最贵的一次动作
  // （MAX_STEPS 的工具循环，每一步都把整段对话再发一遍），第二天打开同一段再点一次，
  // 没有游标就是把昨天那笔钱原样再付一遍。
  const chatTidied = shallowRef(null)
  // 口径：教练 / 面试 / 聊天。**不是三个 agent**，是三套提示词 + 三份工具白名单。
  const chatStance = ref(localStorage.getItem('knowrary-stance') || '教练')
  const chatFocus = shallowRef(null)       // 从图上点过来的节点，带进下一轮上下文
  const graphPane = ref(localStorage.getItem('knowrary-chat-graph') !== 'off')
  let chatAbort = null

  /** 游标之后还有几条没梳理。0 = 这一段已经整理干净了，按钮该是灰的。 */
  const chatFresh = computed(() => {
    const cut = Date.parse(chatTidied.value?.upto || '')
    const rows = chatLog.value.filter((m) => (m.content || '').trim())
    if (!Number.isFinite(cut)) return rows.length
    return rows.filter((m) => !m.ts || Date.parse(m.ts) > cut).length
  })

  function newSessionId() {
    return `s${Date.now().toString(36)}`
  }

  /** 换口径就新开一段。混在同一段里，前半截是面试后半截是闲聊，模型会被自己的历史带跑。 */
  function setStance(next) {
    if (!next || next === chatStance.value) return
    chatStance.value = next
    try { localStorage.setItem('knowrary-stance', next) } catch { /* 无痕模式 */ }
    if (chatLog.value.length) newChatSession()
    setBanner(`切到「${next}」口径`)
  }

  function toggleGraphPane() {
    graphPane.value = !graphPane.value
    try { localStorage.setItem('knowrary-chat-graph', graphPane.value ? 'on' : 'off') } catch { /* 无痕模式 */ }
    nextTick(() => window.dispatchEvent(new Event('resize')))   // 画布跟着重新量宽
  }

  /** 刷新页面后接着聊：把留档里最近几轮读回来。**不是多会话**——
   *  只是别把上下文弄丢。变更卡和工具痕迹不恢复（它们是当时那一刻的东西，过期了）。 */
  async function loadChatHistory(session = null) {
    if (chatBusy.value) return
    try {
      const [hist, list] = await Promise.all([
        fetchChatHistory(currentProject.value || null, session),
        fetchChatSessions(currentProject.value || null),
      ])
      chatSessions.value = list.sessions
      chatLog.value = hist.messages.map((m) => ({
        ...m, cards: [], resumed: true,
        trace: (m.trace || []).map((t) => ({ kind: 'say', text: t })) }))
      // 接着最近那一段聊：服务端不给 session 时返回的就是它，这里把 id 对上
      if (!session && hist.messages.length) chatSession.value = list.sessions[0]?.id || chatSession.value
      else if (session) chatSession.value = session
      // 游标跟着这一段一起回来：**第二天重开时按钮该不该是灰的，全靠它**
      chatTidied.value = list.sessions.find((s) => s.id === chatSession.value)?.tidied || null
    } catch { /* 读不到就当新开一段，不值得为此报错 */ }
  }

  /** 给当前这段对话改个名字。留空 = 回到自动取的名字（第一句我说的话）。 */
  async function renameSession({ session, title }) {
    if (!session) return
    try {
      await renameChatSession(session, title, currentProject.value || null)
      const list = await fetchChatSessions(currentProject.value || null)
      chatSessions.value = list.sessions
    } catch (err) {
      setBanner(`改名失败：${err.message}`, 'error')
    }
  }

  /** 新开一段：旧的还在留档里，随时切回来。 */
  function newChatSession() {
    chatSession.value = newSessionId()
    chatLog.value = []
    chatFocus.value = null
    chatTidied.value = null                 // 新的一段从零开始，没有梳理过
  }

  function pickChatSession(id) {
    if (!id || id === chatSession.value) return
    chatLog.value = []
    loadChatHistory(id)
  }

  /** 梳理时只发游标之后那一段：前面的已经采纳入库了，再喂一遍纯粹是重复付钱。
   *  **本段第一句我说的话仍然带上**——不给话题锚的话，模型看着半截对话不知道这是在聊什么，
   *  搜出来的节点和连出来的边都会跑偏。 */
  function sinceTidied(msgs) {
    const cut = Date.parse(chatTidied.value?.upto || '')
    if (!Number.isFinite(cut)) return msgs
    const fresh = msgs.filter((m) => !m.ts || Date.parse(m.ts) > cut)
    const old = msgs.length - fresh.length
    if (old <= 0) return msgs
    const anchor = (msgs.find((m) => m.role === 'user')?.content || '').slice(0, 120)
    return [{ role: 'user', content:
      `（这一段前面 ${old} 条已经梳理并入库过了，不用再看一遍。当时开头问的是：「${anchor}」。`
      + `下面是入库之后新聊的部分，只梳理这些。）` }, ...fresh]
  }

  /** 发一句话。服务端流式回，边收边渲染；工具调用和变更卡挂在这条回复下面。
   *  `opts.tidy` = 这一轮是「梳理这段」，走增量口径。 */
  async function sendChat(body, opts = {}) {
    if (chatBusy.value) return
    // 从图上点过来的节点：把摘要和出入边拼进这一轮。模型不用再自己 search 一次。
    const focus = chatFocus.value
    const text = focus
      ? `${body}\n\n（我正在看图上的「${focus.name || focus.id}」：${focus.desc || '没写摘要'}）`
      : body
    // ts 由服务端在留档时盖（done 事件带回来）：梳理游标停在某一条留档上，
    // 前端自己按本地时钟盖一个和它对不齐，判"这条在游标前还是后"就会差一截。
    const mine = reactive({ role: 'user', content: text, ts: '' })
    chatLog.value = [...chatLog.value, mine]
    // trace = 过程（"我先查一下"、工具调用、工具报错），content = 最终那段答案。
    // 混在一起的话，每次都要在一堆过程里找那几句有营养的——真实使用里最费时间的一点。
    const reply = reactive({ role: 'assistant', content: '', trace: [], cards: [], projects: [],
                             points: [], listEdits: [], questions: [], streaming: true, ts: '' })
    chatLog.value = [...chatLog.value, reply]
    chatBusy.value = true
    chatAbort = new AbortController()
    // 只把 role/content 发过去：tools / cards 是本地渲染用的，喂回模型只会干扰它
    const feed = chatLog.value.filter((m) => m.content || m.role === 'user')
    const wire = (opts.tidy ? sinceTidied(feed) : feed)
      .map((m) => ({ role: m.role, content: m.content }))
      .filter((m) => m.content.trim())
    chatFocus.value = null
    try {
      await streamChat(wire, (ev) => {
        if (ev.type === 'delta') reply.content = stripToolBlocks(reply.content + ev.text)
        else if (ev.type === 'tool') {
          // 还要接着调工具 = 刚才那段是过程，收进折叠区，正文腾空给最后那段答案
          if (reply.content.trim()) { reply.trace.push({ kind: 'say', text: reply.content }); reply.content = '' }
          reply.trace.push({ kind: 'tool', label: TOOL_LABEL[ev.name] || ev.name, summary: ev.summary })
        }
        else if (ev.type === 'card') reply.cards.push({ ...ev.card, applied: false })
        else if (ev.type === 'project') reply.projects.push({ ...ev.project, applied: false })
        else if (ev.type === 'points') reply.points.push({ ...ev.points, applied: false })
        else if (ev.type === 'list_edit') reply.listEdits.push({ ...ev.list_edit, applied: false })
        else if (ev.type === 'question') reply.questions.push({ stem: ev.stem, points: ev.points })
        else if (ev.type === 'review') { onReview(); pushToast(`已记一次「忘了」：${ev.id}`, 'info') }
        else if (ev.type === 'done') {
          // 服务端也分好了过程和答案；流式那边分错了以它为准
          if (ev.trace?.length && !reply.trace.some((t) => t.kind === 'say')) {
            reply.trace.unshift(...ev.trace.map((t) => ({ kind: 'say', text: t })))
          }
          reply.content = stripToolBlocks(ev.text) || reply.content
          // 留档的 ts 认回来：梳理游标就停在这上面，没有它这一轮在前端是"没有坐标"的
          if (ev.ts) reply.ts = ev.ts
          if (ev.user_ts) mine.ts = ev.user_ts
          // 聊到哪，图上亮哪（重构方案 §5.2）。走已有的 highlightPath，不新写高亮逻辑。
          if (ev.node_ids?.length && graph.value) {
            highlightPath(graph.value, new Set(ev.node_ids), new Set())
          }
        }
        else if (ev.type === 'error') setBanner(`对话失败：${ev.message}`, 'error')
      }, chatAbort.signal, currentProject.value || null, chatSession.value, chatStance.value)
    } catch (err) {
      if (err.name !== 'AbortError') setBanner(`对话失败：${err.body?.detail || err.message}`, 'error')
    } finally {
      reply.streaming = false
      chatBusy.value = false
      chatAbort = null
    }
  }

  /** 把梳理游标推到第 i 条回复为止。**只在写盘成功之后调**。
   *  服务端只进不退，所以这里不用操心先写后面那张卡、再回头写前面那张的顺序。 */
  async function advanceTidied(i) {
    const upto = chatLog.value[i]?.ts
      || [...chatLog.value.slice(0, i + 1)].reverse().find((m) => m.ts)?.ts
    if (!upto || !chatSession.value) return          // 没有坐标就不动游标，下次全量重梳
    try {
      const res = await markChatTidied({ session: chatSession.value, upto,
                                         turns: i + 1, project: currentProject.value || null })
      chatTidied.value = res.tidied || chatTidied.value
    } catch { /* 游标是优化不是真值：推不动只是下次多花一次钱，不该打断写入的成功提示 */ }
  }

  function stopChat() { chatAbort?.abort() }

  return {
    chatLog, chatBusy, chatSessions, chatSession, chatTidied, chatStance, chatFocus,
    graphPane, chatFresh,
    newSessionId, setStance, toggleGraphPane, loadChatHistory, renameSession,
    newChatSession, pickChatSession, sinceTidied, sendChat, advanceTidied, stopChat,
  }
}
