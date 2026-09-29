<script setup>
/**
 * Inbox 条目上「为什么放这儿」：把握（有把握 / 参考 / 猜的 / 判不出）+ 一句理由；
 * 投不出票的（猜的 / 判不出）给「问 AI 该放哪」，问的是它所在的整块；AI 的结论挂在下面，放不放人点。
 * 问的时候要看得见：谁在想、等了几秒（自带秒表，只在在问的时候走）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  item: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  curating: { type: Object, default: null },   // { ids, started }：正在问的那一块
})
const emit = defineEmits(['curate', 'placeAi'])

const SAY = { strong: '有把握', weak: '参考', guess: '猜的', none: '判不出' }
const CHIP = { strong: 'm-mastered', weak: 'm-due', guess: 'm-unbuilt', none: 'm-unbuilt' }

const asking = computed(() => !!props.curating?.ids?.includes(props.item.id))
const size = computed(() => props.item.cluster?.length || 1)
const canAsk = computed(() => ['guess', 'none'].includes(props.item.confidence))

const now = ref(Date.now())
let timer = null
watch(asking, (on) => {
  clearInterval(timer)
  now.value = Date.now()
  timer = on ? setInterval(() => { now.value = Date.now() }, 1000) : null
}, { immediate: true })
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="inbox-reason" :data-confidence="item.confidence">
    <p v-if="item.reason" class="dim">
      <span class="chip" :class="CHIP[item.confidence]">{{ SAY[item.confidence] || item.confidence }}</span>
      {{ item.reason }}
    </p>
    <div v-if="asking" class="cc-audit busy" data-curating>
      <Icon name="clock" :size="12" />
      <span>review 在想{{ size > 1 ? `这 ${size} 个` : '它' }}该放哪 · 已等 {{ Math.max(0, Math.round((now - curating.started) / 1000)) }} 秒</span>
    </div>
    <div v-else-if="item.ai" class="inbox-ai" data-ai>
      <span>AI 建议放「<b>{{ item.ai.group_name }}</b>」<template v-if="item.ai.why">——{{ item.ai.why }}</template></span>
      <span class="dim" :title="item.ai.at">（{{ item.ai.model }}）</span>
      <div class="acts">
        <button class="btn primary tiny" :disabled="busy" data-act="place-ai" @click="emit('placeAi', item)">
          <Icon name="target" :size="12" />{{ (item.ai.ids?.length || 1) > 1 ? `这 ${item.ai.ids.length} 个都放进去` : '放进去' }}</button>
        <button class="btn subtle tiny" :disabled="busy || !!curating" data-act="curate" @click="emit('curate', item)">再问一次</button>
      </div>
    </div>
    <button v-else-if="canAsk" class="btn tiny" :disabled="busy || !!curating" data-act="curate"
            :title="size > 1 ? `它和另外 ${size - 1} 个点连成一块、都还没上画布：整块一起问` : '让 review 看分组树和它的内容，给一个分组和理由'"
            @click="emit('curate', item)">
      <Icon name="help" :size="12" />问 AI 该放哪{{ size > 1 ? `（整块 ${size} 个）` : '' }}</button>
  </div>
</template>
