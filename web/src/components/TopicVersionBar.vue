<script setup>
// 专题画布的版本条（2026-10-27，A 方案）：中心 `版本` 轴有几档就几个小片，
// 点一下切版本。切版只重染画布（paintTopicVersion），不写盘、不重建 cell。
// 档位少（内核就 0.12 / 2.6 / 7.2 三档），小片比连续滑块更直观，一眼看全演进路径。
defineProps({
  versions: { type: Array, default: () => [] },   // 有序版本名，如 ['0.12', '2.6', '7.2']
  current: { type: String, default: '' },
})
const emit = defineEmits(['set'])
</script>

<template>
  <div class="float ver-bar">
    <span class="ver-label">版本</span>
    <div class="seg small" role="tablist">
      <button v-for="v in versions" :key="v" role="tab" :aria-selected="v === current"
              :class="{ on: v === current }" @click="emit('set', v)">{{ v }}</button>
    </div>
  </div>
</template>
