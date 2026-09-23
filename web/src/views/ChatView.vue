<script setup>
/**
 * 对话式教练：聊着学，学完一键入库。
 *
 * **聊天是入口，图是产物。** 这一屏解决的是启动成本——想学一个点，原来要在
 * 计划 / 今日 / 建节点 / 写正文 / 出题几个面板之间跳，现在一句话就够。
 * 但知识仍然只住在 md 里：模型给的是**变更卡**，看过 diff 点「写入」才落盘（4.4）。
 *
 * 会话状态在前端（每次把整段对话发过去），服务端不持有——刷新页面不会丢一半上下文。
 */
/**
 * 对话视图（三期：从 420px 抽屉搬成全屏主体）。
 *
 * **对话不是一个工具面板，它是主界面。** 挤在抽屉里既读不下长回答，
 * 也看不见正在聊的那些点在图上是什么位置——而后者正是这套系统区别于"又一个聊天记录"的地方。
 *
 * 右侧留给画布（在 App.vue 里，这里只管让出宽度）。三条交互把对话和图绑在一起：
 * 聊到哪图上亮哪、点图上的节点带进对话、变更卡写入后新点当场以 draft 出现。
 *
 * 知识仍然只住在 md 里：模型给的是**变更卡**，看过 diff 点「写入」才落盘（4.4）。
 * 会话状态在前端，服务端不持有；每一轮都已经留档在 .knowrary/chat/<项目>/。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import Markdown from '../ui/Markdown.vue'
import Popover from '../ui/Popover.vue'
import { pairDiff, parseDiff } from '../ui/diff.js'

const props = defineProps({
  busy: { type: Boolean, default: false },
  messages: { type: Array, default: () => [] },   // [{ role, content, trace?, cards?, streaming?, failed? }]
  stance: { type: String, default: '教练' },
  sessions: { type: Array, default: () => [] },   // 从留档聚合出来的会话列表
  session: { type: String, default: '' },
  focus: { type: Object, default: null },         // 从图上点过来的节点：带进下一轮上下文
  graphOpen: { type: Boolean, default: true },
  tidied: { type: Object, default: null },        // 梳理游标：{ upto, turns, at }，没梳理过是 null
  fresh: { type: Number, default: 0 },            // 游标之后还有几条没梳理
  auditOn: { type: Boolean, default: false },     // 写入审核开着：变更卡先审再写
  forceAllowed: { type: Boolean, default: true }, // 挡下之后给不给「仍然写入」
})
const emit = defineEmits(['send', 'stop', 'retry', 'apply', 'audit', 'preview', 'revise', 'undo-revise', 'apply-project', 'apply-points',
                          'apply-list-edit', 'goto',
                          'new-session', 'pick-session', 'drop-focus', 'toggle-graph', 'stance',
                          'rename-session', 'archive-session', 'delete-session', 'close'])


/**
 * 变更卡上能改的字段：desc 是显示层（画布上就这一句），body 是笔记层（整篇落盘）。
 * 模型写的只是初稿，**写入前在卡上改**比写完再去详情面板改省一步，而且 desc 还没上图。
 * 只开放正文类字段；边和 layer/year 这些改错会带偏整张图，仍然让它重出一张卡。
 */
const KIND_LABEL = { create_node: '新建', update_body: '整篇替换', append_body: '尾部追加', update_frontmatter: '改摘要' }
function editable(card) {
  return (card.changes || []).filter((ch) =>
    ch.type === 'create_node' || ch.type === 'update_body' || ch.type === 'append_body'
    || (ch.type === 'update_frontmatter' && ch.fields && 'desc' in ch.fields))
}
function toggleEdit(card) {
  card.editing = !card.editing
  // 新建节点模型可能没给 body（只写了 desc）：给个空正文框，人可以自己补
  for (const ch of editable(card)) {
    if (ch.type === 'create_node') { ch.fields = ch.fields || {}; if (ch.body == null) ch.body = '' }
  }
}
// 改过之后 diff 是旧的，真写入时服务端按 changes 现算，所以只是提示，不挡写入
function touch(card) { card.stale = true }

// 点过的卡折成一行：它已经落盘了，再整张摊着只是挤占后面的对话；但要回看当时写了什么，点标题就展开。
// 没点的卡永远整张摆着——切项目、刷新、隔天再打开都一样（它们跟着留档一起回来）
function folded(card) { return card.applied && !card.open }
function unfold(card) { if (card.applied) card.open = !card.open }

// —— 写入审核：挂在卡上，看得见谁在审、等了多久、审出了什么（2026-09-23 之前是默默审的）——
const VERDICT = { pass: '审核通过', warn: '有意见（不挡）', block: '被挡下' }
const VERDICT_CHIP = { pass: 'm-mastered', warn: 'm-due', block: 'm-drop' }
// 审核中的秒表：只在有卡在审时才走，免得整页每秒重渲染
const now = ref(Date.now())
let ticker = null
const auditing = computed(() => props.messages.some((m) => (m.cards || []).some((c) => c.auditing || c.revising)))
watch(auditing, (on) => {
  clearInterval(ticker)
  ticker = on ? setInterval(() => { now.value = Date.now() }, 1000) : null
  now.value = Date.now()
}, { immediate: true })
onBeforeUnmount(() => clearInterval(ticker))

