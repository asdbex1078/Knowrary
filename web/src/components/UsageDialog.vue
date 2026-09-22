<script setup>
/**
 * 模型调用明细：今天花了多少、都花在哪个功能上、最近几十次分别是什么。
 *
 * 只显示 provider **自己报回来的**花销。不按型号估价——价目表会过期，
 * 估出来的数字比没有更糟；provider 不报价时就只摆 token 数，不假装知道钱。
 */
import { computed, onBeforeUnmount, onMounted } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({ usage: { type: Object, required: true } })
const emit = defineEmits(['close', 'refresh'])

const OP_NAME = {
  suggest: '关系建议', quiz: '出题', 'quiz-diagnose': '答题比对',
  'plan-propose': '拆学习清单', 'plan-propose-fast': '速学版拆解',
  'plan-interview': '拆面试考点', 'plan-interview-fast': '速学版面试拆解',
  'plan-map': '铺领域地图', 'plan-map-fast': '速学版领域地图',
  chat: '对话教练', '?': '其它',
}
const money = (n) => (n >= 0.01 ? `$${n.toFixed(2)}` : n > 0 ? '<$0.01' : '$0.00')
const kilo = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`)
const secs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`)

const pct = (n) => (n == null ? '—' : `${Math.round(n * 100)}%`)

const ops = computed(() =>
  Object.entries(props.usage.by_op || {}).sort((a, b) => b[1].calls - a[1].calls))
const tok = (b) => b.input_tokens + b.output_tokens
const cached = computed(() => props.usage.totals.cache_read_tokens + props.usage.totals.cache_write_tokens)

/**
 * 缓存那一格看哪个指标，**由 provider 报得出什么决定，不是由模型名决定**。
 * 契约里 `cache.kind` 存的就是字段名（`ratio` / `hit_rate` / `none`），所以这里直接拿它取值，
 * 前端不再维护第二张 kind → 字段 的对照表（两张表迟早对不上）。
 *
 * - `ratio`：claude-cli / anthropic 报缓存写入，读 ÷ 写；
 * - `hit_rate`：OpenAI 兼容（百炼 / DeepSeek）只报命中数，命中 ÷ 输入。
 *   对这一路算读写比永远是除以 0，页面上只会是一个不会变红也不会变绿的 `—`。
 */
const byHit = computed(() => props.usage.cache?.kind === 'hit_rate')
const metric = computed(() => {
  const c = props.usage.cache
  if (!c || c.kind === 'none') return '—'
  return byHit.value ? pct(c.hit_rate) : `${c.ratio}×`
})
const worstVal = computed(() => {
  const w = props.usage.cache?.worst
  return w ? (byHit.value ? pct(w.hit_rate) : `${w.ratio}×`) : ''
})
const totalMetric = computed(() =>
  (byHit.value ? pct(props.usage.totals.cache_hit_rate) : `${props.usage.totals.cache_ratio}×`))
