<script setup>
/**
 * 答题：写下来 → 对答案 → 自评三档 → 整轮比对诊断。
 *
 * 为什么一定要先写：心里"想一遍"太容易自欺——只记得几个名词也会觉得自己会了。
 * 写出来才暴露你到底能复现多少，而且有了这段文字，"我缺在哪"才比对得出来。
 *
 * 判分仍然由我自己点，模型只在最后整轮给一次诊断，而且**只用来往下调**：
 * 判严了最多多复习一次，判宽了会让一个其实没掌握的点从此不再出现。
 *
 * 诊断放在最后一次性做，不逐题调——逐题会让每道题都卡几秒，答题节奏全毁。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  names: { type: Object, default: () => ({}) },   // node_id → 展示名
  // { items: [{ n, missed, wrong, suggested_grade, comment, beyond, next_gap,
  //             full_answer, beyond_vault, ref_answer }] }
  diagnosis: { type: Object, default: null },
  busy: { type: Boolean, default: false },        // 诊断中 / 交卷中
})
const emit = defineEmits(['diagnose', 'submit', 'goto', 'close'])

const GRADES = [
  { key: '忘了', icon: 'x', tip: '完全想不起来 → 明天再考' },
  { key: '模糊', icon: 'minus', tip: '想起个大概 → 间隔不变' },
  { key: '记得', icon: 'check', tip: '答上来了 → 拉长间隔' },
]
const RANK = { 忘了: 0, 模糊: 1, 记得: 2 }

const at = ref(0)
const revealed = ref(false)
const mine = ref('')             // 当前这题我写的答案
const rows = ref([])             // [{ question, my_answer, grade }]
const phase = ref('answering')   // answering | summary
const boxEl = ref(null)

const current = computed(() => props.questions[at.value] || null)
const wrongCount = computed(() => rows.value.filter((r) => r.grade === '忘了').length)
const hintOf = (n) => (props.diagnosis?.items || []).find((x) => x.n === n) || null

/** 模型判得比我严时才提示下调；判得比我松一律不理会。 */
const downgrade = (i) => {
  const d = hintOf(i + 1)
  if (!d) return null
  return RANK[d.suggested_grade] < RANK[rows.value[i].grade] ? d.suggested_grade : null
}

function focusBox() {
  nextTick(() => boxEl.value?.focus())
}

function reveal() {
  if (revealed.value) return
  revealed.value = true
}

function grade(key) {
  if (!current.value) return
  rows.value.push({ question: current.value, my_answer: mine.value.trim(), grade: key })
  mine.value = ''
  revealed.value = false
  at.value += 1
  if (at.value >= props.questions.length) {
    phase.value = 'summary'
    emit('diagnose', rows.value.map((r) => ({ ...r })))
  } else {
    focusBox()
  }
}

/** 采纳模型的下调建议，并把它的诊断一起带进留档。 */
function adopt(i) {
  const lower = downgrade(i)
  if (!lower) return
  rows.value[i] = { ...rows.value[i], grade: lower }
}

function finish() {
  emit('submit', rows.value.map((r, i) => {
    const d = hintOf(i + 1)
    return { question: r.question, grade: r.grade, my_answer: r.my_answer,
             missed: d?.missed || [], wrong_points: d?.wrong || [] }
  }))
}

function onKey(ev) {
  if (ev.key === 'Escape') { ev.stopPropagation(); emit('close'); return }
  if (phase.value !== 'answering' || !current.value) return
  if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') { ev.preventDefault(); reveal(); return }
  if (!revealed.value) return                       // 写答案时不抢数字键
  const hit = { 1: '忘了', 2: '模糊', 3: '记得' }[ev.key]
  if (hit && ev.target.tagName !== 'TEXTAREA') { ev.preventDefault(); grade(hit) }
}