function auditFresh(c) { return !!c.audit && c.auditFor === JSON.stringify(c.changes) }
function waited(c) { return Math.max(0, Math.round((now.value - (c.auditing || c.revising?.at || now.value)) / 1000)) }
function auditTag(a) {
  if (!a.checked && !a.forced) return '只跑了确定性检查'
  if (a.forced) return '强制写入，没问模型'
  return a.verdict === 'warn' ? `${a.issues.filter((x) => x.code === 'llm').length || a.issues.length} 条意见（不挡）`
    : VERDICT[a.verdict] || a.verdict
}
function auditMeta(a) {
  const bits = []
  if (a.checked && a.model) bits.push(`${a.model} · ${Math.round((a.ms || 0) / 1000)} 秒`)
  if (a.reused) bits.push('写入时沿用了这份结论，没再问模型')
  if (a.model_failed) bits.push('模型没给出可用结论，这次放行')
  if (a.at) bits.push(a.at.slice(5, 16).replace('T', ' '))
  return bits.join(' · ')
}
function blockedNow(c) { return props.auditOn && auditFresh(c) && c.audit.verdict === 'block' }
function working(c) { return !!c.auditing || !!c.revising }

// —— 审完之后的三条路：按意见修改 / 原样写入 / 自己「改一改」（2026-09-23）——
// 以前有意见时只剩一个「照写」，看着像"照审核者的意思写"，其实是原样写；想按意见改只能自己动手抄。
// 意见可以逐条勾：勾上的交给 learn 角色改一版，改完停在卡上给人看，点写入时照常重审。
function pickable(c) { return props.auditOn && !c.applied && auditFresh(c) && c.audit.issues?.length > 0 }
function picked(c) { return (c.audit?.issues || []).filter((_, k) => !c.skip?.[k]) }
function togglePick(c, k) { c.skip = { ...(c.skip || {}), [k]: !c.skip?.[k] } }
function revise(c, i, j) { emit('revise', { card: c, i, j, issues: picked(c) }) }
// 审完有意见（warn / block）时，「按意见修改」是主按钮，原样写入退成次要的
function reviseFirst(c) { return pickable(c) && c.audit.verdict !== 'pass' }
function writeLabel(c) {
  if (!props.auditOn) return '写入'
  if (!auditFresh(c)) return '审核并写入'
  return c.audit.verdict === 'warn' ? '忽略意见，原样写入' : '写入'
}
function writeHint(c) {
  if (!props.auditOn) return '写前自动备份到 .knowrary/backup/'
  if (!auditFresh(c)) {
    if (c.revised) return 'AI 按意见改过了，看一眼改动；写入前会重新审'
    return c.audit ? '卡改过了，写入前会重新审' : '先由 review 角色审一遍，没意见直接写；有意见停下来给你看'
  }
  if (c.audit.verdict === 'block') return '被挡下：按意见修改，或者「改一改」自己改；确认它看走眼了才强制写入'
  if (c.audit.verdict === 'warn') return '按意见修改 = learn 角色照勾上的意见改一版，改完先给你看；原样写入 = 不管意见，按卡上现在的写'
  return '已经审过，写入不再等模型；写前自动备份'
}
function revisedMeta(r) {
  const bits = []
  if (r.model) bits.push(`${r.model} · ${Math.round((r.ms || 0) / 1000)} 秒`)
  if (r.at) bits.push(r.at.slice(5, 16).replace('T', ' '))
  return bits.join(' · ')
}

/** 折叠条上直接写清楚这一轮都动了什么，不点开也知道它去查了图还是出了题。 */
function traceTools(m) {
  return [...new Set((m.trace || []).filter((t) => t.kind === 'tool').map((t) => t.label))]
}
// 三档口径。**不是三个 agent**：同一条链路、同一张图、同一套复习记录，
// 换的只是系统提示词和工具白名单。
const STANCES = {
  教练: { tip: '盯进度：今天学什么、该复习了、计划来不来得及。工具全开' },
  面试: { tip: '真的在考你：一次一问、答完追问一层。**不给入库工具**——边考边改图谱等于开卷' },
  聊天: { tip: '只把事情讲清楚，不催进度不出题。说「这段学完了」才提议入库' },
}

// 开场白：懒人入口的关键是**不用想第一句说什么**
const STARTERS = [
  { t: '今天学什么', q: '看一眼我的今日清单和计划，告诉我今天该动手的是哪几件，按顺序说。' },
  { t: '考考我', q: '从我到期该复习的节点里挑几个考我，一次一题，我答完你再对答案。' },
  { t: '讲个概念', q: '我想搞懂：' },
]

