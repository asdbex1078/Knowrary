<script setup>
/**
 * 画布右键菜单：跟着鼠标落点走的浮层。
 *
 * 不用 Popover：那个是锚在触发按钮上的（position: absolute 相对触发器），
 * 而右键菜单要贴在任意一个视口坐标上，还得在贴近边缘时自己翻面。
 */
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  x: { type: Number, required: true },
  y: { type: Number, required: true },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  // [{ id, label, icon, hint, danger, disabled, on } | { sep: true }]
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['pick', 'close'])

const el = ref(null)
const pos = ref({ left: -9999, top: -9999 })

function place() {
  const box = el.value?.getBoundingClientRect()
  if (!box) return
  const left = Math.max(8, Math.min(props.x, window.innerWidth - box.width - 8))
  // 下方放不下就往上翻，贴着鼠标而不是被视口切掉
  const top = props.y + box.height > window.innerHeight - 8
    ? Math.max(8, props.y - box.height)
    : props.y
  pos.value = { left, top }
}

function onDown(ev) {
  if (!el.value?.contains(ev.target)) emit('close')
}

function onKey(ev) {
  if (ev.key === 'Escape') {
    ev.stopPropagation()
    emit('close')
  }
}

function pick(item) {
  if (item.disabled) return
  emit('pick', item.id)
  emit('close')
}

onMounted(async () => {
  await nextTick()
  place()
  document.addEventListener('mousedown', onDown, true)
  document.addEventListener('keydown', onKey, true)
  window.addEventListener('resize', place)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDown, true)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('resize', place)
})
</script>

<template>
  <div ref="el" class="ctx-menu" :style="{ left: `${pos.left}px`, top: `${pos.top}px` }"
       @contextmenu.prevent>
    <div v-if="title" class="ctx-head">
      <span class="t">{{ title }}</span>
      <span v-if="subtitle" class="s">{{ subtitle }}</span>
    </div>
    <template v-for="(item, i) in items" :key="item.id || `sep-${i}`">
      <div v-if="item.sep" class="pop-sep" />
      <button v-else class="pop-item" :class="{ danger: item.danger, on: item.on }"
              :disabled="item.disabled" @click="pick(item)">
        <Icon v-if="item.icon" :name="item.icon" :size="14" />
        <span>{{ item.label }}</span>
        <span v-if="item.hint" class="hint">{{ item.hint }}</span>
      </button>
    </template>
  </div>
</template>
