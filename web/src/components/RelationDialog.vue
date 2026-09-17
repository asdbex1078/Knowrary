<script setup>
/**
 * 建立关系：先选类型，再搜目标，两步走完直接写回 md。
 *
 * 两个刻意的设计：
 * 1. 选完类型立刻给出**反向读法**（A 基于 B ⇒ B 支撑 A）。方向是这套关系表里最容易
 *    写反的东西，而写反了图就会从"知识网"退化成"箭头乱指的连线图"。
 * 2. 目标用搜索选，不用鼠标从画布上拖——两个节点隔着半张图时，拖拽要先缩小再对准，
 *    比打三个字慢得多。选中后画布自己飞过去（见 App.vue 的 flyTo）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import { FAMILY_STYLE } from '../canvas/shapes.js'

const props = defineProps({
  source: { type: Object, required: true },         // { id, name }
  families: { type: Array, default: () => [] },     // [{ family, types, meta }]
  nodes: { type: Array, default: () => [] },        // index.nodes
  placed: { type: Set, default: () => new Set() },  // 已经在画布上的 id
  linked: { type: Object, default: () => ({}) },    // { 目标id: [已有的关系类型] }
  preset: { type: Object, default: null },          // { relation, target }：欠账清单点「连边」带过来的默认值
})
const emit = defineEmits(['create', 'close'])

const step = ref('type')
const relation = ref('')
const swap = ref(false)      // 反过来：把关系写进目标的 md（目标 → 源）
const query = ref('')
const cursor = ref(0)
const searchEl = ref(null)

const metaOf = (type) => {
  for (const f of props.families) if (f.types.includes(type)) return f.meta?.[type] || {}
  return {}
}
const familyOf = (type) => props.families.find((f) => f.types.includes(type))?.family || '弱关联'

const lineStyle = (family) => {
  const s = FAMILY_STYLE[family] || FAMILY_STYLE.弱关联
  return { borderTopColor: s.stroke, borderTopWidth: `${Math.max(s.strokeWidth, 1.5)}px`,
           borderTopStyle: s.strokeDasharray ? 'dashed' : 'solid' }
}

const from = computed(() => (swap.value ? (target.value?.name || target.value?.id || '目标') : props.source.name || props.source.id))
const to = computed(() => (swap.value ? props.source.name || props.source.id : (target.value?.name || target.value?.id || '目标')))

/** 反向读法：把"方向对不对"在选类型的当场就摆出来。 */
const inverseHint = computed(() => {
  if (!relation.value) return ''
  const m = metaOf(relation.value)
  const a = from.value
  const b = to.value
  if (m.symmetric) return `对称关系：${a} 与 ${b} 互为「${relation.value}」，没有方向之分`
  if (m.canonical) return `反向写法：会归一成 ${b} ${m.canonical} ${a}（md 里仍逐字保留你写的「${relation.value}」）`
  if (m.inverse) return `反向读法：${b} ${m.inverse} ${a}`
  return `单向关系：只从 ${a} 指向 ${b}，反向没有登记类型`
})

const hits = computed(() => {
  const q = query.value.trim().toLowerCase()
  return props.nodes
    .filter((n) => n.id !== props.source.id && !n.virtual)
    .filter((n) => !q || `${n.id} ${n.name || ''} ${n.desc || ''} ${n.field || ''}`.toLowerCase().includes(q))
    .sort((a, b) => (b.degree || 0) - (a.degree || 0))
    .slice(0, 12)
})

// 当前高亮的那条搜索结果就是"目标"，句子跟着上下键实时变
const target = computed(() => hits.value[cursor.value] || null)

watch(hits, () => { cursor.value = 0 })

function chooseType(type) {
  relation.value = type
  step.value = 'target'
  nextTick(() => searchEl.value?.focus())
}

function confirm(node) {
  const picked = node || hits.value[cursor.value]
  if (!picked || !relation.value) return
  emit('create', { relation: relation.value, target: picked.id, swap: swap.value })
}