// 「梳理」不是新链路，就是一条写死的指令：让它回头看这一整段，分清哪些该新建、
// 哪些该往已有节点里补，然后出一张卡。**用户说的"最后来一个按钮整理知识"就是这个。**
const TIDY = `把我们刚才这一段对话梳理一遍，整理进我的知识图谱：

1. 先 search_nodes 看哪些概念图里已经有、哪些在计划里还没建、哪些完全没有；
2. 已经有正文的：**一次 read_node 把它们全读进来**（\`ids\` 一次最多 5 个，别一个一个读），
   这次聊出了笔记里没有的东西，就用 append_body 往正文尾部补一段，
   带个 \`##\` 小标题（比如「## 和 X 的区别」）。只补这次真聊清楚的那点，别重写整篇；
3. 还没建的：create_node，正文按格式说明里那个骨架写——它是什么、为什么需要它、
   怎么运作、容易和什么搞混、我当时是怎么想通的，最后一节「线头」列出聊到但没建成节点的
   人物 / 学科 / 相邻概念并抄进 tags。**我没懂的地方写「没懂：…」留着**，别替我编圆；
   但我说过的人物、年份、出处一个都别精简掉。带上 layer 和 year（有确切年份才填）；
4. 概念之间这次聊到的关系，用 add_edge 连上。

一次 propose_changes 出一张卡就行，别拆成好几条消息。没什么值得入库的就直说。`

const text = ref('')
const box = ref(null)

const empty = computed(() => !props.messages.length)

/** 新内容进来就贴着底部。人正往回翻时不要抢滚动。
 *
 * **但载入一段旧会话必须直接落到最底。** 那个"离底部 260px 以内才滚"的守卫是给流式输出用的
 * （人正往回翻时别抢滚动），首屏却正好卡在它上面：scrollTop 还是 0、整段历史很高，
 * 距离远超 260，于是一次都不滚，进来就停在我说的第一句话上。
 * 所以"从没有内容到有内容"这一跳单独放行，不看距离。
 */
let hadMessages = false
watch(() => props.messages.map((m) => m.content).join('|'), async () => {
  await nextTick()
  const el = box.value
  const fresh = !hadMessages && props.messages.length > 0    // 首屏载入、或换会话后第一次有内容
  hadMessages = props.messages.length > 0
  if (!el) return
  if (fresh || el.scrollHeight - el.scrollTop - el.clientHeight < 260) el.scrollTop = el.scrollHeight
})

function send(q, opts = {}) {
  const body = (q ?? text.value).trim()
  if (!body || props.busy) return
  emit('send', body, opts)              // opts.tidy = 这一轮是梳理，上层只发游标之后那一段
  text.value = ''
}

const sessionLabel = computed(() => {
  const hit = props.sessions.find((s) => s.id === props.session)
  return hit ? `${hit.title}（${hit.turns} 条）` : '这段（还没说话）'
})

// 会话列表：没归档的平铺，归档的折叠在底下。**归档只是收起来**，点开照样能切回去接着聊
const shelvedCount = computed(() => props.sessions.filter((s) => s.archived).length)
const showShelved = ref(false)
const listed = computed(() => {
  const live = props.sessions.filter((s) => !s.archived)
  return showShelved.value ? [...live, ...props.sessions.filter((s) => s.archived)] : live
})
// 删除走两步：先点垃圾桶，这一行变成确认条。删了就没了，不值得省这一下
const confirming = ref('')

function pickSession(s, close) {
  confirming.value = ''
  if (s.id !== props.session) emit('pick-session', s.id)
  close()
}

function doDelete(s) {
  confirming.value = ''
  emit('delete-session', s.id)
}

function deleteTip(s) {
  return s.tidied ? '删除：梳理过的内容已经在节点里了，删的只是对话本身'
    : '删除：这段从没梳理过，里面聊到的东西还没进图谱，删了就找不回来'
}

// 改名：默认名是第一句我说的话（自动取的），改过之后存一张贴纸
const naming = ref(false)
const nameDraft = ref('')
const nameBox = ref(null)
const autoTitle = computed(() =>
  props.sessions.find((s) => s.id === props.session)?.auto || '这段对话')

function startName() {
  const hit = props.sessions.find((s) => s.id === props.session)
  nameDraft.value = hit?.renamed ? hit.title : ''
  naming.value = true
  nextTick(() => nameBox.value?.focus())
}

function saveName() {
  if (!naming.value) return
  naming.value = false
  emit('rename-session', { session: props.session, title: nameDraft.value.trim() })
}

// 梳理是这里最贵的一次动作（一轮工具循环，每一步都把整段对话再发一遍）。
// 游标之后没有新内容就直接置灰：**第二天重开同一段再点一次，等于把昨天那笔钱再付一遍。**
const tidyReady = computed(() => props.messages.length >= 2 && props.fresh > 0)
const tidyTip = computed(() => {
  if (props.messages.length < 2) return '先聊几句，再让我整理'
  if (!props.fresh) {
    const at = (props.tidied?.at || '').slice(5, 16).replace('T', ' ')
    return `这一段已经梳理并入库过了${at ? `（${at}）` : ''}，没有新内容——再点一次只是重复花钱。`
      + '接着聊几句，按钮就会亮回来'
  }
  return props.tidied
    ? `只梳理上次入库之后新聊的 ${props.fresh} 条：该新建的新建、该补的往已有节点里补，出一张变更卡`
    : '回头看这一整段：该新建的新建、该补的往已有节点里补，出一张变更卡'
})

