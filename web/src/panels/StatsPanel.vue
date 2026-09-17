<script setup>
/**
 * 参数量图表：把 `params` 这个字段画出来，一眼比出量级。
 *
 * 两张图回答两个不同的问题：
 *   「这一系列是怎么涨上去的」—— 折线，X 是年份，Y 是参数量（对数轴）；
 *   「这几家谁更大」        —— 横向柱状，一根一个点，按参数量排。
 *
 * **Y 轴必须是对数**：340M 和 1.3 万亿差了近四个数量级，线性轴上前者是一根看不见的线。
 *
 * 为什么手画 SVG 不引图表库：这里最多几十个点，而 mermaid 已经把入库的 dist 撑到 6MB，
 * 再引一个图表库要为同样几十个点再付一次。
 */
import { computed, ref } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  index: { type: Object, default: null },
  // 演化族的链：同一条链就是一个系列，不另设 series 字段
  chains: { type: Array, default: () => [] },
})
const emit = defineEmits(['goto', 'close'])

const BY = { field: '领域', layer: '抽象层', chain: '演化链' }
const groupBy = ref('chain')

const W = 300           // 画布宽（面板里，跟着拉宽会重算）
const H = 190

const withParams = computed(() => (props.index?.nodes || [])
  .filter((n) => typeof n.params_n === 'number' && n.params_n > 0)
  .map((n) => ({ id: n.id, name: n.name || n.id, params: n.params_n,
                 year: typeof n.year === 'number' ? n.year : null,
                 field: n.field || '(未指定)', layer: n.layer || '未分层' })))

const total = computed(() => (props.index?.nodes || []).length)

/** 一个点属于哪个系列。演化链按"它在哪条链上"，同时在多条链上就取最长的那条。 */
const chainOf = (id) => {
  let best = null
  for (const c of props.chains) {
    if (!c.ids.includes(id)) continue
    if (!best || c.ids.length > best.ids.length) best = c
  }
  return best?.name || '（不在任何演化链上）'
}

const groups = computed(() => {
  const out = new Map()
  for (const n of withParams.value) {
    const key = groupBy.value === 'chain' ? chainOf(n.id) : n[groupBy.value]
    if (!out.has(key)) out.set(key, [])
    out.get(key).push(n)
  }
  return [...out.entries()]
    .map(([name, items]) => ({ name, items: items.sort((a, b) => (a.year ?? 0) - (b.year ?? 0)) }))
    .sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name, 'zh'))
})

// —— 折线图：X 年份、Y 参数量（log10） ——

const timed = computed(() => withParams.value.filter((n) => n.year !== null))

const scale = computed(() => {
  const ns = timed.value
  if (!ns.length) return null
  const years = ns.map((n) => n.year)
  const lo = Math.min(...years)
  const hi = Math.max(...years)
  const p = ns.map((n) => Math.log10(n.params))
  const pLo = Math.floor(Math.min(...p))
  const pHi = Math.ceil(Math.max(...p))
  const x = (year) => 34 + (hi === lo ? (W - 48) / 2 : ((year - lo) / (hi - lo)) * (W - 48))
  const y = (v) => H - 26 - ((Math.log10(v) - pLo) / Math.max(1, pHi - pLo)) * (H - 44)
  return { x, y, lo, hi, pLo, pHi }
})

const lines = computed(() => {
  if (!scale.value) return []
  const s = scale.value
  return groups.value.map((g) => ({
    name: g.name,
    dots: g.items.filter((n) => n.year !== null).map((n) => ({ ...n, cx: s.x(n.year), cy: s.y(n.params) })),
  })).filter((g) => g.dots.length)
})

const path = (dots) => dots.map((d, i) => `${i ? 'L' : 'M'} ${d.cx.toFixed(1)} ${d.cy.toFixed(1)}`).join(' ')

/** 10^n 的刻度。写成 1B / 175B 这种人读得懂的写法。 */
const ticks = computed(() => {
  if (!scale.value) return []
  const { pLo, pHi, y } = scale.value
  const out = []
  for (let p = pLo; p <= pHi; p++) out.push({ label: human(10 ** p), y: y(10 ** p) })
  return out
})

