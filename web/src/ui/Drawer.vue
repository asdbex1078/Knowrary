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
})
const emit = defineEmits(['close'])

const key = computed(() => (props.storageKey ? `knowrary-drawer-${props.storageKey}` : ''))
const stored = key.value ? Number(localStorage.getItem(key.value)) : 0
const width = ref(stored >= props.min && stored <= props.max ? stored : props.defaultWidth)

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
  width.value = Math.min(props.max, Math.max(props.min, startW + delta))
}

function onUp() {
  dragging.value = false
  document.removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseup', onUp)
  if (key.value) localStorage.setItem(key.value, String(Math.round(width.value)))
  window.dispatchEvent(new Event('resize'))   // 画布跟着重新量宽
}

onBeforeUnmount(onUp)
</script>

<template>
  <aside class="drawer" :class="[`side-${side}`, { dragging }]" :style="{ width: `${width}px` }">
    <header class="drawer-head">
      <Icon v-if="icon" :name="icon" :size="15" class="head-icon" />
      <h2>{{ title }}</h2>
      <slot name="head-actions" />
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
