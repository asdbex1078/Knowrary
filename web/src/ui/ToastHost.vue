<script setup>
// 浮层提示：原来是顶在画布上方的一条 banner，会把画布压矮、且一次只能显示一条。
import Icon from './Icon.vue'

defineProps({ items: { type: Array, default: () => [] } })
const emit = defineEmits(['dismiss'])

const ICON = { error: 'warn', success: 'check', info: 'eye' }
</script>

<template>
  <div class="toast-host">
    <TransitionGroup name="toast">
      <div v-for="t in items" :key="t.id" class="toast" :class="t.kind || 'info'">
        <Icon :name="ICON[t.kind] || 'eye'" :size="15" class="toast-icon" />
        <span class="toast-text">{{ t.text }}</span>
        <button class="icon-btn ghost tiny" title="知道了" @click="emit('dismiss', t.id)">
          <Icon name="x" :size="13" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>
