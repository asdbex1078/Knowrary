<script setup>
/**
 * 重命名知识点：改 id，同时把所有指向它的引用一起迁走。
 *
 * 为什么非得在这里改、不能在 Obsidian 或 IDE 里直接改文件名：**文件名就是 id**。
 * 直接改会一次断四类引用——别人的 `[[链接]]`、画布位置与边样式、复习与答题记录、学习计划。
 * 而且事后补不回来：旧 id 已经没了，系统只看得到"少了一个、多了一个"，
 * 无从确认它俩是同一个。所以改名必须是一个动作，不能是事后的同步。
 *
 * 先算影响面给人看，确认才落盘——和 Markdown 写回一个路子。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  source: { type: Object, required: true },     // { id, name }
  taken: { type: Set, default: () => new Set() },
  impact: { type: Object, default: null },      // dry-run 结果
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['preview', 'apply', 'close'])

const next = ref(props.source.id)
const box = ref(null)

const bad = computed(() => {
  const v = next.value.trim()
  if (!v) return ''
  if (/[\\/:*?"<>|\s]/.test(v)) return '不能含空格或 / \\ : * ? " < > |（它同时是文件名）'
  if (v === props.source.id) return ''
  if (props.taken.has(v)) return `已经有一个叫「${v}」的知识点了`
  return ''
})
const ready = computed(() => !!next.value.trim() && !bad.value && next.value.trim() !== props.source.id)

// 名字一改，上一次算的影响面就作废了，别让人对着过期的数字点确认
watch(next, () => { if (props.impact) emit('preview', null) })

const rows = computed(() => {
  const i = props.impact
  if (!i) return []
  return [
    { n: i.links, text: `处 [[链接]]，分布在 ${i.files.length} 个文件`, show: i.links > 0 },
    { n: i.layout ? 1 : 0, text: '处画布位置', show: i.layout },
    { n: i.layout_edges, text: '条边的手工拐点', show: i.layout_edges > 0 },
    { n: i.refs, text: '张引用卡', show: i.refs > 0 },
    { n: i.docs, text: '个分组把它当总览文档', show: i.docs > 0 },
    { n: i.reviews, text: '次复习记录', show: i.reviews > 0 },
    { n: i.quiz, text: '条答题记录', show: i.quiz > 0 },
    { n: i.plans.length, text: `个学习计划（${i.plans.join('、')}）`, show: i.plans.length > 0 },
  ].filter((r) => r.show)
})

function onKey(ev) {
  if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') }
}
onMounted(() => { document.addEventListener('keydown', onKey, true); nextTick(() => box.value?.select()) })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog node-dialog">
    <header>
      <Icon name="pencil" :size="15" />
      <span class="who">重命名</span>
      <span class="dim">{{ source.id }}</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <label class="fld">
        <span class="lb">新名字</span>
        <input ref="box" v-model="next" @keydown.enter.prevent="ready && emit('preview', next.trim())" />
        <span class="hint">它同时是文件名和 id，改完引用会一起迁走。</span>
      </label>
      <p v-if="bad" class="warn-text">{{ bad }}</p>

      <template v-if="impact">
        <div class="quiz-label">这次会动到</div>
        <div v-if="rows.length" class="card" style="padding: 10px 12px">
          <div v-for="(r, i) in rows" :key="i" class="rename-row">
            <b class="tnum">{{ r.n }}</b> {{ r.text }}
          </div>
        </div>
        <p v-else class="dim" style="font-size: 12px">没有任何引用指向它，只改文件名。</p>
        <p class="dim" style="font-size: 11.5px; margin-top: 8px; line-height: 1.6">
          <code>{{ impact.path }}</code> → <code>{{ impact.new_path }}</code><br>
          落盘前会整份备份到 <code>.knowrary/backup/</code>。
        </p>
      </template>

      <footer>
        <span class="dim">{{ impact ? '确认无误再改' : '先看看会动到什么' }}</span>
        <button v-if="!impact" class="btn primary" :disabled="!ready || busy"
                @click="emit('preview', next.trim())">
          <Icon name="eye" :size="14" />{{ busy ? '计算中…' : '看影响' }}
        </button>
        <button v-else class="btn primary" :disabled="busy" @click="emit('apply', impact.new_id)">
          <Icon name="check" :size="14" />{{ busy ? '迁移中…' : '确认改名' }}
        </button>
      </footer>
    </div>
  </div>
</template>
