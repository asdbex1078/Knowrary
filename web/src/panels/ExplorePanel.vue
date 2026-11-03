<script setup>
/**
 * 探索台（2026-10-30）：学习这半（线B「保鲜」）单独的家。
 *
 * **一个项目 = 一场远征**。项目本来就是「目标 → 分阶段知识点清单」的整理轴（线A：幽灵/壳/建好），
 * 这里只呈现它的**掌握轴**（线B：会不会）——数据全是服务端现算的学/考双态，这里不存一个字，
 * 只换一套说法：把「错题/到期」叫心魔、把「记得」叫点亮、把「还没建」叫新边疆。
 *
 *   学灰/红（图里没有 / 只有壳） → 🌊 新边疆
 *   学绿 + 考红/黄（错过 / 模糊到期）→ 👹 心魔
 *   学绿 + 考灰（建好没考过）       → ◻️ 待点亮
 *   学绿 + 考绿（记得没到期）       → ✨ 点亮
 *
 * 「模拟面试」= 对这场远征里已建出来的点出题（复用 /api/quiz 的「面试」口径），
 * 答错的自动进错题本 → 下次来就是心魔。整条闭环全是现成的。
 */
import { computed, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import { todayISO } from '../today.js'

const props = defineProps({
  doc: { type: Object, default: null },             // plansDoc：{ revision, projects: {id: project} }
  progress: { type: Object, default: () => ({}) },  // { pid: { lists:[...], all:{ states, points, ... } } }
  project: { type: String, default: '' },           // 当前远征（顶栏选的项目）
  names: { type: Object, default: () => ({}) },      // id → 名字（索引里的）
  busy: { type: Boolean, default: false },           // 正在出题
  streak: { type: Object, default: () => ({ streak: 0, week: 0 }) },   // 连续探索天数
  proposal: { type: Object, default: null },         // 模型起草的大纲（纯提议，采纳才落盘）
  proposing: { type: Boolean, default: false },      // 正在起草
  gameSkin: { type: Boolean, default: true },        // 皮肤：true=远征/心魔/点亮，false=平实措辞
})

const DONE_AT = 0.8     // 达成阈值的兜底默认；每个远征在 project.done_at 里各自定（0.5–1.0）
const emit = defineEmits(['quiz', 'goto', 'build', 'refresh', 'plans', 'propose', 'adopt', 'map', 'threshold', 'close'])

const pr = computed(() => props.doc?.projects?.[props.project] || null)
const all = computed(() => props.progress?.[props.project]?.all || null)

// 幽灵点不在索引里，名字 / why / 层 只在清单里——先认清单，再退回索引
const planPoints = computed(() => {
  const out = {}
  for (const ls of pr.value?.lists || []) {
    for (const st of ls.stages || []) for (const p of st.points || []) out[p.id] = p
  }
  return out
})
const nameOf = (id) => {
  const p = planPoints.value[id]
  return (p?.name && p.name !== id) ? p.name : (props.names[id] || id)
}

/** 学/考双态 → 探索态。 */
function stateOf(s) {
  if (!s || s.study !== '绿') return 'frontier'       // 图里没有 / 只有壳
  if (s.exam === '红' || s.exam === '黄') return 'demon'
  if (s.exam === '绿') return 'lit'
  return 'untested'                                   // 建好了，还没考过
}

const buckets = computed(() => {
  const out = { demon: [], untested: [], frontier: [], lit: [] }
  const states = all.value?.states || {}
  for (const id of Object.keys(states)) out[stateOf(states[id])].push(id)
  return out
})
const total = computed(() => Object.keys(all.value?.states || {}).length)
const litPct = computed(() => (total.value ? Math.round((buckets.value.lit.length / total.value) * 100) : 0))
// 已建出来的点（能出题的）：心魔 + 待点亮 + 点亮
const builtIds = computed(() => [...buckets.value.demon, ...buckets.value.untested, ...buckets.value.lit])

// 四个探索态的出题口径固定不变（行为），emoji / 措辞随皮肤换。
const ZONE_STYLE = { demon: '复习', untested: '面试', frontier: null, lit: null }
const ZONE_ORDER = ['demon', 'untested', 'frontier', 'lit']

// 两套皮肤：game（远征/心魔/点亮）与 plain（项目/难点/已学）。只换说法，不换结构与行为。
const SKIN = {
  game: {
    zones: {
      demon: { emoji: '👹', title: '心魔', act: '降服它们', hint: '学过、正在被遗忘吞噬——出题唤回来' },
      untested: { emoji: '◻️', title: '待点亮', act: '去验一验', hint: '建好了还没考过——考一次就点亮' },
      frontier: { emoji: '🌊', title: '新边疆', act: null, hint: '计划里有、还没建出来——去开拓' },
      lit: { emoji: '✨', title: '已点亮', act: null, hint: '记得、没到期' },
    },
    clue: {
      demon: { emoji: '👹', verb: '降服', act: 'quiz', style: '复习' },
      untested: { emoji: '◻️', verb: '去验', act: 'quiz', style: '面试' },
      frontier: { emoji: '🌊', verb: '开拓', act: 'build', style: null },
    },
    doneHead: '🎉 这片海已点亮', doneWord: '远征达成', doneTail: '剩下的都是锦上添花了。',
    litLabel: '已点亮', todayHead: '🧭 今日探索', todayHint: '今天挑这几件',
    seaBtn: '在图上看这片海', threshLabel: '达成线', weekWord: '探索',
    interview: '来一场模拟面试', interviewHint: '按「面试」口径全方位出题 · 答错的会变心魔',
    expand: '让模型补充大纲（拓展边疆）',
  },
  plain: {
    zones: {
      demon: { emoji: '⚠️', title: '易忘', act: '去复习', hint: '学过、正在遗忘——出题唤回来' },
      untested: { emoji: '◻️', title: '待检验', act: '去测一测', hint: '建好了还没考过——测一次就确认' },
      frontier: { emoji: '○', title: '未学', act: null, hint: '计划里有、还没建出来——去学' },
      lit: { emoji: '●', title: '已掌握', act: null, hint: '记得、没到期' },
    },
    clue: {
      demon: { emoji: '⚠️', verb: '复习', act: 'quiz', style: '复习' },
      untested: { emoji: '◻️', verb: '检验', act: 'quiz', style: '面试' },
      frontier: { emoji: '○', verb: '去学', act: 'build', style: null },
    },
    doneHead: '✓ 已掌握', doneWord: '目标达成', doneTail: '剩下的可以慢慢补。',
    litLabel: '已掌握', todayHead: '今日安排', todayHint: '今天做这几件',
    seaBtn: '在图上看这些点', threshLabel: '达成线', weekWord: '学习',
    interview: '来一次自测', interviewHint: '全方位出题测一遍 · 答错的进复习清单',
    expand: '让模型补充大纲',
  },
}
const sk = computed(() => (props.gameSkin ? SKIN.game : SKIN.plain))
const ZONES = computed(() => ZONE_ORDER.map((key) => ({ key, style: ZONE_STYLE[key], ...sk.value.zones[key] })))

function quizZone(z) {
  const ids = buckets.value[z.key]
  if (ids.length && !props.busy) emit('quiz', { ids, style: z.style })
}

// ---- 远征大纲：写目标 → 模型起草 → 勾选（你改）→ 采纳成要点亮的地图 ----
const goal = ref('')
watch(() => props.project, () => { goal.value = pr.value?.name || '' }, { immediate: true })

/** 起草：空画布从目标拟一份，有点了则「拓展」（把已有点喂给模型别重复）。 */
function draft() {
  const g = goal.value.trim()
  if (!g || props.proposing) return
  emit('propose', { goal: g, plan_name: pr.value?.name || '', kind: '学习', level: pr.value?.level || null,
                    known_points: Object.keys(all.value?.states || {}) })
}

const chosen = ref(new Set())
// 只排除「本清单已经有的」(duplicates)；「图谱里已有」(existing) 照样收——已建的点正该上地图（变待点亮 / 点亮）
const skip = computed(() => new Set(props.proposal?.duplicates || []))
watch(() => props.proposal, (p) => {
  chosen.value = new Set((p?.stages || []).flatMap((s) => (s.points || []).map((x) => x.id))
    .filter((id) => !skip.value.has(id)))
})
function toggle(id) {
  const n = new Set(chosen.value)
  n.has(id) ? n.delete(id) : n.add(id)
  chosen.value = n
}
function adopt() {
  if (chosen.value.size) emit('adopt', { ids: [...chosen.value] })
}
function interview() {
  if (builtIds.value.length && !props.busy) emit('quiz', { ids: builtIds.value, style: '面试' })
}
function onPoint(key, id) {
  // 新边疆：点一下去建（buildPoint 要的是清单里那个点对象，带上 project 回查 field/why/层）
  if (key === 'frontier') emit('build', { ...(planPoints.value[id] || {}), id, project: props.project })
  else emit('goto', id)
}

// ---- 达成仪式 + 今日探索线索卡 ----
const threshold = computed(() => pr.value?.done_at ?? DONE_AT)          // 每个远征各自定
const thresholdPct = computed(() => Math.round(threshold.value * 100))
const done = computed(() => total.value > 0 && buckets.value.lit.length / total.value >= threshold.value)
function onThreshold(e) { emit('threshold', Number(e.target.value)) }

// 庆祝动画：只在「本次会话里当场跨过达成线」那一刻放一次。
// 两道闸：computed 初值不触发 watch（打开就已达成的不放）；切远征先解除武装
// （切到一个早已达成的远征，done 也会 false→true，但那不是"刚跨过"，不该撒花）。
const celebrating = ref(false)
let celebrateTimer = null
let armed = false
watch(() => props.project, () => { armed = false })
watch(done, (now, was) => {
  const fire = armed && now && !was
  armed = true
  if (!fire) return
  celebrating.value = true
  clearTimeout(celebrateTimer)
  celebrateTimer = setTimeout(() => { celebrating.value = false }, 2200)
})
const CONFETTI_COLORS = ['#ffd166', '#06d6a0', '#ef476f', '#118ab2', '#f78c6b', '#9b8cff']
function confettiStyle(n) {
  const rnd = (salt) => (((n * 2654435761 + salt) >>> 0) % 1000) / 1000
  return {
    left: `${(rnd(1) * 100).toFixed(1)}%`,
    background: CONFETTI_COLORS[n % CONFETTI_COLORS.length],
    animationDelay: `${(rnd(2) * 0.35).toFixed(2)}s`,
    animationDuration: `${(1.3 + rnd(3) * 0.5).toFixed(2)}s`,
  }
}

/** 每天一份、当天稳定的线索（按日期+id 定序，所以同一天不乱跳，换天才轮换）。 */
function rank(id, seed) {
  let h = seed >>> 0
  for (let i = 0; i < id.length; i += 1) h = (Math.imul(h, 31) + id.charCodeAt(i)) >>> 0
  return h
}
function pickDaily(ids, n, seed) {
  return [...ids].sort((a, b) => rank(a, seed) - rank(b, seed)).slice(0, n)
}
const todayClues = computed(() => {
  const CLUE = sk.value.clue
  let seed = 0
  for (const c of todayISO()) seed = (Math.imul(seed, 31) + c.charCodeAt(0)) >>> 0
  const take = [...pickDaily(buckets.value.demon, 2, seed).map((id) => ({ id, kind: 'demon' })),
                ...pickDaily(buckets.value.untested, 1, seed ^ 0x1f).map((id) => ({ id, kind: 'untested' })),
                ...pickDaily(buckets.value.frontier, 2, seed ^ 0x3d).map((id) => ({ id, kind: 'frontier' }))]
  return take.slice(0, 5).map((c) => ({ ...c, ...CLUE[c.kind], name: nameOf(c.id) }))
})
function doClue(c) {
  if (c.act === 'build') onPoint('frontier', c.id)
  else if (!props.busy) emit('quiz', { ids: [c.id], style: c.style })
}
</script>

<template>
  <Drawer side="left" :title="pr ? (pr.name || project) : (gameSkin ? '探索台' : '学习进度')" icon="target" storage-key="explore"
          :default-width="420" :min="320" :max="900" expandable @close="emit('close')">
    <template #head-actions>
      <button class="icon-btn ghost tiny" title="刷新" @click="emit('refresh')"><Icon name="refresh" :size="14" /></button>
    </template>
    <template #default>
    <!-- 没选项目 -->
    <div v-if="!pr" class="exp-empty">
      <p>先在顶栏选一个<b>目标（项目）</b>，这里就是它的探索台。</p>
      <button class="btn" @click="emit('plans')">去选 / 新建一个目标</button>
    </div>

    <!-- 模型起草的大纲：勾你要的，采纳成要点亮的地图（「你改」就是这一步的勾选） -->
    <section v-else-if="proposal" class="exp-draft">
      <h4>模型起草的大纲 <span class="dim">勾你要的，采纳成地图</span></h4>
      <div v-for="st in proposal.stages" :key="st.name" class="exp-draft-stage">
        <div class="exp-draft-stage-name">{{ st.name }}</div>
        <div class="exp-chips">
          <button v-for="p in st.points" :key="p.id" class="exp-chip draft"
                  :class="{ on: chosen.has(p.id), had: skip.has(p.id) }"
                  :title="(p.why || '') + (skip.has(p.id) ? '（已经有了）' : '')" @click="toggle(p.id)">
            <span v-if="chosen.has(p.id)">✓ </span>{{ p.name || p.id }}
          </button>
        </div>
      </div>
      <div class="exp-draft-acts">
        <button class="btn" :disabled="!chosen.size || busy" @click="adopt">采纳 {{ chosen.size }} 个为地图</button>
        <button class="btn ghost" @click="emit('propose', null)">取消</button>
      </div>
    </section>

    <!-- 空远征：写目标，让模型起草大纲 -->
    <div v-else-if="!total" class="exp-new">
      <p>{{ gameSkin ? '这场远征还没有地图。' : '这个目标还没拆成清单。' }}写下目标，让模型起草一份<b>能力大纲</b>——采纳后就是你要逐个{{ gameSkin ? '点亮' : '掌握' }}的清单。</p>
      <textarea v-model="goal" class="exp-goal" rows="2" placeholder="比如：Java 架构师 / Agent 高级开发" />
      <button class="btn" :disabled="!goal.trim() || proposing" @click="draft">
        {{ proposing ? '起草中…' : '让模型起草大纲' }}
      </button>
      <button class="btn ghost" @click="emit('plans')">或者手动去拆清单</button>
    </div>

    <template v-else>
      <!-- 达成仪式：点亮到阈值 -->
      <div v-if="done" class="exp-done">
        <span v-if="celebrating" class="exp-confetti" aria-hidden="true">
          <i v-for="n in 24" :key="n" :style="confettiStyle(n)" />
        </span>
        {{ sk.doneHead }} {{ litPct }}%——<b>{{ sk.doneWord }}</b>。<span class="dim">{{ sk.doneTail }}</span>
      </div>

      <!-- 连续天数（宽容：今天没动不算断，还给本周活跃天数兜底） -->
      <div class="exp-streak">
        <span class="s-fire" :class="{ cold: !streak.streak }">🔥 连续 <b>{{ streak.streak }}</b> 天</span>
        <span class="dim">· 本周{{ sk.weekWord }} {{ streak.week }} 天</span>
      </div>

      <!-- 今日探索：每天一份、当天稳定的小行动 -->
      <section v-if="todayClues.length" class="exp-today">
        <div class="exp-today-head">{{ sk.todayHead }} <span class="dim">{{ sk.todayHint }}</span></div>
        <button v-for="c in todayClues" :key="c.id" class="exp-clue" :class="c.kind"
                :disabled="busy && c.act === 'quiz'" @click="doClue(c)">
          <span class="exp-clue-emoji">{{ c.emoji }}</span>
          <span class="exp-clue-name">{{ c.name }}</span>
          <span class="exp-clue-verb">{{ c.verb }}</span>
        </button>
      </section>

      <!-- 进度：点亮了多少 -->
      <section class="exp-progress">
        <div class="exp-pct"><b>{{ litPct }}%</b><span class="dim">{{ sk.litLabel }}</span></div>
        <div class="exp-bar"><i :style="{ width: litPct + '%' }" /><u class="exp-bar-line" :style="{ left: thresholdPct + '%' }" :title="`${sk.threshLabel} ${thresholdPct}%`" /></div>
        <label class="exp-thresh">
          <span class="dim">{{ sk.threshLabel }} <b>{{ thresholdPct }}%</b></span>
          <input type="range" min="50" max="100" step="5" :value="thresholdPct" @change="onThreshold" />
        </label>
        <div class="exp-counts">
          <span class="c lit">✨ {{ buckets.lit.length }}</span>
          <span class="c demon">👹 {{ buckets.demon.length }}</span>
          <span class="c untested">◻️ {{ buckets.untested.length }}</span>
          <span class="c frontier">🌊 {{ buckets.frontier.length }}</span>
          <span class="dim">· 共 {{ total }}</span>
        </div>
        <button class="exp-sea btn ghost tiny" title="切到项目图、开迷雾，在真实图谱上看" @click="emit('map')">
          <Icon name="map" :size="13" />{{ sk.seaBtn }}
        </button>
      </section>

      <button class="exp-interview" :disabled="!builtIds.length || busy" @click="interview">
        <Icon name="target" :size="15" />{{ busy ? '出题中…' : sk.interview }}
        <span class="dim">{{ sk.interviewHint }}</span>
      </button>

      <!-- 四个探索区 -->
      <section v-for="z in ZONES" :key="z.key" v-show="buckets[z.key].length" class="exp-zone" :class="z.key">
        <div class="exp-zone-head">
          <span class="exp-zone-title">{{ z.emoji }} {{ z.title }} <span class="dim">{{ buckets[z.key].length }}</span></span>
          <button v-if="z.act && buckets[z.key].length" class="btn tiny" :disabled="busy" @click="quizZone(z)">{{ z.act }}</button>
        </div>
        <p class="exp-zone-hint dim">{{ z.hint }}</p>
        <div class="exp-chips">
          <button v-for="id in buckets[z.key]" :key="id" class="exp-chip" :class="z.key"
                  :title="z.key === 'frontier' ? '去把它建出来' : '打开这个点'" @click="onPoint(z.key, id)">
            {{ nameOf(id) }}
          </button>
        </div>
      </section>

      <!-- 拓展边疆：让模型按目标补大纲（已有的点会喂给它，不重复） -->
      <button class="exp-expand btn ghost" :disabled="proposing" @click="draft">
        <Icon name="plus" :size="13" />{{ proposing ? '起草中…' : sk.expand }}
      </button>
    </template>
    </template>
  </Drawer>
</template>
