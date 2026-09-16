<script setup>
// 沿演化链导览：跟着最长的那条「谁接谁」一站一站走，镜头推到节点上，这里浮出讲解。
//
// 为什么要有它：时间线的叙事单位是「哪一年」，但技术史真正的叙事单位是「谁接谁」——
// 年份只是坐标。实盘 83 年里只有 22 年有节点，按年走一路都是空档；按链走站站有内容。
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  chain: { type: Array, default: () => [] },     // 链上每一站的节点 id
  index: { type: Number, default: 0 },           // 当前第几站（0 基）
  stop: { type: Object, default: null },         // 当前这站的节点元数据
  via: { type: String, default: '' },            // 上一站到这一站是什么关系
  playing: { type: Boolean, default: false },
})
const emit = defineEmits(['go', 'prev', 'next', 'toggle', 'close'])

const total = computed(() => props.chain.length)
const first = computed(() => props.index <= 0)
const last = computed(() => props.index >= total.value - 1)
</script>

<template>
  <div class="float tour">
    <header>
      <span class="badge">第 {{ index + 1 }}/{{ total }} 站</span>
      <span v-if="stop?.year" class="yr tnum">{{ stop.year }}</span>
      <span v-if="via" class="via">{{ via }}</span>
      <button class="icon-btn ghost tiny" title="退出导览（Esc）" @click="emit('close')">
        <Icon name="x" :size="13" />
      </button>
    </header>

    <h3>{{ stop?.name || stop?.id || '—' }}</h3>
    <p v-if="stop?.desc" class="desc">{{ stop.desc }}</p>
    <p v-else class="desc dim">这个点还没写 desc——导览到它时就只有一个名字可读。</p>

    <!-- 站点条：既是进度也是目录，点哪站跳哪站 -->
    <ol class="stops">
      <li v-for="(id, n) in chain" :key="id">
        <button :class="{ on: n === index, done: n < index }" :title="id"
                @click="emit('go', n)" />
      </li>
    </ol>

    <footer>
      <button class="btn tiny" :disabled="first" @click="emit('prev')">
        <Icon name="chevronLeft" :size="13" />上一站
      </button>
      <button class="btn tiny primary" @click="emit('toggle')">
        <Icon :name="playing ? 'pause' : 'play'" :size="13" />{{ playing ? '暂停' : '自动走' }}
      </button>
      <button class="btn tiny" :disabled="last" @click="emit('next')">
        下一站<Icon name="chevronRight" :size="13" />
      </button>
    </footer>
  </div>
</template>
