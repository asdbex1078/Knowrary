<script setup>
/**
 * 顶栏：只放"全局"的东西——身份、视图切换、搜索、保存状态、系统菜单。
 * 画布操作（缩放 / 布局 / 撤销）一律下放到画布浮层，内容面板下放到左侧活动栏。
 */
import { computed, ref } from 'vue'
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'

const props = defineProps({
  mode: { type: String, default: 'structure' },
  hits: { type: Array, default: () => [] },
  status: { type: String, default: 'saved' },
  statusText: { type: String, default: '' },
  theme: { type: String, default: 'light' },
  has3d: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['switch-mode', 'search', 'goto', 'toggle-theme', 'reload', 'rebuild', 'open-3d', 'help'])

const q = ref('')
const cursor = ref(0)
const inputEl = ref(null)

function onInput(v) {
  q.value = v
  cursor.value = 0
  emit('search', v)
}

function pick(id) {
  q.value = ''
  emit('search', '')
  emit('goto', id)
  inputEl.value?.blur()
}

function onKey(ev) {
  if (!props.hits.length) return
  if (ev.key === 'ArrowDown') { ev.preventDefault(); cursor.value = (cursor.value + 1) % props.hits.length }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); cursor.value = (cursor.value - 1 + props.hits.length) % props.hits.length }
  else if (ev.key === 'Enter') { ev.preventDefault(); pick(props.hits[cursor.value].id) }
  else if (ev.key === 'Escape') { onInput(''); inputEl.value?.blur() }
}

const statusClass = computed(() => props.status)
defineExpose({ focus: () => inputEl.value?.focus() })
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <span class="mark"><Icon name="cube" :size="14" :width="1.9" /></span>
      Knowrary
    </div>

    <div class="seg" role="tablist">
      <button :class="{ on: mode === 'structure' }" role="tab" @click="emit('switch-mode', 'structure')">
        <Icon name="network" :size="14" />结构
      </button>
      <button :class="{ on: mode === 'history' }" role="tab" title="只看有 year 的节点，X 轴是年份"
              @click="emit('switch-mode', 'history')">
        <Icon name="clock" :size="14" />历史
      </button>
    </div>

    <span class="divider" />

    <div class="search">
      <div class="field">
        <Icon name="search" :size="14" />
        <input ref="inputEl" :value="q" placeholder="搜索知识点…" spellcheck="false"
               @input="onInput($event.target.value)" @keydown="onKey" />
        <kbd v-if="!q">/</kbd>
        <button v-else class="icon-btn ghost tiny" title="清空" @click="onInput('')">
          <Icon name="x" :size="12" />
        </button>
      </div>
      <ul v-if="hits.length" class="hits scroll-thin">
        <li v-for="(h, i) in hits" :key="h.id" :class="{ cursor: i === cursor }"
            @mouseenter="cursor = i" @mousedown.prevent="pick(h.id)">
          <span class="nm">{{ h.name || h.id }}</span>
          <span class="fd">{{ h.field || '未分类' }}</span>
        </li>
      </ul>
    </div>

    <span class="grow" />

    <span class="save-state" :class="statusClass" :title="statusText">
      <span class="dot" />{{ statusText }}
    </span>

    <Popover align="end" :width="212">
      <template #trigger="{ toggle, open }">
        <button class="icon-btn" :class="{ active: open }" title="更多" @click="toggle">
          <Icon name="more" :size="16" />
        </button>
      </template>
      <template #default="{ close }">
        <button class="pop-item" @click="emit('toggle-theme'); close()">
          <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="15" />
          {{ theme === 'dark' ? '切到浅色' : '切到深色' }}
        </button>
        <button v-if="has3d" class="pop-item" @click="emit('open-3d'); close()">
          <Icon name="cube" :size="15" />3D 总览<span class="hint">新页面</span>
        </button>
        <div class="pop-sep" />
        <button class="pop-item" :disabled="busy" @click="emit('reload'); close()">
          <Icon name="refresh" :size="15" />重新加载
        </button>
        <button class="pop-item" title="画布没反应时点这里重建（不影响已保存的布局）"
                @click="emit('rebuild'); close()">
          <Icon name="rotate" :size="15" />恢复画布
        </button>
        <div class="pop-sep" />
        <button class="pop-item" @click="emit('help'); close()">
          <Icon name="help" :size="15" />快捷键<span class="hint">?</span>
        </button>
      </template>
    </Popover>
  </header>
</template>