const hasTotal = computed(() =>
  (byHit.value ? props.usage.totals.cache_hit_rate != null : !!props.usage.totals.cache_ratio))

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog usage-dialog">
    <header>
      <Icon name="network" :size="15" />
      <span class="who">模型调用</span>
      <span class="dim">{{ usage.provider || '未配置' }}</span>
      <button class="icon-btn ghost tiny" title="刷新" @click="emit('refresh')">
        <Icon name="refresh" :size="14" />
      </button>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <div class="quiz-label">今天（{{ usage.date }}）</div>
      <div class="usage-big">
        <span><b>{{ usage.today.calls }}</b> 次</span>
        <span v-if="usage.cost_known"><b>{{ money(usage.today.cost_usd) }}</b></span>
        <span class="dim">{{ kilo(tok(usage.today)) }} tok<template v-if="usage.today.errors">
          · <span class="warn-text">{{ usage.today.errors }} 次失败</span></template></span>
      </div>

      <dl class="usage-grid">
        <dt>累计</dt>
        <dd>{{ usage.totals.calls }} 次 · {{ kilo(tok(usage.totals)) }} tok<template
          v-if="usage.cost_known"> · {{ money(usage.totals.cost_usd) }}</template></dd>
        <dt>缓存 token</dt>
        <dd>{{ kilo(cached) }}
          <span class="dim">（读 {{ kilo(usage.totals.cache_read_tokens) }} / 写
            {{ kilo(usage.totals.cache_write_tokens) }}）</span></dd>
        <!-- 这一格看的是**今天**的多轮对话，不是总账：累计只加不减，糟过一天就再也绿不回来。
             指标名跟着 provider 口径换（见 byHit）：不报缓存写入的那一路只能看命中率 -->
        <dt>{{ byHit ? '命中率' : '读写比' }}<span class="dim">（今天）</span></dt>
        <dd :class="{ 'warn-text': usage.cache && !usage.cache.ok }">
          {{ metric }}
          <span v-if="byHit" class="dim">
            （{{ kilo(usage.today.cache_read_tokens) }} / {{ kilo(usage.today.input_tokens) }} tok）</span>
          <span v-if="usage.cache?.worst" class="dim">
            · 最低 {{ OP_NAME[usage.cache.worst.op] || usage.cache.worst.op }} {{ worstVal }}</span>
          <span v-else-if="usage.cache" class="dim">
            · 今天 {{ usage.cache.calls }} 次多轮调用，还不够判</span>
          <span v-if="hasTotal" class="dim">　累计 {{ totalMetric }}</span></dd>
        <!-- 花了多少 vs 换来了什么。**这两个数来自两份互不相干的流水**（账本 + 卡片流水），
             在接口里才第一次拼到一起；这里不存任何摊派过的金额 -->
        <dt>卡片产出<span class="dim">（今天）</span></dt>
        <dd v-if="usage.cards?.proposed">
          摆出 <b>{{ usage.cards.proposed }}</b> 张 · 点了 <b>{{ usage.cards.applied }}</b> 张
          <span v-if="usage.cost_known && usage.cards.applied" class="dim">
            · 每张落地的卡 ≈ {{ money(usage.today.cost_usd / usage.cards.applied) }}</span>
          <span v-if="usage.cards.total_proposed" class="dim">　累计采纳
            {{ Math.round(usage.cards.total_applied / usage.cards.total_proposed * 100) }}%
            （{{ usage.cards.total_applied }}/{{ usage.cards.total_proposed }}）</span></dd>
        <dd v-else class="dim">今天还没摆过卡</dd>
        <dt>角色 → provider</dt>
        <dd>{{ Object.entries(usage.roles).map(([r, p]) => `${r} → ${p}`).join('，') || '默认 claude-cli' }}</dd>
      </dl>

      <p v-if="usage.cache && !usage.cache.ok && !byHit" class="warn-text"
         style="font-size: 11.5px; line-height: 1.6">
        今天多轮对话的读写比低于 {{ usage.cache.healthy }}×，说明**每一轮都在重写缓存而不是读它**。
        缓存失效不报错、答案也全对，只有账单在涨——健康的多轮循环应该在 5-10×。
        先查前缀里是不是混进了会变的东西（时间、节点数、未排序的 JSON）；
        要是聊天本来就隔了十几分钟一句，那只是 5 分钟缓存到期，不是前缀出了问题。
      </p>
      <p v-else-if="usage.cache && !usage.cache.ok" class="warn-text"
         style="font-size: 11.5px; line-height: 1.6">
        今天多轮对话的命中率低于 {{ Math.round(usage.cache.healthy_hit * 100) }}%，
        意思是这么多输入 token 是**按原价重买的**，没吃到缓存。先查前缀里是不是混进了会变的东西
        （时间、节点数、未排序的 JSON）——前缀只增不改时该有 80% 以上。
        排除掉它之后再看这三条：换 provider 的第一次调用永远全价；上一次请求刚结束就紧接着发，
        对面的隐式缓存还没写进去；隔几分钟再发有时只命中系统提示那一截（这一路是 best-effort，
        不保证命中，不是你这边前缀变了）。
      </p>
      <p v-if="!usage.cost_known" class="dim" style="font-size: 11.5px; line-height: 1.6">
        当前 provider 不返回花销，所以这里只有 token 数。**不按型号估价**——价目表会过期，
        估出来的数字比没有更糟。
      </p>
      <p v-else-if="cached > tok(usage.totals) * 3" class="dim" style="font-size: 11.5px; line-height: 1.6">
        花销大头是缓存 token（<code>claude -p</code> 每次都要重建一次系统提示的缓存），
        不是你这几个 prompt 本身。想压成本就换 API 类型的 provider。
      </p>

      <div class="quiz-label" style="margin-top: 14px">按功能</div>
      <div v-for="[op, b] in ops" :key="op" class="usage-row">
        <span class="op">{{ OP_NAME[op] || op }}</span>
        <span>{{ b.calls }} 次 · {{ kilo(tok(b)) }} tok</span>
        <span class="tail">
          <template v-if="usage.cost_known">{{ money(b.cost_usd) }} · </template>
          均 {{ secs(Math.round(b.ms / Math.max(b.calls, 1))) }}
        </span>
      </div>
      <p v-if="!ops.length" class="dim" style="font-size: 12px">还没调用过模型。</p>

      <div class="quiz-label" style="margin-top: 14px">最近</div>
      <div v-for="(r, i) in usage.recent" :key="i" class="usage-row" :class="{ bad: !r.ok }">
        <span class="op">{{ OP_NAME[r.op] || r.op }}</span>
        <span class="dim">{{ r.ts.slice(5, 16).replace('T', ' ') }}</span>
        <span v-if="!r.ok" class="warn-text" :title="r.error">失败</span>
        <span v-else class="dim">{{ kilo((r.input_tokens || 0) + (r.output_tokens || 0)) }} tok</span>
        <!-- 换 provider 的那天，这一列是唯一能看出"哪条记录是谁打的"的东西 -->
        <span class="mdl dim" :title="`${r.provider || '?'} · ${r.model || '?'}`">{{ r.model || r.provider }}</span>
        <span class="tail">
          <template v-if="r.cost_usd">{{ money(r.cost_usd) }} · </template>{{ secs(r.ms || 0) }}
        </span>
      </div>
      <p v-if="!usage.recent.length" class="dim" style="font-size: 12px">暂无记录。</p>
    </div>
  </div>
</template>
