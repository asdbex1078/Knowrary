<script setup>
// 统一的图标渲染器：只认 icons.js 里的名字，尺寸 / 线宽由外部给。
import { computed } from 'vue'
import { ICONS } from './icons.js'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  width: { type: [Number, String], default: 1.7 },
})

const paths = computed(() => ICONS[props.name] || [])
</script>

<template>
  <svg class="icon" :width="size" :height="size" viewBox="0 0 24 24" fill="none"
       :stroke-width="width" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"
       aria-hidden="true" focusable="false">
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>
