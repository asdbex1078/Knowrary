<script setup>
/**
 * 校验已有边的进度条：谁在审、第几批、审了多少条、这一批等了几秒，能停。
 * 批量审一个领域要好几分钟——「模型调用要看得见」：屏幕上得一直有东西在走。
 * 自带秒表，只在有任务时走，不让外面的面板每秒重渲染。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  run: { type: Object, default: null },   // { label, total, done, batch, batches, started, stop }
})
const emit = defineEmits(['stop'])

const now = ref(Date.now())
let timer = null
watch(() => !!props.run, (on) => {
  clearInterval(timer)
  timer = on ? setInterval(() => { now.value = Date.now() }, 1000) : null
}, { immediate: true })
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div v-if="run" class="cc-audit busy edge-run" data-edge-run>
    <Icon name="checklist" :size="13" />
    <span>review 在审「{{ run.label }}」的关系 · 第 {{ run.batch }}/{{ run.batches }} 批 ·
      已审 {{ run.done }}/{{ run.total }} 条 · 这一批已等 {{ Math.max(0, Math.round((now - run.started) / 1000)) }} 秒</span>
    <button class="btn subtle tiny" :disabled="run.stop" style="margin-left: auto" data-act="stop-edge-check"
            title="这一批审完就停，已经审完的结论都留着" @click="emit('stop')">{{ run.stop ? '这批审完就停' : '停' }}</button>
  </div>
</template>
