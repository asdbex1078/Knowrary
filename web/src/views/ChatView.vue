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
import { computed, nextTick, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import Markdown from '../ui/Markdown.vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  messages: { type: Array, default: () => [] },   // [{ role, content, trace?, cards?, streaming? }]
  stance: { type: String, default: '教练' },
  sessions: { type: Array, default: () => [] },   // 从留档聚合出来的会话列表
  session: { type: String, default: '' },
  focus: { type: Object, default: null },         // 从图上点过来的节点：带进下一轮上下文
  graphOpen: { type: Boolean, default: true },
  tidied: { type: Object, default: null },        // 梳理游标：{ upto, turns, at }，没梳理过是 null
  fresh: { type: Number, default: 0 },            // 游标之后还有几条没梳理
})
const emit = defineEmits(['send', 'stop', 'apply', 'apply-project', 'apply-points', 'goto',
                          'new-session', 'pick-session', 'drop-focus', 'toggle-graph', 'stance',
                          'rename-session', 'close'])


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
   怎么运作、容易和什么搞混、我当时是怎么想通的。**我没懂的地方写「没懂：…」留着**，别替我编圆。
   带上 layer 和 year（有确切年份才填）；
4. 概念之间这次聊到的关系，用 add_edge 连上。

一次 propose_changes 出一张卡就行，别拆成好几条消息。没什么值得入库的就直说。`

const text = ref('')
const box = ref(null)

const empty = computed(() => !props.messages.length)

/** 新内容进来就贴着底部。人正往回翻时不要抢滚动。 */
watch(() => props.messages.map((m) => m.content).join('|'), async () => {
  await nextTick()
  const el = box.value
  if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 260) el.scrollTop = el.scrollHeight
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
      <select v-else class="sess-pick" :value="session" title="切到另一段对话"
              @change="emit('pick-session', $event.target.value)">
        <option v-if="!sessions.some((s) => s.id === session)" :value="session">{{ sessionLabel }}</option>
        <option v-for="s in sessions" :key="s.id" :value="s.id">
          {{ s.title }} · {{ s.turns }} 条
        </option>
      </select>
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
          <div v-if="m.content" class="bubble">
            <Markdown v-if="m.role === 'assistant'" :text="m.content" />
            <template v-else>{{ m.content }}</template>
            <span v-if="m.streaming" class="caret">▍</span>
          </div>
          <div v-else-if="m.streaming" class="bubble dim">想一下…</div>


          <!-- 讲完一段问的那个检验问题：已经攒进题库，以后按遗忘曲线抽查 -->
          <div v-for="(qq, j) in (m.questions || [])" :key="`q${j}`" class="tool-line"
               :title="`「${qq.stem}」已经进题库，考点：${(qq.points || []).join('、') || '（这轮提到的点）'}\n下次考这些点时优先用它，不用再花钱出题`">
            <Icon name="checklist" :size="12" />这道检验题已收进题库<span
              v-if="(qq.points || []).length" class="dim"> · {{ qq.points.join('、') }}</span>
          </div>

          <!-- 项目卡：建项目 / 加清单。同样只是提议，点了才写 projects.json -->
          <div v-for="(pj, j) in (m.projects || [])" :key="`p${j}`" class="change-card">
            <div class="cc-head">
              <Icon name="checklist" :size="13" />
              {{ pj.action === 'create' ? '提议新建项目' : '提议加清单' }}「{{ pj.name }}」
              <span class="dim">id {{ pj.id }}</span>
              <!-- 档位一路决定出题深浅和拆点粒度，按下「创建」之前得看得见它被定成了哪一档 -->
              <span v-if="pj.level" class="chip m-due" title="学到什么份上：出题深浅、拆点粒度都看它">{{ pj.level }}</span>
              <span v-if="pj.applied" class="chip m-mastered">已创建</span>
            </div>
            <ul>
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
          <div v-for="(pt, j) in (m.points || [])" :key="`pt${j}`" class="change-card">
            <div class="cc-head">
              <Icon name="network" :size="13" />
              给「{{ pt.project_name }}·{{ pt.list_name }}」拆了
              {{ pt.stages.reduce((n, s) => n + s.points.length, 0) }} 个点
              <span v-if="pt.applied" class="chip m-mastered">已采纳</span>
            </div>
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
            <div v-if="!pt.applied" class="cc-acts">
              <button class="btn primary tiny" :disabled="busy" @click="emit('apply-points', { card: pt, i, j })">
                <Icon name="check" :size="13" />采纳进清单
              </button>
              <span class="dim" style="font-size: 11px">只写 projects.json；标了「别的项目里有」的是重叠，不是错</span>
            </div>
          </div>

          <!-- 变更卡：**这里是唯一能写 md 的地方，且必须人点** -->
          <div v-for="(c, j) in (m.cards || [])" :key="`c${j}`" class="change-card">
            <div class="cc-head">
              <Icon name="file" :size="13" />提议写入 {{ c.files.length }} 个文件
              <span v-if="c.applied" class="chip m-mastered">已写入</span>
            </div>
            <pre v-for="f in c.files" :key="f.path" class="cc-diff"><b>{{ f.path }}</b>
{{ f.diff || '（新文件）' }}</pre>
            <p v-if="c.into" class="dim" style="font-size: 11px; margin: 0 0 6px">
              写入后同时把 <b>{{ c.into.points.join('、') }}</b> 加进
              「{{ c.into.project_name }}·{{ c.into.list_name }}」清单——
              不加的话节点建出来了、项目进度却不认它
            </p>
            <div v-if="!c.applied" class="cc-acts">
              <button class="btn primary tiny" :disabled="busy" @click="emit('apply', { card: c, i, j })">
                <Icon name="check" :size="13" />写入
              </button>
              <span class="dim" style="font-size: 11px">写前自动备份到 .knowrary/backup/</span>
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
