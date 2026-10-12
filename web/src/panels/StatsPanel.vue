<script setup>
/**
 * 参数量图表：把模型目录里的型号画出来，一眼比出量级（2026-09-30 起读模型目录，不再读知识点的 `params`——
 * 型号不是知识节点，参数只挂在知识点上时整个库只有一个 DeepSeek-V3 有数）。
 *
 * 两张图回答两个不同的问题：
 *   「这一系列是怎么涨上去的」—— 折线，一个家族一条，X 是发布时间，Y 是选中的那一项（对数轴）；
 *   「谁更大」              —— 横向柱状，一根一个型号，按选中的那一项排。
 * 比哪一项能换：2026-10-12 起不写死，列的是每张谱系图 `谱系.md` 里声明成「数值」的字段
 * （大模型的参数 / 激活 / 上下文 / 训练数据，CPU 的制程 / 晶体管…），一次比一张图里的一项。
 *
 * **Y 轴必须是对数**：117M 和 2.8T 差了四个数量级，线性轴上前者是一根看不见的线。
 * 为什么手画 SVG 不引图表库：这里最多几百个点，而 mermaid 已经把入库的 dist 撑到 6MB。
 */
import { computed, ref } from 'vue'
import { humanNumber } from '../canvas/modelLanes.js'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  models: { type: Object, default: null },        // /api/models
})
const emit = defineEmits(['show-model', 'open-lineage', 'close'])

const BY = { family: '家族', open: '开源 / 闭源' }
/** 能比的项：每张图里声明成数值的字段。`id` = `图|字段`。 */
const metrics = computed(() => (props.models?.lineages || []).flatMap((l) => l.fields.filter((f) => f.numeric)
  .map((f) => ({ id: `${l.id}|${f.key}`, lineage: l.id, key: f.key, unit: f.unit,
                 label: (props.models.lineages.length > 1 ? `${l.name} · ` : '') + f.key }))))
const picked = ref('')
const metric = computed(() => metrics.value.find((m) => m.id === picked.value) || metrics.value[0] || null)
const fmt = (n) => humanNumber(n, metric.value?.unit) || String(n)
const groupBy = ref('family')

const W = 300           // 画布宽（面板里，跟着拉宽会重算）
const H = 190

const all = computed(() => (props.models?.families || []).filter((f) => f.lineage === metric.value?.lineage)
  .flatMap((f) => f.models.map((m) => ({
    id: m.id, name: m.name, family: f.name, open: f.open, t: m.t, year: m.year, value: m.numbers?.[metric.value.key] }))))
const withValue = computed(() => all.value.filter((m) => typeof m.value === 'number' && m.value > 0))
const timed = computed(() => withValue.value.filter((m) => m.t != null))

const groups = computed(() => {
  const out = new Map()
  for (const m of timed.value) {
    if (!out.has(m.family)) out.set(m.family, [])
    out.get(m.family).push(m)
  }
  return [...out.entries()].map(([name, items]) => ({ name, items: items.sort((a, b) => a.t - b.t) }))
})

// —— 折线图：X 发布时间、Y 选中的那一项（log10） ——

const scale = computed(() => {
  const ns = timed.value
  if (!ns.length) return null
  const lo = Math.floor(Math.min(...ns.map((n) => n.t)))
  const hi = Math.ceil(Math.max(...ns.map((n) => n.t)))
  const p = ns.map((n) => Math.log10(n.value))
  const pLo = Math.floor(Math.min(...p))
  const pHi = Math.ceil(Math.max(...p))
  const x = (t) => 34 + (hi === lo ? (W - 48) / 2 : ((t - lo) / (hi - lo)) * (W - 48))
  const y = (v) => H - 26 - ((Math.log10(v) - pLo) / Math.max(1, pHi - pLo)) * (H - 44)
  return { x, y, lo, hi, pLo, pHi }
})

const lines = computed(() => {
  const s = scale.value
  if (!s) return []
  return groups.value.map((g) => ({ name: g.name, dots: g.items.map((n) => ({ ...n, cx: s.x(n.t), cy: s.y(n.value) })) }))
})

const path = (dots) => dots.map((d, i) => `${i ? 'L' : 'M'} ${d.cx.toFixed(1)} ${d.cy.toFixed(1)}`).join(' ')

/** 10^n 的刻度，写成 1B / 100K 这种人读得懂的写法。 */
const ticks = computed(() => {
  if (!scale.value) return []
  const { pLo, pHi, y } = scale.value
  const out = []
  for (let p = pLo; p <= pHi; p++) out.push({ label: fmt(10 ** p), y: y(10 ** p) })
  return out
})

// —— 柱状图：一根一个型号，按选中的那一项排，按家族 / 开源闭源分段 ——