function starter(s) {
  if (s.q.endsWith('：')) { text.value = s.q; return }   // 要我补一句的，只填进输入框
  send(s.q)
}

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
}
</script>

<template>
  <section class="chat-view">
    <!-- 会话条：多段对话在这里切。会话不是一张表，是留档行上的一个标签聚合出来的 -->
    <div class="chat-bar">
      <!-- 改名：名字默认取第一句我说的话，改过的存成一张贴纸（不建会话表） -->
      <input v-if="naming" ref="nameBox" v-model="nameDraft" class="sess-pick sess-name"
             :placeholder="autoTitle" title="回车保存，Esc 取消；留空就回到自动取的名字"
             @keydown.enter.prevent="saveName" @keydown.esc="naming = false" @blur="saveName" />
      <Popover v-else align="start" :width="320">
        <template #trigger="{ toggle, open }">
          <button class="sess-pick sess-trigger" :class="{ active: open }" title="切换、归档或删除对话"
                  @click="confirming = ''; toggle()">
            <span class="sess-label">{{ sessionLabel }}</span>
            <Icon name="chevronDown" :size="12" class="caret" />
          </button>
        </template>
        <template #default="{ close }">
          <div class="sess-list">
            <div v-if="!listed.length" class="sess-empty">还没有别的对话</div>
            <template v-for="s in listed" :key="s.id">
              <div v-if="confirming === s.id" class="sess-confirm" :title="deleteTip(s)">
                <span>删除「{{ s.title }}」？{{ s.tidied ? '' : '没梳理过' }}</span>
                <button class="btn tiny danger" @click="doDelete(s)">删除</button>
                <button class="btn subtle tiny" @click="confirming = ''">取消</button>
              </div>
              <div v-else class="sess-row" :class="{ on: s.id === session, shelved: s.archived }"
                   @click="pickSession(s, close)">
                <span class="sess-title">{{ s.title }}</span>
                <span class="dim">{{ s.turns }} 条</span>
                <button class="icon-btn ghost tiny"
                        :title="s.archived ? '放回列表' : '归档：不占列表，随时能在「已归档」里找回来'"
                        @click.stop="emit('archive-session', { session: s.id, archived: !s.archived })">
                  <Icon :name="s.archived ? 'undo' : 'archive'" :size="13" />
                </button>
                <button class="icon-btn ghost tiny danger" :title="deleteTip(s)"
                        @click.stop="confirming = s.id">
                  <Icon name="trash" :size="13" />
                </button>
              </div>
            </template>
          </div>
          <template v-if="shelvedCount">
            <div class="pop-sep" />
            <button class="pop-item" @click="showShelved = !showShelved">
              <Icon name="archive" :size="13" />{{ showShelved ? '收起已归档' : '已归档' }}
              <span class="hint">{{ shelvedCount }}</span>
            </button>
          </template>
        </template>
      </Popover>
      <button class="icon-btn ghost tiny" :title="naming ? '收起' : '给这段对话改个名字'"
              @click="naming ? (naming = false) : startName()">
        <Icon name="pencil" :size="13" />
      </button>
      <select class="sess-pick" style="max-width: 112px" :value="stance"
              :title="STANCES[stance]?.tip" @change="emit('stance', $event.target.value)">
        <option v-for="(v, k) in STANCES" :key="k" :value="k">口径 · {{ k }}</option>
      </select>
      <button class="btn subtle tiny" title="新开一段（旧的还在，随时切回来）" @click="emit('new-session')">
        <Icon name="plus" :size="13" />新的一段
      </button>
      <!-- 一直摆着（聊之前是灰的）：藏起来的入口等于没有入口 -->
      <button class="btn subtle tiny" :disabled="busy || !tidyReady" :title="tidyTip"
              @click="send(TIDY, { tidy: true })">
        <Icon name="checklist" :size="13" />梳理这段<span v-if="tidied && fresh" class="dim">
          · 新 {{ fresh }}</span>
      </button>
      <span class="grow" />
      <button class="icon-btn ghost tiny" :title="graphOpen ? '收起右侧的图' : '展开右侧的图'"
              @click="emit('toggle-graph')">
        <Icon :name="graphOpen ? 'fold' : 'unfold'" :size="14" />
      </button>
      <!-- 关掉对话＝回到画布。没有这个按钮时只能去顶栏点「项目图」，
           而"我要关掉这个东西"和"我要切到那个视图"在脑子里不是一回事 -->
      <button class="icon-btn ghost tiny" title="收起对话，回到画布（对话没丢，还在这条线上）"
              @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </div>

    <!-- 消息区自己滚，输入框钉在底下：聊到第 20 条还要往下翻才能打字是不能接受的 -->
    <div class="chat-wrap">
      <div ref="box" class="chat-box">
        <div v-if="empty" class="empty">
          <Icon name="network" :size="30" :width="1.3" />
          <span class="t">聊着学</span>
          <span class="s">问概念、让我讲、被我考；学完了说一声，我把知识点整理成变更卡，
            你看过 diff 点一下才写进图谱。</span>
        </div>

        <div v-if="messages.length && messages[0].resumed" class="resumed-hint dim">
          下面是之前聊过的（从 <code>.knowrary/chat/</code> 读回来的），直接接着问就行
        </div>

        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <!-- 过程默认收起来：查资料、工具报错、"我先看一下"——摊开给人看是对的，
               但和答案混在一行里，每次都得在里面淘那几句有营养的。点开随时能看全 -->
          <details v-if="(m.trace || []).length" class="trace">
            <summary>
              <Icon name="search" :size="12" />过程 · {{ (m.trace || []).length }} 步
              <span v-if="traceTools(m).length" class="dim">（{{ traceTools(m).join('、') }}）</span>
            </summary>
            <div v-for="(t, j) in m.trace" :key="`tr${j}`" class="trace-row">
              <template v-if="t.kind === 'tool'">
                <span class="tool-line"><Icon name="search" :size="12" />{{ t.label }}</span>
                <div v-if="t.summary" class="dim trace-sum">{{ t.summary }}</div>
              </template>
              <div v-else class="dim trace-say">{{ t.text }}</div>
            </div>
          </details>

          <!-- 模型那边渲染成图文（Markdown + mermaid 画的图）；我自己说的话按原样显示 -->
          <div v-if="m.content" class="bubble" :class="{ broken: m.failed }">
            <Markdown v-if="m.role === 'assistant'" :text="m.content" />
            <template v-else>{{ m.content }}</template>
            <span v-if="m.streaming" class="caret">▍</span>
          </div>
          <!-- 等得久了就把秒数抬出来（服务端的心跳事件带回来的）。
               模型想一分钟和连接已经死了，屏幕上本来长得一模一样 -->
          <div v-else-if="m.streaming" class="bubble dim">
            {{ m.waited >= 10 ? `还在等模型…（已等 ${m.waited} 秒）` : '想一下…' }}
          </div>

          <!-- 没答成：单独一块摆出来。上面那半截和一段正常回答长得一样，
               不标的话只会以为模型就答了这么点。它也不会再被当上下文发回模型 -->
          <div v-if="m.failed" class="bubble broken-note">
            <div><Icon name="warn" :size="13" />这一轮没答成{{ m.content ? '，上面是断掉前收到的半截' : '' }}</div>
            <div v-if="m.error" class="dim">{{ m.error }}</div>
            <button class="btn tiny" :disabled="busy" @click="emit('retry')">
              <Icon name="refresh" :size="13" />重试这一轮
            </button>
          </div>


          <!-- 讲完一段问的那个检验问题：已经攒进题库，以后按遗忘曲线抽查 -->
          <div v-for="(qq, j) in (m.questions || [])" :key="`q${j}`" class="tool-line"
               :title="`「${qq.stem}」已经进题库，考点：${(qq.points || []).join('、') || '（这轮提到的点）'}\n下次考这些点时优先用它，不用再花钱出题`">
            <Icon name="checklist" :size="12" />这道检验题已收进题库<span
              v-if="(qq.points || []).length" class="dim"> · {{ qq.points.join('、') }}</span>
          </div>

          <!-- 项目卡：建项目 / 加清单。同样只是提议，点了才写 projects.json -->
          <div v-for="(pj, j) in (m.projects || [])" :key="`p${j}`" class="change-card"
               :class="{ folded: folded(pj) }">
            <div class="cc-head" :class="{ foldable: pj.applied }" @click="unfold(pj)">
              <Icon name="checklist" :size="13" />
              {{ pj.action === 'create' ? '提议新建项目' : '提议加清单' }}「{{ pj.name }}」
              <span class="dim">id {{ pj.id }}</span>
              <!-- 档位一路决定出题深浅和拆点粒度，按下「创建」之前得看得见它被定成了哪一档 -->
              <span v-if="pj.level" class="chip m-due" title="学到什么份上：出题深浅、拆点粒度都看它">{{ pj.level }}</span>
              <span v-if="pj.applied" class="chip m-mastered">已创建</span>
              <Icon v-if="pj.applied" :name="pj.open ? 'chevronDown' : 'chevronRight'" :size="12" class="cc-caret" />
            </div>
            <ul v-if="!folded(pj)">
              <li v-for="(ls, k) in pj.lists" :key="k" class="edge-row point">
                <span class="chip m-unbuilt">{{ ls.kind }}</span>
                <span class="to">{{ ls.name }}</span>
                <span v-if="ls.goal" class="yr why">{{ ls.goal }}</span>
                <span v-if="ls.target_date" class="dim">→ {{ ls.target_date }}</span>
              </li>
            </ul>
            <div v-if="!pj.applied" class="cc-acts">
              <button class="btn primary tiny" :disabled="busy" @click="emit('apply-project', { card: pj, i, j })">
                <Icon name="check" :size="13" />创建
              </button>
              <span class="dim" style="font-size: 11px">只写 projects.json，不碰 md、不碰画布</span>
            </div>
          </div>

          <!-- 拆点卡：把清单拆成知识点。和面板上「让 AI 拆一份」是同一条链路 -->
          <div v-for="(pt, j) in (m.points || [])" :key="`pt${j}`" class="change-card"
               :class="{ folded: folded(pt) }">
            <div class="cc-head" :class="{ foldable: pt.applied }" @click="unfold(pt)">
              <Icon name="network" :size="13" />
              给「{{ pt.project_name }}·{{ pt.list_name }}」拆了
              {{ pt.stages.reduce((n, s) => n + s.points.length, 0) }} 个点
              <span v-if="pt.applied" class="chip m-mastered">已采纳</span>
              <Icon v-if="pt.applied" :name="pt.open ? 'chevronDown' : 'chevronRight'" :size="12" class="cc-caret" />
            </div>
            <template v-if="!folded(pt)">
            <div v-for="(st, k) in pt.stages" :key="k" class="prop-stage">
              <div class="prop-stage-name">{{ st.name }}
                <span v-if="st.deadline" class="dim" style="font-weight: 400">· 排到 {{ st.deadline }}</span>
              </div>
              <ul>
                <li v-for="q in st.points" :key="q.id" class="edge-row point">
                  <span class="chip" :class="pt.existing?.includes(q.id) ? 'm-learned' : 'm-unbuilt'">
                    {{ pt.existing?.includes(q.id) ? '图里有' : '要新建' }}
                  </span>
                  <span class="to">{{ q.name || q.id }}</span>
                  <span class="load">{{ q.load }}</span>
                  <span v-if="pt.in_projects?.[q.id]" class="chip m-due"
                        :title="'重叠是合法的：掌握度还是同一个'">{{ pt.in_projects[q.id].join('/') }} 里有</span>
                  <span v-if="q.why" class="yr why">{{ q.why }}</span>
                </li>
              </ul>
            </div>
            <p v-if="pt.schedule?.verdict" class="dim" style="font-size: 11.5px">
              时间账：{{ pt.schedule.verdict }}——这份约 {{ pt.schedule.total_hours }} 小时，
              按现在的投入要学到 {{ pt.schedule.suggested_target_date }}
            </p>
            </template>
            <div v-if="!pt.applied" class="cc-acts">
              <button class="btn primary tiny" :disabled="busy" @click="emit('apply-points', { card: pt, i, j })">
                <Icon name="check" :size="13" />采纳进清单
              </button>
              <span class="dim" style="font-size: 11px">只写 projects.json；标了「别的项目里有」的是重叠，不是错</span>
            </div>
          </div>

          <!-- 清单卡：改已有条目（换 id / 删 / 改字段）。只动 projects.json，不碰 md -->
          <div v-for="(le, j) in (m.listEdits || [])" :key="`le${j}`" class="change-card"
               :class="{ folded: folded(le) }">
            <div class="cc-head" :class="{ foldable: le.applied }" @click="unfold(le)">
              <Icon name="network" :size="13" />
              改「{{ le.project_name }}·{{ le.list_name }}」{{ le.edits.length }} 条
              <span v-if="le.applied" class="chip m-mastered">已应用</span>
              <Icon v-if="le.applied" :name="le.open ? 'chevronDown' : 'chevronRight'" :size="12" class="cc-caret" />
            </div>
            <template v-if="!folded(le)">
            <ul>
              <li v-for="(e, k) in le.edits" :key="k" class="edge-row point">
                <span class="chip" :class="e.op === 'drop' ? 'm-drop' : 'm-due'">
                  {{ e.op === 'drop' ? '删掉' : e.op === 'rename' ? '换 id' : '改字段' }}
                </span>
                <span class="to" :class="{ gone: e.op === 'drop' }">{{ e.name }}</span>
                <span v-if="e.op === 'rename'" class="yr">{{ e.id }} → <b>{{ e.to }}</b></span>
                <span v-else-if="e.op === 'set'" class="yr">
                  <template v-for="(v, f) in e.fields" :key="f">{{ f }}：{{ e.before[f] || '（空）' }} → <b>{{ v }}</b>&nbsp;</template>
                </span>
              </li>
            </ul>
            <p v-if="le.empties" class="dim" style="font-size: 11.5px; color: var(--danger)">
              ⚠️ 这些删完就是<b>空清单</b>了——项目进度和今日清单会跟着全空。
            </p>
            <p v-else-if="!le.applied" class="dim" style="font-size: 11.5px">改完这份清单还剩 {{ le.left }} 个点</p>
            </template>
            <div v-if="!le.applied" class="cc-acts">
              <button class="btn primary tiny" :disabled="busy"
                      @click="emit('apply-list-edit', { card: le, i, j })">
                <Icon name="check" :size="13" />应用
              </button>
              <span class="dim" style="font-size: 11px">只写 projects.json；不会把节点从图里删掉</span>
            </div>
          </div>

          <!-- 变更卡：**这里是唯一能写 md 的地方，且必须人点** -->
          <div v-for="(c, j) in (m.cards || [])" :key="`c${j}`" class="change-card"
               :class="{ folded: folded(c) }">
            <div class="cc-head" :class="{ foldable: c.applied }" @click="unfold(c)">
              <Icon name="file" :size="13" />提议写入 {{ c.files.length }} 个文件
              <span class="dim cc-paths">{{ c.files.map((f) => f.path.split('/').pop().replace(/\.md$/, '')).join('、') }}</span>
              <span v-if="c.auditing" class="chip m-due">审核中 {{ waited(c) }}s</span>
              <span v-else-if="c.audit" class="chip" :class="VERDICT_CHIP[c.audit.verdict] || 'm-due'"
                    :title="auditMeta(c.audit)">{{ auditTag(c.audit) }}</span>
              <span v-if="c.applied" class="chip m-mastered">已写入</span>
              <Icon v-if="c.applied" :name="c.open ? 'chevronDown' : 'chevronRight'" :size="12" class="cc-caret" />
              <button v-if="!c.applied && !c.expired && editable(c).length" class="btn subtle tiny" style="margin-left: auto"
                      :title="c.editing ? '收起编辑框' : '写入前先改摘要 / 正文'" @click="toggleEdit(c)">
                <Icon :name="c.editing ? 'x' : 'pencil'" :size="12" />{{ c.editing ? '收起' : '改一改' }}
              </button>
            </div>
            <!-- 写入前改：模型给的是初稿，desc（显示层）和 body（笔记层）都能在这儿定稿 -->
            <div v-if="c.editing && !c.applied" class="cc-edit">
              <div v-for="(ch, k) in editable(c)" :key="k" class="cc-edit-one">
                <div class="cc-edit-head"><b>{{ ch.source }}</b><span class="dim">{{ KIND_LABEL[ch.type] }}</span></div>
                <input v-if="ch.fields && 'desc' in ch.fields || ch.type === 'create_node'"
                       v-model="ch.fields.desc" type="text" placeholder="一句话摘要（显示层，画布上就这一句）"
                       @input="touch(c)">
                <textarea v-if="ch.type !== 'update_frontmatter'" v-model="ch.body" class="scroll-thin" rows="12"
                          placeholder="正文（笔记层，整篇落盘；## 关系 由系统管，别写）" @input="touch(c)"></textarea>
              </div>
              <div class="cc-acts">
                <button class="btn subtle tiny" :disabled="busy" @click="emit('preview', { card: c, i, j })">
                  <Icon name="refresh" :size="12" />重算 diff
                </button>
                <span v-if="c.stale" class="dim" style="font-size: 11px">改过了，下面的 diff 还是旧的；直接写入也按改后的算</span>
              </div>
            </div>
            <p v-if="c.expired && !c.applied" class="cc-expired">
              这张卡摆出来之后文件变了，照原样写不进去了：{{ c.expired }}。要的话让我重新出一张。
            </p>
            <template v-if="!folded(c)">
            <div v-for="f in c.files" :key="f.path" class="cc-file">
              <div class="cc-file-head">
                <b>{{ f.path }}</b>
                <span v-if="parseDiff(f.diff).isNew" class="chip cc-new">新文件 · 全篇初稿</span>
              </div>
              <pre class="cc-diff"><span v-for="(l, k) in parseDiff(f.diff).lines" :key="k"
                :class="l.cls">{{ l.text }}
