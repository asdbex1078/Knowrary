<script setup>
/**
 * 角落小地图：当前视口在全图里的位置，点一下就跳过去。
 *
 * 不用 x6-plugin-minimap：那个插件是把整张画布再渲染一遍（几百个 cell 的第二份 DOM），
 * 而小地图要的信息其实只有"框在哪、点在哪"——直接按 layout 画 SVG 更省，
 * 也不受 LOD 折叠影响（折叠只改画布，不改 layout）。
 *
 * 面板本身可拖可缩：固定在左下角会压住那一片的节点，而人看图时想把它挪到空白处。
 * 位置与尺寸记在 localStorage，跟主题一样属于"这台机器上的个人偏好"，不进 layout.json。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  layout: { type: Object, default: null },
  view: { type: Object, default: () => ({ cx: 0, cy: 0, w: 0, h: 0 }) },  // 视口（图坐标）
  focus: { type: String, default: null },
  collapsed: { type: Set, default: () => new Set() },
})
const emit = defineEmits(['jump', 'close'])

const STORE = 'knowrary-map-box'
const PAD = 5
const MIN = { w: 120, h: 84 }
const MAX = { w: 380, h: 280 }
const MARGIN = 10

const svgEl = ref(null)
const rootEl = ref(null)
const box = ref(load())

function load() {
  const def = { x: null, y: null, w: 152, h: 104 }   // x/y 为 null = 还没拖过，贴左下角
  try {
    return { ...def, ...(JSON.parse(localStorage.getItem(STORE) || '{}') || {}) }
  } catch {
    return def
  }
}

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify(box.value))
  } catch { /* 隐私模式下存不了就算了，本次会话仍然可用 */ }
}

// 画布区的位置要等挂载后才量得到；量到之前先按窗口算，量到之后这个 ref 一变就重排
const fallback = ref({ left: MARGIN, bottom: window.innerHeight - 16 })

/**
 * 面板位置。没拖过时贴"画布区"的左下角而不是窗口左下角——
 * 窗口左下角是活动栏和抽屉的地盘，默认摆那儿会正好压在工具窗口上。
 */
const stageBox = () => rootEl.value?.parentElement?.getBoundingClientRect()
    || { left: MARGIN, top: 0, right: window.innerWidth, bottom: window.innerHeight }

const style = computed(() => {
  const b = box.value
  const stage = fallback.value
  const left = b.x === null ? stage.left + MARGIN : b.x
  const top = b.y === null ? stage.bottom - b.h - 46 : b.y
  return { left: `${left}px`, top: `${top}px`, width: `${b.w + PAD * 2}px` }
})

const W = computed(() => box.value.w)
const H = computed(() => box.value.h)

/** 全图外框：把 layout 里所有框并起来，再把视口也算进去（视口飞到图外时仍能看见自己）。 */
const world = computed(() => {
  const boxes = [
    ...Object.values(props.layout?.groups || {}).map((g) => ({ x: g.x, y: g.y, w: g.w, h: g.h })),
    ...Object.values(props.layout?.nodes || {}).map((n) => ({ x: n.x, y: n.y, w: n.w || 160, h: n.h || 60 })),
  ]
  const v = props.view
  if (v.w && v.h) boxes.push({ x: v.cx - v.w / 2, y: v.cy - v.h / 2, w: v.w, h: v.h })
  if (!boxes.length) return { x: 0, y: 0, w: 1, h: 1, k: 1 }
  const x0 = Math.min(...boxes.map((b) => b.x))
  const y0 = Math.min(...boxes.map((b) => b.y))
  const x1 = Math.max(...boxes.map((b) => b.x + b.w))
  const y1 = Math.max(...boxes.map((b) => b.y + b.h))
  const w = Math.max(x1 - x0, 1)
  const h = Math.max(y1 - y0, 1)
  return { x: x0, y: y0, w, h, k: Math.min(W.value / w, H.value / h) }
})

const sx = (x) => (x - world.value.x) * world.value.k
const sy = (y) => (y - world.value.y) * world.value.k

const groups = computed(() => Object.entries(props.layout?.groups || {}).map(([id, g]) => ({
  id, name: g.name, x: sx(g.x), y: sy(g.y),
  w: Math.max(g.w * world.value.k, 2), h: Math.max(g.h * world.value.k, 2),
  on: id === props.focus, folded: props.collapsed.has(id),
})))

