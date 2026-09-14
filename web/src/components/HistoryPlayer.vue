<script setup>
// 历史视图的年份播放器：浮在画布底部居中，像视频播放器一样理解成本最低。
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  playing: { type: Boolean, default: false },
  upto: { type: Number, default: null },
  range: { type: Array, default: () => [0, 0] },
  compact: { type: Boolean, default: false },
  validity: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle-play', 'set-upto', 'toggle-compact', 'toggle-validity'])

const cur = computed(() => (props.upto === null ? props.range[1] : props.upto))
const pct = computed(() => {
  const [a, b] = props.range
  if (b <= a) return 100
  return Math.round(((cur.value - a) / (b - a)) * 100)
})
</script>

<template>
  <div class="float player">
    <button class="icon-btn" :title="playing ? '暂停' : '按年回放'" @click="emit('toggle-play')">
      <Icon :name="playing ? 'pause' : 'play'" :size="16" />
    </button>
    <input class="range" type="range" :min="range[0]" :max="range[1]" :value="cur"
           :style="{ '--pct': `${pct}%` }" @input="emit('set-upto', Number($event.target.value))" />
    <span class="yr">{{ upto === null ? '全部年份' : `≤ ${upto}` }}</span>
    <button v-if="upto !== null" class="icon-btn ghost tiny" title="放开年份限制" @click="emit('set-upto', null)">
      <Icon name="x" :size="13" />
    </button>
    <span class="sep" />
    <button class="btn tiny" :class="{ active: compact }" title="空白超过 20 年的区段压缩成固定宽度"
            @click="emit('toggle-compact')">紧凑</button>
    <button class="btn tiny" :class="{ active: validity }"
            title="按 start_year ≤ 当前年 < end_year 过滤：只看那一年仍然有效的东西"
            @click="emit('toggle-validity')">有效期</button>
  </div>
</template>