const bars = computed(() => {
  const max = Math.max(...withValue.value.map((n) => n.value), 1)
  const key = (m) => (groupBy.value === 'family' ? m.family : m.open === true ? '开源' : m.open === false ? '闭源' : '没写')
  const out = new Map()
  for (const m of [...withValue.value].sort((a, b) => b.value - a.value)) {
    if (!out.has(key(m))) out.set(key(m), [])
    out.get(key(m)).push({ ...m, pct: Math.max(2, (Math.log10(m.value) / Math.log10(max)) * 100),
                           label: fmt(m.value) })
  }
  return [...out.entries()].map(([name, items]) => ({ name, items }))
})
</script>

<template>
  <Drawer side="left" title="数值对比" icon="chart" storage-key="stats" :default-width="330"
          @close="emit('close')">
    <template #head-actions>
      <span class="head-count">{{ withValue.length }}</span>
    </template>

    <template #default>
      <p v-if="!metric || !all.length" class="dim" style="line-height: 1.7" data-stats-empty>
        谱系目录里还没有能比的数。参数量、晶体管、制程这些都记在型号上（型号不是知识点），
        而且要在那张图的 <code>谱系.md</code> 里声明成「数值」：去「谱系 → 模型谱系」指定目录，
        或者在「原文」里把讲迭代史的文章抽成谱系。
        <button class="btn tiny" data-act="open-lineage" @click="emit('open-lineage')">
          <Icon name="network" :size="12" />去模型谱系</button>
      </p>

      <template v-else>
        <div class="seg stats-metric" role="tablist">
          <button v-for="x in metrics" :key="x.id" :class="{ on: metric.id === x.id }" :data-metric="x.id"
                  @click="picked = x.id">{{ x.label }}</button>
        </div>
        <p class="dim" style="font-size: 11px; margin: 8px 0 10px; line-height: 1.6">
          {{ all.length }} 个型号里 <b>{{ withValue.length }}</b> 个写了{{ metric.key }}；
          其中 {{ timed.length }} 个还有发布时间，能进上面那张增长图。
        </p>

        <div class="section">
          <div class="section-head">
            <Icon name="clock" :size="13" />怎么涨上来的
            <span class="tail dim">一个家族一条线 · Y 轴是对数</span>
          </div>
          <svg v-if="scale" class="chart" :viewBox="`0 0 ${W} ${H}`" role="img">
            <g class="axis">
              <line v-for="t in ticks" :key="t.label" x1="30" :y1="t.y" :x2="W - 8" :y2="t.y" />
              <text v-for="t in ticks" :key="`l${t.label}`" x="26" :y="t.y + 3" text-anchor="end">{{ t.label }}</text>
              <text x="34" :y="H - 8">{{ scale.lo }}</text>
              <text v-if="scale.hi !== scale.lo" :x="W - 14" :y="H - 8" text-anchor="end">{{ scale.hi }}</text>
            </g>
            <g v-for="(g, i) in lines" :key="g.name" :class="`s${i % 6}`">
              <path v-if="g.dots.length > 1" class="ln" :d="path(g.dots)" />
              <circle v-for="d in g.dots" :key="d.id" class="pt" :cx="d.cx" :cy="d.cy" r="4"
                      @click="emit('show-model', d.id)">
                <title>{{ d.name }}（{{ d.year }}）· {{ fmt(d.value) }}</title>
              </circle>
            </g>
          </svg>
          <p v-else class="dim" style="font-size: 11px">写了{{ metric.key }}的型号都还没有发布时间，画不出增长曲线。</p>
          <p v-if="lines.length" class="stats-legend">
            <span v-for="(g, i) in lines" :key="g.name" :class="`s${i % 6}`"><i />{{ g.name }}</span></p>
        </div>

        <div class="section">
          <div class="section-head">
            <Icon name="layers" :size="13" />谁更大
            <span class="tail">
              <select v-model="groupBy" class="sess-pick" style="max-width: 110px">
                <option v-for="(label, k) in BY" :key="k" :value="k">按{{ label }}</option>
              </select>
            </span>
          </div>
          <template v-for="g in bars" :key="g.name">
            <p class="bar-group dim">{{ g.name }}</p>
            <ul class="bars">
              <li v-for="b in g.items" :key="b.id" class="bar-row" :data-bar="b.name" @click="emit('show-model', b.id)">
                <span class="nm" :title="`${b.name}${b.year ? `（${b.year}）` : ''}`">{{ b.name }}</span>
                <span class="track"><i :style="{ width: `${b.pct}%` }" /></span>
                <span class="val tnum">{{ b.label }}</span>
              </li>
            </ul>
          </template>
        </div>

        <p class="dim" style="font-size: 11px; line-height: 1.7; margin-top: 12px">
          柱长按对数画：线性的话 117M 在 2.8T 旁边就是一条看不见的线。点一个型号去模型谱系里看它。
        </p>
      </template>
    </template>
  </Drawer>
</template>
