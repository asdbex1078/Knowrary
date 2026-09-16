<script setup>
/**
 * 左侧活动栏：一列图标 = 一个工具窗口，互斥开合（IDE 的工具窗口条）。
 * 角标直接把"有多少待办"摆在眼前，不用点进去才知道。
 */
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  active: { type: String, default: '' },
  mode: { type: String, default: 'structure' },
  inbox: { type: Number, default: 0 },
  due: { type: Number, default: 0 },
  theme: { type: String, default: 'light' },
})
const emit = defineEmits(['select', 'toggle-theme'])

const items = computed(() => {
  const list = [
    { id: 'inbox', icon: 'inbox', tip: 'Inbox · 待上图的知识点', badge: props.inbox, structureOnly: true },
    { id: 'plans', icon: 'checklist', tip: '项目 · 学习计划 / 面试方案 / 领域地图', structureOnly: true },
    { id: 'study', icon: 'rotate', tip: '学习 · 今日复习与测验', badge: props.due, gold: true },
    { id: 'digest', icon: 'layers', tip: '欠账 · 草稿 / 桥 / 重复 / stub' },
    { id: 'calendar', icon: 'clock', tip: '日历 · 每天建了多少、复习了多少（全是算出来的）' },
    { id: 'assets', icon: 'image', tip: '素材 · 贴图与便签', structureOnly: true },
    { id: 'timeline', icon: 'timeline', tip: '时间线 · 分组与过滤', historyOnly: true },
  ]
  return list.filter((it) => {
    if (it.structureOnly) return props.mode === 'structure'
    if (it.historyOnly) return props.mode === 'history'
    return true
  })
})
</script>

<template>
  <nav class="rail">
    <button v-for="it in items" :key="it.id" class="rail-btn" :class="{ on: active === it.id }"
            :data-tip="it.tip" @click="emit('select', it.id)">
      <Icon :name="it.icon" :size="18" />
      <span v-if="it.badge" class="badge" :class="{ gold: it.gold }">{{ it.badge > 99 ? '99+' : it.badge }}</span>
    </button>

    <span class="spacer" />

    <button class="rail-btn" :data-tip="theme === 'dark' ? '切到浅色' : '切到深色'" @click="emit('toggle-theme')">
      <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="17" />
    </button>
  </nav>
</template>
