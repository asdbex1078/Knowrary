<script setup>
/**
 * 侧抽屉：左右两侧共用。自带标题栏、关闭按钮、可拖拽改宽（宽度记在 localStorage）。
 *
 * 画布是主角，抽屉必须能一键收起；宽度要能记住，否则每次开都得重新拖。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  side: { type: String, default: 'left' },        // left | right
  title: { type: String, default: '' },
  icon: { type: String, default: '' },
  storageKey: { type: String, default: '' },
  defaultWidth: { type: Number, default: 300 },
  min: { type: Number, default: 240 },
  max: { type: Number, default: 560 },
  expandable: { type: Boolean, default: false },   // 头上多一个「展宽 / 还原」开关
})
const emit = defineEmits(['close'])

const key = computed(() => (props.storageKey ? `knowrary-drawer-${props.storageKey}` : ''))

// 上限跟着视口走：写死 max 的话，宽屏上拖不开、窄屏上又能把画布整个盖掉。
// 永远给画布留 320px，否则"抽屉"就名不副实了。
const CANVAS_KEEP = 320
const vw = ref(window.innerWidth)
const maxW = computed(() => Math.max(props.min, Math.min(props.max, vw.value - CANVAS_KEEP)))

const stored = key.value ? Number(localStorage.getItem(key.value)) : 0
const width = ref(stored >= props.min ? stored : props.defaultWidth)

let startX = 0
let startW = 0
const dragging = ref(false)

function onDown(ev) {
  dragging.value = true
  startX = ev.clientX
  startW = width.value
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  ev.preventDefault()
}

function onMove(ev) {
  const delta = props.side === 'left' ? ev.clientX - startX : startX - ev.clientX
  width.value = clamp(startW + delta)
}

function clamp(w) { return Math.min(maxW.value, Math.max(props.min, w)) }

function onViewport() {
  vw.value = window.innerWidth
  width.value = clamp(width.value)          // 窗口变窄时抽屉跟着收，别盖住画布
}
window.addEventListener('resize', onViewport)
onViewport()

/** 展宽 / 还原：不想每次都去拖那条缝。展宽后的宽度照样记进 localStorage。 */
function toggleWide() {
  width.value = width.value >= maxW.value - 2 ? props.defaultWidth : maxW.value
  persist()
}

function persist() {
  if (key.value) localStorage.setItem(key.value, String(Math.round(width.value)))
  window.dispatchEvent(new Event('resize'))
}

function onUp() {
  dragging.value = false
  document.removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseup', onUp)
  persist()                                  // 画布跟着重新量宽
}

onBeforeUnmount(() => {
  onUp()
  window.removeEventListener('resize', onViewport)
})
</script>

<template>
  <aside class="drawer" :class="[`side-${side}`, { dragging }]" :style="{ width: `${width}px` }">
    <header class="drawer-head">
      <Icon v-if="icon" :name="icon" :size="15" class="head-icon" />
      <h2>{{ title }}</h2>
      <slot name="head-actions" />
      <button v-if="expandable" class="icon-btn ghost"
              :title="width >= maxW - 2 ? '还原宽度' : '展宽（也可以拖右边那条缝）'" @click="toggleWide">
        <Icon :name="width >= maxW - 2 ? 'fold' : 'unfold'" :size="15" />
      </button>
      <button class="icon-btn ghost" title="收起（Esc）" @click="emit('close')">
        <Icon name="x" :size="15" />
      </button>
    </header>
    <div class="drawer-body">
      <slot />
    </div>
    <div class="drawer-grip" @mousedown="onDown" />
  </aside>
</template>
