<script setup>
// 专题图的 AI 面板（2026-10-13 第 6 步）：「审这张图」的意见 + 「问 AI 该画什么」。
//
// 都是点了才调模型；调的时候写明在等谁、等了多久（模型调用必须看得见）。
// 意见挂在框上（框角的金色角标），这里列全：采纳 = 照意见里的改法改画布（改字 / 加框 / 升级 / 连线），
// 忽略 = 只记状态。结果存在专题文件夹里，刷新还在。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import Markdown from '../ui/Markdown.vue'

const props = defineProps({
  doc: { type: Object, default: () => ({}) },         // GET /api/topics/<专题>/review
  busy: { type: String, default: '' },                // '' | 'review' | 'advice'
  focus: { type: String, default: '' },               // 当前选中的框：它的意见排前面、高亮
  names: { type: Function, default: (x) => x },       // 框 / 知识点 id → 给人看的名字
})
const emit = defineEmits(['review', 'advice', 'adopt', 'ignore', 'pick', 'close'])

const question = ref('')
const KIND = { fact: '事实错误', missing: '缺了什么', upgrade: '建议升级', link: '建议连线' }
const review = computed(() => props.doc?.review || null)
const items = computed(() => {
  const all = review.value?.items || []
  const rank = (it) => (it.status === 'open' ? 0 : 1) * 2 + (it.target === props.focus ? 0 : 1)
  return [...all].sort((a, b) => rank(a) - rank(b))
})
const openCount = computed(() => items.value.filter((it) => it.status === 'open').length)
const advice = computed(() => props.doc?.advice || [])

// 等模型时数秒：看得见在等、等了多久
const waited = ref(0)
let timer = null
watch(() => props.busy, (b) => {
  clearInterval(timer)
  waited.value = 0
  if (b) timer = setInterval(() => { waited.value += 1 }, 1000)
})
onBeforeUnmount(() => clearInterval(timer))

const secs = (ms) => `${(ms / 1000).toFixed(1)}s`

function fixText(it) {
  const f = it.fix || {}
  if (it.kind === 'fact') return `${f.field === 'tip' ? '悬停说明' : '文字'}改成「${f.value}」`
  if (it.kind === 'missing') return `加一个框「${f.text}」${it.target ? `，放进「${props.names(it.target)}」` : ''}`
  if (it.kind === 'upgrade') return f.node ? `绑定已有知识点「${props.names(f.node)}」` : `新建知识点「${f.name || ''}」`
  if (it.kind === 'link') return `连线 ${props.names(f.from?.id)} → ${props.names(f.to?.id)}${f.text ? `「${f.text}」` : ''}`
  return ''
}

function ask() {
  emit('advice', question.value)
}
</script>

<template>
  <aside class="topic-ai" data-topic-ai>
    <header class="tai-head">
      <Icon name="eye" :size="14" /><b>AI</b>
      <button class="icon-btn ghost tiny" title="收起" @click="emit('close')"><Icon name="x" :size="13" /></button>
    </header>

    <section>
      <div class="tai-row">
        <button class="btn tiny primary" data-act="topic-review" :disabled="!!busy" @click="emit('review')">
          <Icon name="check" :size="13" />{{ review ? '再审一次' : '审这张图' }}
        </button>
        <span v-if="busy === 'review'" class="dim" data-waiting>review 角色在审…{{ waited }}s</span>
        <span v-else-if="review" class="dim">{{ review.model }} · {{ secs(review.ms) }} · {{ openCount }} 条待处理</span>
      </div>
      <p v-if="review?.summary" class="tai-sum">{{ review.summary }}</p>
      <p v-if="review && !items.length" class="dim">没挑出要改的地方。</p>
      <p v-if="review?.dropped" class="dim tai-small">另有 {{ review.dropped }} 条指不到画布上的东西，没摆出来（已记进问题流）。</p>
      <ul class="tai-items">
        <li v-for="it in items" :key="it.id" :class="['tai-item', it.status, { focus: it.target && it.target === focus }]"
            :data-review-item="it.id" @click="emit('pick', it)">
          <div class="tai-item-head">
            <span class="tai-kind" :class="it.kind">{{ KIND[it.kind] }}</span>
            <span v-if="it.target" class="dim">「{{ names(it.target) }}」</span>
            <span v-if="it.status !== 'open'" class="dim tai-state">{{ it.status === 'adopted' ? '已采纳' : '已忽略' }}</span>
          </div>
          <div class="tai-say">{{ it.say }}</div>
          <div v-if="it.why && it.status === 'open'" class="dim tai-small">{{ it.why }}</div>
          <div v-if="it.status === 'open'" class="tai-fix">
            <span class="dim tai-small">{{ fixText(it) }}</span>
            <span class="tai-acts">
              <button class="btn tiny" data-act="review-adopt" @click.stop="emit('adopt', it)">采纳</button>
              <button class="btn tiny ghost" data-act="review-ignore" @click.stop="emit('ignore', it)">忽略</button>
            </span>
          </div>
        </li>
      </ul>
    </section>

    <section class="tai-advice">
      <div class="tai-sub">问 AI 该画什么 <span class="dim tai-small">（只给建议，不替你画）</span></div>
      <textarea v-model="question" rows="2" data-advice-question placeholder="比如：中间该分几块？上面该放哪些？（可空）" />
      <div class="tai-row">
        <button class="btn tiny" data-act="topic-advice" :disabled="!!busy" @click="ask">问</button>
        <span v-if="busy === 'advice'" class="dim" data-waiting>learn 角色在想…{{ waited }}s</span>
      </div>
      <div v-for="(a, i) in advice" :key="a.at + i" class="tai-answer" :data-advice="i">
        <div class="dim tai-small">{{ a.question || '（没有具体问题）' }} · {{ a.model }} · {{ secs(a.ms) }}</div>
        <Markdown :text="a.text" />
      </div>
    </section>
  </aside>
</template>