</span></pre>
            </div>
            <p v-if="c.into" class="dim" style="font-size: 11px; margin: 0 0 6px">
              写入后同时把 <b>{{ c.into.points.join('、') }}</b> 加进
              「{{ c.into.project_name }}·{{ c.into.list_name }}」清单——
              不加的话节点建出来了、项目进度却不认它
            </p>
            <div v-if="c.auditing" class="cc-audit busy">
              <Icon name="checklist" :size="13" />review 角色在审这张卡 · 已等 {{ waited(c) }} 秒
              <span class="dim">（看事实、关系方向、和库里有没有冲突）</span>
            </div>
            <div v-else-if="c.audit" class="cc-audit" :class="c.audit.verdict">
              <div class="cc-audit-head">
                <Icon name="checklist" :size="13" /><b>{{ auditTag(c.audit) }}</b>
                <span class="dim">{{ auditMeta(c.audit) }}</span>
              </div>
              <p v-if="c.audit.summary" class="cc-audit-sum">{{ c.audit.summary }}</p>
              <ul v-if="c.audit.issues?.length" class="cc-audit-list">
                <li v-for="(it, k) in c.audit.issues" :key="k" :class="[it.level, { off: pickable(c) && c.skip?.[k] }]">
                  <input v-if="pickable(c)" type="checkbox" class="cc-audit-pick" :checked="!c.skip?.[k]"
                         :disabled="working(c)" title="勾上的意见交给「按意见修改」" @change="togglePick(c, k)">
                  <span class="chip" :class="it.level === 'block' ? 'm-drop' : 'm-due'">{{ it.level === 'block' ? '得改' : '提醒' }}</span>
                  <span>{{ it.message }}</span>
                  <span v-if="it.path" class="dim cc-audit-path">{{ it.path }}</span>
                  <p v-if="it.why" class="dim">依据：{{ it.why }}</p>
                  <p v-if="it.fix" class="cc-audit-fix">改法：{{ it.fix }}</p>
                </li>
              </ul>
              <p v-if="!c.applied && !auditFresh(c)" class="dim" style="font-size: 11px; margin: 4px 0 0">
                卡改过了，这是改之前的审核意见</p>
            </div>
            <div v-if="c.revising" class="cc-audit busy">
              <Icon name="pencil" :size="13" />learn 角色在按 {{ c.revising.n }} 条意见改 · 已等 {{ waited(c) }} 秒
              <span class="dim">（只改卡，不写盘；改完先给你看）</span>
            </div>
            <div v-else-if="c.revised" class="cc-audit cc-revised">
              <div class="cc-audit-head">
                <Icon name="pencil" :size="13" /><b>AI 按意见改过</b>
                <span class="dim">{{ revisedMeta(c.revised) }}</span>
                <button v-if="!c.applied" class="btn subtle tiny" style="margin-left: auto" :disabled="busy || working(c)"
                        title="不要这一版：卡回到改之前的样子" @click="emit('undo-revise', { card: c, i, j })">
                  <Icon name="x" :size="12" />撤回修改
                </button>
              </div>
              <p v-if="c.revised.summary" class="cc-audit-sum">{{ c.revised.summary }}</p>
              <ul v-if="c.revised.skipped?.length" class="cc-audit-list">
                <li v-for="(s, k) in c.revised.skipped" :key="k" class="warn">
                  <span class="chip m-due">没照改</span><span>{{ s.what }}</span>
                  <p v-if="s.why" class="dim">{{ s.why }}</p>
                </li>
              </ul>
              <details v-if="c.revised.delta" :open="c.revised.open ?? !c.applied" class="cc-revised-delta">
                <summary class="dim">改了哪儿（改前 → 改后）</summary>
                <pre class="cc-diff cc-delta"><span v-for="(l, k) in pairDiff(c.revised.delta)" :key="k"
                  :class="l.cls"><template v-for="(p, q) in l.parts" :key="q"><mark v-if="p.hot">{{ p.text }}</mark><template v-else>{{ p.text }}</template></template>