function human(v) {
  if (v >= 1e12) return `${round(v / 1e12)}T`
  if (v >= 1e9) return `${round(v / 1e9)}B`
  if (v >= 1e6) return `${round(v / 1e6)}M`
  if (v >= 1e3) return `${round(v / 1e3)}K`
  return String(Math.round(v))
}
const round = (v) => (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10)

// —— 柱状图：一根一个点，按参数量排 ——

const bars = computed(() => {
  const all = [...withParams.value].sort((a, b) => b.params - a.params)
  const max = Math.max(...all.map((n) => n.params), 1)
  return all.map((n) => ({ ...n,
    pct: Math.max(2, (Math.log10(n.params) / Math.log10(max)) * 100),
    label: human(n.params) }))
})
</script>

<template>
  <Drawer side="left" title="参数量" icon="chart" storage-key="stats" :default-width="330"
          @close="emit('close')">
    <template #head-actions>
      <span class="head-count">{{ withParams.length }}</span>
    </template>

    <template #default>
      <p v-if="!withParams.length" class="dim" style="line-height: 1.7">
        还没有任何知识点填了 <code>params</code>。在 md 的 frontmatter 里写
        <code>params: 175B</code>（也认 <code>340M</code> / <code>1.3万亿</code>），
        它就会出现在这两张图里。
      </p>

      <template v-else>
        <p class="dim" style="font-size: 11px; margin: 0 0 10px; line-height: 1.6">
          {{ total }} 个知识点里 <b>{{ withParams.length }}</b> 个填了参数量；
          其中 {{ timed.length }} 个还有年份，能进上面那张增长图。
        </p>

        <div class="section">
          <div class="section-head">
            <Icon name="clock" :size="13" />怎么涨上来的
            <span class="tail dim">Y 轴是对数</span>
          </div>
          <svg v-if="scale" class="chart" :viewBox="`0 0 ${W} ${H}`" role="img">
            <g class="axis">
              <line v-for="t in ticks" :key="t.label" x1="30" :y1="t.y" :x2="W - 8" :y2="t.y" />
              <text v-for="t in ticks" :key="`l${t.label}`" x="26" :y="t.y + 3"
                    text-anchor="end">{{ t.label }}</text>
              <text x="34" :y="H - 8">{{ scale.lo }}</text>
              <!-- 只有一个年份时别把同一个数字在两头各写一遍 -->
              <text v-if="scale.hi !== scale.lo" :x="W - 14" :y="H - 8"
                    text-anchor="end">{{ scale.hi }}</text>
            </g>
            <g v-for="(g, i) in lines" :key="g.name" :class="`s${i % 6}`">
              <path v-if="g.dots.length > 1" class="ln" :d="path(g.dots)" />
              <g v-for="d in g.dots" :key="d.id">
                <circle class="pt" :cx="d.cx" :cy="d.cy" r="4" @click="emit('goto', d.id)">
                  <title>{{ d.name }}（{{ d.year }}）· {{ human(d.params) }}</title>
                </circle>
              </g>
            </g>
          </svg>
          <p v-else class="dim" style="font-size: 11px">填了参数量的点都还没有 <code>year</code>，画不出增长曲线。</p>
        </div>

        <div class="section">
          <div class="section-head">
            <Icon name="layers" :size="13" />谁更大
            <span class="tail">
              <select v-model="groupBy" class="sess-pick" style="max-width: 96px">
                <option v-for="(label, k) in BY" :key="k" :value="k">按{{ label }}</option>
              </select>
            </span>
          </div>
          <ul class="bars">
            <li v-for="b in bars" :key="b.id" class="bar-row" @click="emit('goto', b.id)">
              <span class="nm" :title="`${b.name}${b.year ? `（${b.year}）` : ''}`">{{ b.name }}</span>
              <span class="track"><i :style="{ width: `${b.pct}%` }" /></span>
              <span class="val tnum">{{ b.label }}</span>
            </li>
          </ul>
        </div>

        <p class="dim" style="font-size: 11px; line-height: 1.7; margin-top: 12px">
          柱长按对数画：线性的话 340M 在 1.3T 旁边就是一条看不见的线。点一行跳到那个知识点。
        </p>
      </template>
    </template>
  </Drawer>
</template>
