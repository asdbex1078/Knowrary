<script setup>
/**
 * 写入审核挡下来的时候，把结论摆在这儿。
 *
 * **它不是错误弹窗**：挡下是这条链路的正常结局之一，所以主按钮是「回去改」，
 * 而不是"确定"。每条问题都带依据和改法——只说"不对"而不说"哪不对"的审核，
 * 人第二次就会去把开关关掉。
 *
 * 「仍然写入」是故意留的后门（设置里可以关）。模型也会看走眼，不给后门，
 * 整套审核就会被关掉，那时连算得出来的那几条也不查了。
 */
import { computed, onBeforeUnmount, onMounted } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  report: { type: Object, required: true },
  forceAllowed: { type: Boolean, default: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'force'])

const blocking = computed(() => (props.report.issues || []).filter((i) => i.level === 'block'))
const warnings = computed(() => (props.report.issues || []).filter((i) => i.level !== 'block'))

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<style scoped>
.au-list { display: flex; flex-direction: column; gap: 8px; margin: 6px 0 2px; }
.au-item { border: 1px solid var(--line); border-radius: 9px; padding: 9px 11px; background: var(--surface); }
.au-item.block { border-color: var(--danger, #d9534f); }
.au-head { display: flex; align-items: baseline; gap: 6px; font-size: 12.5px; }
.au-head b { flex: 1; }
.au-path { font-size: 11px; opacity: .6; white-space: nowrap; }
.au-why, .au-fix { font-size: 11.5px; line-height: 1.6; margin: 4px 0 0; }
.au-fix { color: var(--ok, #2f7d52); }
.au-sum { font-size: 12.5px; line-height: 1.7; margin: 0 0 8px; }
</style>

<template>
  <div class="rel-dialog node-dialog">
    <header>
      <Icon name="checklist" :size="15" />
      <span class="who">审核没通过</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <p v-if="report.summary" class="au-sum">{{ report.summary }}</p>

      <div v-if="blocking.length" class="quiz-label">这几条得先改</div>
      <div class="au-list">
        <div v-for="(it, i) in blocking" :key="`b${i}`" class="au-item block">
          <div class="au-head"><b>{{ it.message }}</b><span class="au-path">{{ it.path }}</span></div>
          <p v-if="it.why" class="au-why dim">依据：{{ it.why }}</p>
          <p v-if="it.fix" class="au-fix">改法：{{ it.fix }}</p>
        </div>
      </div>

      <template v-if="warnings.length">
        <div class="quiz-label">顺带提一句（不挡）</div>
        <div class="au-list">
          <div v-for="(it, i) in warnings" :key="`w${i}`" class="au-item">
            <div class="au-head"><span>{{ it.message }}</span><span class="au-path">{{ it.path }}</span></div>
            <p v-if="it.fix" class="au-fix">{{ it.fix }}</p>
          </div>
        </div>
      </template>

      <footer>
        <span class="dim">{{ forceAllowed ? '改完再写，或者你确认它看走眼了' : '「仍然写入」在设置里关着' }}</span>
        <button v-if="forceAllowed" class="btn subtle" :disabled="busy" @click="emit('force')">
          {{ busy ? '写入中…' : '仍然写入' }}
        </button>
        <button class="btn primary" :disabled="busy" @click="emit('close')">
          <Icon name="pencil" :size="14" />回去改
        </button>
      </footer>
    </div>
  </div>
</template>