onMounted(() => { document.addEventListener('keydown', onKey, true); focusBox() })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog quiz-dialog">
    <header>
      <Icon name="rotate" :size="15" />
      <span class="who">测验</span>
      <span class="dim">
        {{ phase === 'summary' ? `${questions.length} 题 · 错 ${wrongCount} 题`
                               : `第 ${at + 1} / ${questions.length} 题` }}
      </span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <!-- 答题 -->
      <template v-if="phase === 'answering' && current">
        <div class="quiz-type">{{ current.type }}</div>
        <p class="quiz-stem">{{ current.stem }}</p>

        <div class="quiz-points">
          <span class="dim" style="font-size: 11.5px">考点</span>
          <button v-for="p in current.points" :key="p" class="chip link" @click="emit('goto', p)">
            {{ names[p] || p }}
          </button>
        </div>

        <textarea ref="boxEl" v-model="mine" class="quiz-input" :readonly="revealed" rows="5"
                  :placeholder="revealed ? '' : '把你想到的写下来，再对答案。写不出来就空着——空着也是一种答案。'" />

        <template v-if="!revealed">
          <p v-if="current.hint" class="dim" style="font-size: 12px; line-height: 1.6">
            提示：{{ current.hint }}
          </p>
          <footer>
            <span class="dim">⌘↵ 对答案</span>
            <button class="btn primary" @click="reveal">
              <Icon name="eye" :size="14" />对答案
            </button>
          </footer>
        </template>

        <template v-else>
          <div class="quiz-label">标准答案</div>
          <div class="card quiz-answer">{{ current.ref_answer || '（这题还没有标准答案，批改时会照你的笔记补一份）' }}</div>
          <p class="dim" style="font-size: 11.5px; margin: 10px 0 6px">对完了，刚才答得怎么样？</p>
          <div class="quiz-grades">
            <button v-for="(g, i) in GRADES" :key="g.key" class="btn" :class="{ primary: g.key === '记得' }"
                    :title="`${g.tip}（按 ${i + 1}）`" @click="grade(g.key)">
              <Icon :name="g.icon" :size="14" />{{ g.key }}
            </button>
          </div>
        </template>
      </template>

      <!-- 整轮诊断 -->
      <template v-else-if="phase === 'summary'">
        <p v-if="busy && !diagnosis" class="dim" style="font-size: 12.5px; line-height: 1.7">
          正在逐题比对你写的和标准答案，看漏在哪、有没有记反的，要几秒。
        </p>
        <p v-else-if="!diagnosis" class="dim" style="font-size: 12.5px">
          这轮没拿到诊断，下面按你的自评记。
        </p>

        <ol class="quiz-summary">
          <li v-for="(r, i) in rows" :key="i">
            <div class="sum-head">
              <span class="quiz-type">{{ r.question.type }}</span>
              <span class="chip" :class="{ 'warn-text': r.grade === '忘了' }">{{ r.grade }}</span>
              <span v-for="p in r.question.points" :key="p" class="chip link" @click="emit('goto', p)">
                {{ names[p] || p }}
              </span>
            </div>
            <p class="sum-stem">{{ r.question.stem }}</p>

            <template v-if="hintOf(i + 1)">
              <p v-if="hintOf(i + 1).comment" class="dim sum-comment">{{ hintOf(i + 1).comment }}</p>
              <div v-if="hintOf(i + 1).missed.length" class="sum-line">
                <span class="sum-tag">漏掉</span>
                <span v-for="m in hintOf(i + 1).missed" :key="m" class="chip">{{ m }}</span>
              </div>
              <div v-if="hintOf(i + 1).wrong.length" class="sum-line">
                <span class="sum-tag bad">记错</span>
                <span v-for="w in hintOf(i + 1).wrong" :key="w" class="chip warn-text">{{ w }}</span>
              </div>
              <div v-if="downgrade(i)" class="sum-line">
                <span class="dim" style="font-size: 11.5px">
                  比对下来更像「{{ downgrade(i) }}」
                </span>
                <button class="btn subtle tiny" @click="adopt(i)">下调为 {{ downgrade(i) }}</button>
              </div>

              <!-- 档位之间的信号：答超了该升档，没超也该知道下一档还差什么。
                   它不进复习调度——调度只认上面那三档。 -->
              <div v-if="hintOf(i + 1).beyond || hintOf(i + 1).next_gap" class="sum-line">
                <span v-if="hintOf(i + 1).beyond" class="sum-tag up">答超了</span>
                <span v-if="hintOf(i + 1).next_gap" class="dim" style="font-size: 11.5px">
                  {{ hintOf(i + 1).next_gap }}
                </span>
              </div>

              <!-- 这题本来没有标准答案（聊天随口问的题只有题干和考点），刚照笔记正文补了一份。
                   来源是我自己的笔记，所以它会进题库当以后的判分依据，和下面那份模型写的不是一回事。 -->
              <template v-if="hintOf(i + 1).ref_answer">
                <div class="sum-line"><span class="sum-tag">照你的笔记补出的标准答案</span></div>
                <div class="card quiz-answer">{{ hintOf(i + 1).ref_answer }}</div>
              </template>

              <details v-if="hintOf(i + 1).full_answer" class="sum-full">
                <summary>完整答案</summary>
                <div class="card quiz-answer">{{ hintOf(i + 1).full_answer }}</div>
                <div v-if="hintOf(i + 1).beyond_vault.length" class="sum-line">
                  <span class="sum-tag new">笔记里没有</span>
                  <span v-for="b in hintOf(i + 1).beyond_vault" :key="b" class="chip">{{ b }}</span>
                </div>
              </details>
            </template>
          </li>
        </ol>

        <p class="dim sum-note">
          「漏掉」多复习几次就补上了；<b>「记错」要回去看那张卡</b>——记成了别的东西，再考几遍只会焊得更牢。
          判分按这批点设定的档位来，<b>「答超了」不加分，是在提醒你这个点可以升档</b>。
          「笔记里没有」的那几条是模型补的，<b>信之前先自己核一遍</b>，核实了再补进节点。
        </p>

        <footer>
          <span class="dim">错的会被排进明天</span>
          <button class="btn primary" :disabled="busy" @click="finish">
            <Icon name="check" :size="14" />{{ busy ? '记录中…' : '交卷' }}
          </button>
        </footer>
      </template>
    </div>
  </div>
</template>