const dots = computed(() => Object.entries(props.layout?.nodes || {})
  .filter(([id]) => !props.layout?.groups?.[id])       // 幽灵记录（分组 id 出现在 nodes 里）不画
  .map(([id, n]) => ({ id, x: sx(n.x + (n.w || 160) / 2), y: sy(n.y + (n.h || 60) / 2) })))

const viewRect = computed(() => {
  const v = props.view
  if (!v.w || !v.h) return null
  return { x: sx(v.cx - v.w / 2), y: sy(v.cy - v.h / 2), w: v.w * world.value.k, h: v.h * world.value.k }
})

// —— 点图跳视口 ——

function toGraph(ev) {
  const rect = svgEl.value.getBoundingClientRect()
  const k = world.value.k || 1
  return {
    x: world.value.x + (ev.clientX - rect.left) / k,
    y: world.value.y + (ev.clientY - rect.top) / k,
  }
}

let jumping = false

function onMapDown(ev) {
  jumping = true
  emit('jump', toGraph(ev))
  window.addEventListener('mousemove', onMapMove)
  window.addEventListener('mouseup', onMapUp)
}
const onMapMove = (ev) => jumping && emit('jump', toGraph(ev))
function onMapUp() {
  jumping = false
  window.removeEventListener('mousemove', onMapMove)
  window.removeEventListener('mouseup', onMapUp)
}

// —— 拖面板 / 拉尺寸：同一套指针循环，只是算法不同 ——

/** 把面板夹回窗口内：改窗口大小、或从"默认贴边"切成真实坐标时都要过一遍。 */
function clamp() {
  const b = box.value
  const w = b.w + PAD * 2
  const stage = fallback.value
  const left = b.x === null ? stage.left + MARGIN : b.x
  const top = b.y === null ? stage.bottom - b.h - 46 : b.y
  b.x = Math.max(MARGIN, Math.min(left, window.innerWidth - w - MARGIN))
  b.y = Math.max(MARGIN, Math.min(top, window.innerHeight - b.h - MARGIN - 24))
}

function startDrag(ev, mode) {
  ev.preventDefault()
  ev.stopPropagation()
  clamp()                                   // 从"贴边默认位置"切成真实坐标再开拖
  const start = { mx: ev.clientX, my: ev.clientY, ...box.value }
  const move = (e) => {
    const dx = e.clientX - start.mx
    const dy = e.clientY - start.my
    if (mode === 'move') {
      box.value = { ...box.value, x: start.x + dx, y: start.y + dy }
    } else {
      box.value = { ...box.value,
                    w: Math.max(MIN.w, Math.min(MAX.w, start.w + dx)),
                    h: Math.max(MIN.h, Math.min(MAX.h, start.h + dy)) }
    }
  }
  const up = () => {
    clamp()
    save()
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

function measure() {
  const r = stageBox()
  fallback.value = { left: r.left, bottom: r.bottom }
}

const onWindowResize = () => { measure(); clamp(); save() }

onMounted(() => {
  measure()
  window.addEventListener('resize', onWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onWindowResize)
  window.removeEventListener('mousemove', onMapMove)
  window.removeEventListener('mouseup', onMapUp)
})
</script>

<template>
  <div ref="rootEl" class="float map" :style="style" @contextmenu.prevent>
    <div class="mm-grip" title="拖我换个位置" @mousedown="startDrag($event, 'move')">
      <Icon name="map" :size="11" />
      <span>小地图</span>
      <button class="icon-btn ghost tiny" title="收起（右下角缩放条里可以再打开）"
              @mousedown.stop @click="emit('close')">
        <Icon name="x" :size="11" />
      </button>
    </div>
    <svg ref="svgEl" :width="W" :height="H" @mousedown.prevent="onMapDown">
      <rect v-for="g in groups" :key="g.id" class="mm-group" :class="{ on: g.on, folded: g.folded }"
            :x="g.x" :y="g.y" :width="g.w" :height="g.h" rx="2">
        <title>{{ g.name }}</title>
      </rect>
      <circle v-for="d in dots" :key="d.id" class="mm-dot" :cx="d.x" :cy="d.y" r="1.2" />
      <rect v-if="viewRect" class="mm-view" :x="viewRect.x" :y="viewRect.y"
            :width="Math.max(viewRect.w, 3)" :height="Math.max(viewRect.h, 3)" rx="2" />
    </svg>
    <span class="mm-resize" title="拖我改大小" @mousedown="startDrag($event, 'size')" />
  </div>
</template>
