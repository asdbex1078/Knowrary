<script setup>
/**
 * 锚定下拉：触发器 + 浮层，点外面 / 按 Esc 收起，同一时刻只开一个。
 *
 * 原来用原生 <details class="menu">，它不会自己关、也没法做进场动画，
 * 还得手写 closeOthers 去互斥。这里统一成受控组件。
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  align: { type: String, default: 'start' },     // start | end：浮层贴触发器的左边还是右边
  placement: { type: String, default: 'bottom' },// bottom | top
  width: { type: [Number, String], default: null },
  disabled: { type: Boolean, default: false },
})

const open = ref(false)
const root = ref(null)
const panel = ref(null)

function toggle() {
  if (props.disabled) return
  open.value = !open.value
}

function close() { open.value = false }

function onDocDown(ev) {
  if (!root.value?.contains(ev.target)) close()
}

function onKey(ev) {
  if (ev.key === 'Escape' && open.value) {
    ev.stopPropagation()
    close()
  }
}

watch(open, async (v) => {
  if (v) {
    document.addEventListener('mousedown', onDocDown, true)
    document.addEventListener('keydown', onKey, true)
    await nextTick()
    panel.value?.focus?.()
  } else {
    document.removeEventListener('mousedown', onDocDown, true)
    document.removeEventListener('keydown', onKey, true)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown, true)
  document.removeEventListener('keydown', onKey, true)
})

defineExpose({ close })
</script>

<template>
  <div ref="root" class="pop-root">
    <slot name="trigger" :open="open" :toggle="toggle">
      <button class="btn" :class="{ active: open }" :disabled="disabled" @click="toggle">
        <slot name="label" />
        <Icon name="chevronDown" :size="13" class="caret" />
      </button>
    </slot>
    <Transition name="pop">
      <div v-if="open" ref="panel" class="pop-panel" tabindex="-1"
           :class="[`al-${align}`, `pl-${placement}`]"
           :style="width ? { width: typeof width === 'number' ? `${width}px` : width } : null"
           @click="$emit('inside-click')">
        <slot :close="close" />
      </div>
    </Transition>
  </div>
</template>