function onSearchKey(ev) {
  if (ev.key === 'ArrowDown') { ev.preventDefault(); cursor.value = Math.min(cursor.value + 1, hits.value.length - 1) }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); cursor.value = Math.max(cursor.value - 1, 0) }
  else if (ev.key === 'Enter') { ev.preventDefault(); confirm() }
}

function onKey(ev) {
  if (ev.key !== 'Escape') return
  ev.stopPropagation()
  if (step.value === 'target') { step.value = 'type'; return }
  emit('close')
}

/**
 * 欠账清单里的连边建议带着"默认类型 + 默认目标"过来，直接落到第二步。
 *
 * **只是默认值，不是决定**：类型能退回去换、方向能反过来、目标能重搜。建议算出来的
 * `包含` / `对比` 是按名字猜的，猜错了写进 md 就是一条骗人的边。
 */
onMounted(() => {
  if (!props.preset?.relation) return
  relation.value = props.preset.relation
  step.value = 'target'
  query.value = props.preset.target || ''
  nextTick(() => {
    const i = hits.value.findIndex((n) => n.id === props.preset.target)
    if (i >= 0) cursor.value = i
    searchEl.value?.focus()
  })
})

onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog">
    <header>
      <Icon name="link" :size="15" />
      <span class="who">{{ source.name || source.id }}</span>
      <span class="dim">建立关系</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <!-- ---------- 第一步：选类型 ---------- -->
    <div v-if="step === 'type'" class="rel-body scroll-thin">
      <p class="tip">先选关系类型——类型决定线条的颜色与粗细，也决定这条边属于哪个族。</p>
      <div v-for="f in families" :key="f.family" class="fam">
        <div class="fam-head">
          <span class="swatch" :style="lineStyle(f.family)" />
          {{ f.family }}
        </div>
        <div class="types">
          <button v-for="t in f.types" :key="t" class="type-chip" @click="chooseType(t)">
            {{ t }}
            <span v-if="metaOf(t).inverse" class="inv">⇄ {{ metaOf(t).inverse }}</span>
            <span v-else-if="metaOf(t).symmetric" class="inv">对称</span>
          </button>
        </div>
      </div>
    </div>

    <!-- ---------- 第二步：搜目标 ---------- -->
    <div v-else class="rel-body">
      <div class="sentence">
        <button class="back" title="换个类型" @click="step = 'type'">
          <Icon name="chevronLeft" :size="13" />{{ relation }}
        </button>
        <span class="s-a">{{ from }}</span>
        <span class="arrow" :style="lineStyle(familyOf(relation))" />
        <span class="s-b">{{ to }}</span>
        <button class="icon-btn ghost tiny" title="反过来：把这条关系写进对方的文件"
                :class="{ active: swap }" @click="swap = !swap">
          <Icon name="rotate" :size="13" />
        </button>
      </div>
      <p class="tip inv-hint">{{ inverseHint }}</p>

      <div class="search-row">
        <Icon name="search" :size="14" class="dim" />
        <input ref="searchEl" v-model="query" placeholder="搜知识点：名称 / id / 摘要"
               @keydown="onSearchKey" />
      </div>
      <ul class="hits scroll-thin">
        <li v-for="(n, i) in hits" :key="n.id" :class="{ cur: i === cursor }"
            @mouseenter="cursor = i" @click="confirm(n)">
          <span class="nm">{{ n.name || n.id }}</span>
          <span v-if="linked[n.id]?.length" class="tag warn" :title="`已有关系：${linked[n.id].join('、')}`">
            已连 {{ linked[n.id].length }}
          </span>
          <span v-if="!placed.has(n.id)" class="tag">未上画布</span>
          <span class="ds">{{ n.desc || n.field || '' }}</span>
        </li>
        <li v-if="!hits.length" class="none">没有匹配的知识点</li>
      </ul>
      <footer>
        <span class="dim">↑↓ 选择 · Enter 建立 · Esc 返回</span>
        <button class="btn primary" :disabled="!hits.length" @click="confirm()">
          <Icon name="check" :size="14" />建立关系
        </button>
      </footer>
    </div>
  </div>
</template>