</span></pre>
              </details>
              <p v-else class="dim" style="font-size: 11px; margin: 4px 0 0">模型交回的和原来一模一样，什么都没改</p>
            </div>
            </template>
            <div v-if="!c.applied && !c.expired" class="cc-acts">
              <button v-if="auditOn && !c.audit" class="btn subtle tiny" :disabled="busy || working(c)"
                      title="只审不写：结论挂在卡上，看完再决定写不写" @click="emit('audit', { card: c, i, j })">
                <Icon name="checklist" :size="12" />审核
              </button>
              <button v-if="auditOn && c.audit && !auditFresh(c)" class="btn subtle tiny" :disabled="busy || working(c)"
                      title="卡改过了，上面那份结论已经作废：只审不写，再审一遍" @click="emit('audit', { card: c, i, j })">
                <Icon name="checklist" :size="12" />重新审核
              </button>
              <button v-if="pickable(c)" class="btn tiny" :class="reviseFirst(c) ? 'primary' : 'subtle'"
                      :disabled="busy || working(c) || !picked(c).length"
                      title="把勾上的意见交给 learn 角色改一版：只改卡，不写盘，改完先给你看"
                      @click="revise(c, i, j)">
                <Icon name="pencil" :size="12" />按意见修改（{{ picked(c).length }} 条）
              </button>
              <button v-if="!blockedNow(c)" class="btn tiny" :class="reviseFirst(c) ? 'subtle' : 'primary'"
                      :disabled="busy || working(c)" @click="emit('apply', { card: c, i, j })">
                <Icon name="check" :size="13" />{{ writeLabel(c) }}
              </button>
              <button v-if="blockedNow(c) && forceAllowed" class="btn subtle tiny danger" :disabled="busy || working(c)"
                      title="你确认审核看走眼了：跳过模型直接写（会记一笔）"
                      @click="emit('apply', { card: c, i, j, force: true })">忽略意见，强制写入</button>
              <span class="dim" style="font-size: 11px">{{ writeHint(c) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="empty" class="starters">
        <button v-for="s in STARTERS" :key="s.t" class="btn subtle tiny" @click="starter(s)">{{ s.t }}</button>
      </div>

      <div v-if="focus" class="focus-chip">
        <Icon name="target" :size="12" />带上「{{ focus.name || focus.id }}」的摘要和关系
        <button class="icon-btn ghost tiny" title="不带了" @click="emit('drop-focus')">
          <Icon name="x" :size="12" />
        </button>
      </div>

      <div class="chat-input">
        <textarea v-model="text" rows="2" placeholder="问点什么…（回车发送，Shift+回车换行）"
                  @keydown="onKey" />
        <button v-if="busy" class="icon-btn ghost" title="停" @click="emit('stop')">
          <Icon name="pause" :size="15" />
        </button>
        <button v-else class="icon-btn primary" :disabled="!text.trim()" title="发送（回车）" @click="send()">
          <Icon name="arrowRight" :size="15" />
        </button>
      </div>
    </div>
  </section>
</template>
