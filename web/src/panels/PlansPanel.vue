<script setup>
/**
 * 学习计划：目标 → 要掌握的点。**计划是图谱的施工图，不是图谱的选集。**
 *
 * 所以这里的知识点**允许指向图里还不存在的节点**——你要学 Transformer 的时候
 * 这些节点一个都没有，计划就是用来把它们填出来的。没建的点标成「未建」，点一下就去建。
 *
 * 不做自动保存：计划是人手编排的文档，不是拖拽手势。边改边存只会把一半想完的东西写进去，
 * 还会和 base_revision 打架。改完点「保存」。
 */
import { computed, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  doc: { type: Object, default: null },        // { revision, plans: {id: plan} }
  progress: { type: Object, default: () => ({}) },
  schedules: { type: Object, default: () => ({}) },   // 时间账，服务端现算：装不装得下、每阶段排到哪天
  busy: { type: Boolean, default: false },
  proposal: { type: Object, default: null },   // AI 拆出来的清单，纯提议，人点了才进 draft
  proposing: { type: Boolean, default: false },
  fields: { type: Array, default: () => [] },   // 已有领域，建点时的落脚点从这里选
})
const emit = defineEmits(['save', 'goto', 'build', 'quiz', 'propose', 'refresh', 'close'])

// 可行性三档。判定在服务端做（除法而已，不问模型），这里只负责说人话。
const VERDICT = {
  充裕: { cls: 'm-mastered', tip: '按你填的投入，这个期限装得下' },
  紧: { cls: 'm-due', tip: '装得下但几乎没有余量，病一场就崩' },
  不可能: { cls: 'm-unbuilt', tip: '按你填的投入，这些点在期限内学不完' },
}
const LOADS = ['轻', '中', '重']       // 负荷三档，换算成小时在服务端 core.LOAD_HOURS

// 三种口径共用同一套数据与链路，差别只在措辞和拆解模板（F10.3b）。
// 「初始化一个领域的所有点」不是第二个功能，就是这里的第三种口径。
const KIND = {
  学习: { goal: '目标', ph: '例如：三个月吃透 Transformer 到 RLHF', act: '让 AI 拆一份要点',
          hint: '按依赖顺序拆：先学的在前', unit: '已建' },
  面试: { goal: '岗位要求', ph: '把 JD 原文贴进来，AI 会按「会怎么问」拆成考点',
          act: '让 AI 拆一份考点', hint: '按会怎么问拆：组名像面试轮次', unit: '已备' },
  领域: { goal: '领域范围', ph: '例如：大模型推理——从注意力到部署，我想知道这个领域有哪几块',
          act: '让 AI 铺一份领域地图', hint: '按覆盖度铺：分组是子领域，求不漏而不是求顺序',
          unit: '已覆盖' },
}
const kindOf = (k) => KIND[k] || KIND.学习

// 五档掌握度 → 样式与提示。前两档是"还没建出来"，后三档才轮得到复习说话。
const MASTERY = {
  未建: { cls: 'm-unbuilt', tip: '图里还没有这个节点，点右边去建' },
  只有壳: { cls: 'm-shell', tip: '节点在，但还是空壳，点进去写正文' },
  待复习: { cls: 'm-due', tip: '到复习时间了' },
  学过: { cls: 'm-learned', tip: '建过也复习过，还没到"已掌握"' },
  已掌握: { cls: 'm-mastered', tip: '间隔已经拉得很长' },
}

const draft = ref({})          // 本地可编辑副本
const pick = ref('')           // 当前选中的计划 id
const dirty = ref(false)
const adding = ref({})         // { [stageIndex]: { id, why } }
const dropped = ref(new Set()) // 提议里被我划掉的点

// 模型仍然可能把计划里已有的点再列一遍。同名的这里默认划掉（近义词拦不住，
// 那要靠 prompt 里「这份计划里已经有的点」那一节）。
watch(() => props.proposal, (p) => {
  dropped.value = new Set(p?.duplicates || [])
})

watch(() => props.doc, (d) => {
  draft.value = JSON.parse(JSON.stringify(d?.plans || {}))
  dirty.value = false
  if (!draft.value[pick.value]) pick.value = Object.keys(draft.value)[0] || ''
}, { immediate: true, deep: false })

const ids = computed(() => Object.keys(draft.value))
const plan = computed(() => draft.value[pick.value] || null)
const stat = computed(() => props.progress?.[pick.value] || null)
// 时间账跟着**已保存**的计划走：草稿还没提交，服务端算不到。改完存一次就刷新了。
const sched = computed(() => props.schedules?.[pick.value] || null)
const proposeReq = computed(() => ({ goal: plan.value?.goal, plan_name: plan.value?.name,
                                     kind: plan.value?.kind, coach: plan.value?.coach,
                                     target_date: plan.value?.target_date,
                                     weekly_hours: plan.value?.weekly_hours,
                                     known_points: (plan.value?.stages || []).flatMap(
                                       (st) => st.points.map((p) => (p.name && p.name !== p.id)
                                         ? `${p.id}（${p.name}）` : p.id)) }))
// 编辑中的点还没提交，服务端算不出掌握度，先当「未建」显示
const masteryOf = (id) => props.progress?.[pick.value]?.points?.[id] || '未建'

function touch() { dirty.value = true }

function newPlan() {
  let id = '新计划'
  for (let i = 2; draft.value[id]; i += 1) id = `新计划${i}`
  draft.value = { ...draft.value, [id]: { name: id, goal: '', kind: '学习', coach: '', field: '', daily_quota: 2,
                                          created: new Date().toISOString().slice(0, 10),
                                          target_date: null, weekly_hours: 7, stages: [] } }
  pick.value = id
  touch()
}

function dropPlan() {
  const rest = { ...draft.value }
  delete rest[pick.value]
  draft.value = rest
  pick.value = ids.value[0] || ''
  touch()
}

function addStage() {
  const n = plan.value.stages.length + 1
  plan.value.stages.push({ name: plan.value.kind === '领域' ? `子领域 ${n}` : `第 ${n} 阶段`,
                           deadline: null, points: [] })
  touch()
}

function addPoint(si) {
  const form = adding.value[si] || {}
  const id = (form.id || '').trim()
  if (!id) return
  if (plan.value.stages.some((s) => s.points.some((p) => p.id === id))) return
  plan.value.stages[si].points.push({ id, name: id, why: (form.why || '').trim(), load: '中' })
  adding.value = { ...adding.value, [si]: { id: '', why: '' } }
  touch()
}

/** 已经在计划里的点不重复加；被划掉的不要。空阶段直接不生成。 */
function adopt() {
  const mine = new Set(plan.value.stages.flatMap((s) => s.points.map((p) => p.id)))
  for (const st of props.proposal?.stages || []) {
    const points = st.points.filter((p) => !dropped.value.has(p.id) && !mine.has(p.id))
    if (!points.length) continue
    points.forEach((p) => mine.add(p.id))
    const hit = plan.value.stages.find((x) => x.name === st.name)
    if (hit) { hit.points.push(...points); hit.deadline = hit.deadline || st.deadline || null }
    else plan.value.stages.push({ name: st.name, deadline: st.deadline || null, points: [...points] })
  }
  if (!plan.value.field && props.proposal?.suggested_field) {
    plan.value.field = props.proposal.suggested_field
  }
  dropped.value = new Set()
  emit('propose', null)          // 收起提议区
  touch()
}

/** 把服务端排好的建议截止日填进各阶段。按阶段名对齐，对不上的（改过名）跳过。 */
function applyDeadlines() {
  for (const row of sched.value?.stages || []) {
    const hit = plan.value.stages.find((s) => s.name === row.name)
    if (hit && row.suggested_deadline) hit.deadline = row.suggested_deadline
  }
  touch()
}

function toggleDrop(id) {
  const next = new Set(dropped.value)
  next.has(id) ? next.delete(id) : next.add(id)
  dropped.value = next
}

function dropPoint(si, pi) {
  plan.value.stages[si].points.splice(pi, 1)
  touch()
}
</script>

<template>
  <Drawer side="left" title="学习计划" icon="checklist" storage-key="plans" :default-width="360"
          @close="emit('close')">
    <template #head-actions>
      <button class="icon-btn ghost tiny" title="新建一个计划" @click="newPlan">
        <Icon name="plus" :size="14" />
      </button>
      <button class="btn tiny" :class="{ primary: dirty }" :disabled="busy || !dirty"
              :title="dirty ? '把计划写进 plans.json（不碰 md、不碰画布）' : '没有改动'"
              @click="emit('save', draft)">
        <Icon name="save" :size="13" />{{ busy ? '保存中…' : dirty ? '保存' : '已保存' }}
      </button>
    </template>

    <template #default>
      <div v-if="!doc" class="dim">加载中…</div>

      <div v-else-if="!ids.length" class="empty">
        <Icon name="checklist" :size="30" :width="1.3" />
        <span class="t">还没有学习计划</span>
        <span class="s">先定个目标，比如「吃透 Transformer」，再把要掌握的点列出来。<br>
          这些点**不需要图里已经有**——计划就是用来把它们填出来的。</span>
        <button class="btn primary" style="margin-top: 12px" @click="newPlan">
          <Icon name="plus" :size="14" />新建计划
        </button>
      </div>

      <template v-else-if="plan">
        <select v-if="ids.length > 1" v-model="pick" class="plan-switch">
          <option v-for="id in ids" :key="id" :value="id">{{ draft[id].name || id }}</option>
        </select>

        <label class="fld"><span class="lb">计划名称</span>
          <input v-model="plan.name" @input="touch" /></label>
        <div class="two">
          <label class="fld"><span class="lb">类型</span>
            <select v-model="plan.kind" :title="kindOf(plan.kind).hint" @change="touch">
              <option value="学习">学习</option>
              <option value="面试">面试</option>
              <option value="领域">领域</option>
            </select>
          </label>
          <label class="fld"><span class="lb">教练方向<i>可选</i></span>
            <input v-model="plan.coach" :placeholder="plan.kind === '面试' ? 'Java 后端开发' : '大模型 / 运维…'"
                   @input="touch" />
          </label>
        </div>

        <label class="fld"><span class="lb">{{ kindOf(plan.kind).goal }}</span>
          <textarea v-if="plan.kind === '面试'" v-model="plan.goal" class="quiz-input" rows="4"
                    :placeholder="kindOf(plan.kind).ph" @input="touch" />
          <input v-else v-model="plan.goal" :placeholder="kindOf(plan.kind).ph" @input="touch" />
          <span class="hint">{{ kindOf(plan.kind).hint }}</span>
        </label>
        <div class="two">
          <label class="fld"><span class="lb">{{ plan.kind === '面试' ? '面试日期' : '目标日期' }}<i>可选</i></span>
            <input v-model="plan.target_date" placeholder="2026-12-15" @input="touch" /></label>
          <label class="fld"><span class="lb">每周投入<i>小时</i></span>
            <input v-model.number="plan.weekly_hours" type="number" min="1" max="80" @input="touch" /></label>
        </div>
        <p class="dim" style="font-size: 11px; margin: -4px 0 8px; line-height: 1.6">
          这两个值是排时间表的依据：拆解时会一起发给模型，回来的每个点带一档负荷，
          阶段截止日按负荷摊在这段时间里。
        </p>
        <button class="btn" :disabled="proposing || !plan.goal.trim()" style="width: 100%; justify-content: center"
                :title="plan.goal.trim() ? '让 AI 按这个目标拆出要掌握的点' : '先写一句目标'"
                @click="emit('propose', proposeReq)">
          <Icon name="network" :size="14" />{{ proposing ? '拆解中…' : kindOf(plan.kind).act }}
        </button>
        <p v-if="proposing" class="dim" style="font-size: 11.5px; margin-top: 8px; line-height: 1.6">
          正在{{ kindOf(plan.kind).act.replace('让 AI ', '') }}，会尽量复用图里已有的节点、避开这份计划里已经有的点，要几秒。
        </p>

        <!-- AI 提议：纯展示，划掉不要的，点「采纳」才进计划，再点「保存」才落盘 -->
        <div v-if="proposal" class="proposal">
          <div class="section-head">
            <Icon name="network" :size="13" />AI 拆出的{{ plan.kind === '面试' ? '考点' : plan.kind === '领域' ? '地图' : '要点' }}
            <span class="count">{{ proposal.stages.reduce((n, s) => n + s.points.length, 0) }}</span>
            <button class="icon-btn ghost tiny" title="不要这份" @click="emit('propose', null); dropped = new Set()">
              <Icon name="x" :size="13" />
            </button>
          </div>
          <div v-if="proposal.schedule?.total_hours" class="sched-head" style="margin: 6px 0">
            <span v-if="proposal.schedule.verdict" class="chip" :class="VERDICT[proposal.schedule.verdict]?.cls"
                  :title="VERDICT[proposal.schedule.verdict]?.tip">{{ proposal.schedule.verdict }}</span>
            <span>这份约 {{ proposal.schedule.total_hours }} 小时<template
              v-if="proposal.schedule.days_left !== null">，你还剩
              {{ proposal.schedule.capacity_hours }} 小时（{{ proposal.schedule.days_left }} 天）</template>；
              按现在的投入要学到 <b>{{ proposal.schedule.suggested_target_date }}</b></span>
          </div>
          <p v-if="proposal.duplicates?.length" class="dim" style="font-size: 11.5px; line-height: 1.6">
            其中 <b>{{ proposal.duplicates.length }}</b> 个这份计划里已经有了，已经默认划掉——
            想重新表述就点回来。
          </p>
          <p v-if="proposal.notes" class="dim" style="font-size: 11.5px; line-height: 1.6">{{ proposal.notes }}</p>
          <p v-for="w in proposal.warnings" :key="w" class="warn-text" style="font-size: 11px">{{ w }}</p>

          <div v-for="(st, i) in proposal.stages" :key="i" class="prop-stage">
            <div class="prop-stage-name">{{ st.name }}
              <span v-if="st.deadline" class="dim" style="font-weight: 400">· 排到 {{ st.deadline }}</span>
            </div>
            <ul>
              <li v-for="p in st.points" :key="p.id" class="edge-row point"
                  :class="{ struck: dropped.has(p.id) }">
                <span class="chip" :class="proposal.duplicates?.includes(p.id) ? 'm-shell'
                                            : proposal.existing.includes(p.id) ? 'm-learned' : 'm-unbuilt'"
                      :title="proposal.duplicates?.includes(p.id) ? '这份计划里已经有了，默认不再加一遍' : ''">
                  {{ proposal.duplicates?.includes(p.id) ? '计划里有'
                     : proposal.existing.includes(p.id) ? '图里有' : '要新建' }}
                </span>
                <span class="to">{{ p.name || p.id }}</span>
                <span class="load" :title="`学习负荷：${p.load}`">{{ p.load }}</span>
                <span v-if="p.why" class="yr why">{{ p.why }}</span>
                <button class="icon-btn ghost tiny" :title="dropped.has(p.id) ? '加回来' : '不要这个点'"
                        @click="toggleDrop(p.id)">
                  <Icon :name="dropped.has(p.id) ? 'undo' : 'x'" :size="13" />
                </button>
              </li>
            </ul>
          </div>

          <div v-if="proposal.dropped?.length" class="prop-stage">
            <div class="prop-stage-name">这次砍掉的（{{ proposal.dropped.length }}）</div>
            <ul>
              <li v-for="p in proposal.dropped" :key="p.id" class="edge-row point struck">
                <span class="to">{{ p.name || p.id }}</span>
                <span v-if="p.why" class="yr why">{{ p.why }}</span>
              </li>
            </ul>
            <p class="dim" style="font-size: 11px; line-height: 1.6">
              速学版只留绕不开的点。这些不会进计划——记着自己跳过了什么，别当成学全了。
            </p>
          </div>

          <button class="btn primary" style="width: 100%; justify-content: center; margin-top: 10px"
                  @click="adopt">
            <Icon name="check" :size="14" />采纳进计划
          </button>
        </div>
        <label class="fld">
          <span class="lb">领域</span>
          <input v-model="plan.field" list="kg-plan-fields" placeholder="例如：深度学习（决定知识点建到哪个目录）"
                 @input="touch" />
          <datalist id="kg-plan-fields"><option v-for="f in fields" :key="f" :value="f" /></datalist>
          <span class="hint">这份计划里「未建」的点，会建到 <code>nodes/{{ plan.field || '…' }}/</code> 下</span>
        </label>

        <div class="two">
          <label class="fld"><span class="lb">每天几个点</span>
            <input v-model.number="plan.daily_quota" type="number" min="1" max="20" @input="touch" /></label>
          <label v-if="sched?.suggested_quota" class="fld"><span class="lb">建议每天</span>
            <button class="btn subtle" style="justify-content: center"
                    :disabled="plan.daily_quota === sched.suggested_quota"
                    :title="`按剩余 ${sched.remaining_points} 个点、还剩 ${sched.days_left} 天算`"
                    @click="plan.daily_quota = sched.suggested_quota; touch()">
              {{ sched.suggested_quota }} 个 · 采用
            </button>
          </label>
        </div>

        <!-- 时间账：装不装得下。判定在服务端算（除法而已，不问模型） -->
        <div v-if="sched && sched.total_hours" class="sched" :class="VERDICT[sched.verdict]?.cls">
          <div class="sched-head">
            <span v-if="sched.verdict" class="chip" :class="VERDICT[sched.verdict]?.cls"
                  :title="VERDICT[sched.verdict]?.tip">{{ sched.verdict }}</span>
            <span>还剩 {{ sched.remaining_hours }} 小时的量<template v-if="sched.days_left !== null">，
              距目标日 {{ sched.days_left }} 天（按每周 {{ sched.weekly_hours }} 小时约
              {{ sched.capacity_hours }} 小时）</template></span>
          </div>
          <p v-if="sched.verdict === '不可能'" class="warn-text" style="font-size: 11.5px; line-height: 1.6">
            这个期限装不下。按现在的投入要学到 <b>{{ sched.suggested_target_date }}</b>（约
            {{ sched.need_days }} 天）。要么把目标日期改到那天，要么加大每周投入——
            都不改的话，就让 AI 按现有时间压出一份最精炼的。
          </p>
          <p v-else-if="sched.verdict === '紧' " class="dim" style="font-size: 11.5px; line-height: 1.6">
            排得进去，但几乎没有余量。宽松一点的话学到 <b>{{ sched.suggested_target_date }}</b>。
          </p>
          <p v-if="sched.behind" class="warn-text" style="font-size: 11.5px">
            已经过了截止日还没建出来的点：<b>{{ sched.behind }}</b> 个。
          </p>
          <div class="sched-acts">
            <button class="btn subtle tiny" title="把下面各阶段的截止日填成建议值（之后还能手改）"
                    @click="applyDeadlines">
              <Icon name="clock" :size="13" />排截止日
            </button>
            <button v-if="sched.verdict === '不可能' || sched.verdict === '紧'" class="btn subtle tiny"
                    :disabled="proposing || !plan.goal.trim()"
                    title="按现有时间重拆一份：只留绕不开的点，被砍掉的会列出来"
                    @click="emit('propose', { ...proposeReq, mode: '速学' })">
              <Icon name="fold" :size="13" />{{ proposing ? '压缩中…' : '按现有时间压缩' }}
            </button>
            <button v-if="sched.suggested_target_date && sched.verdict === '不可能'" class="btn subtle tiny"
                    title="把目标日期改成算出来的现实日期"
                    @click="plan.target_date = sched.suggested_target_date; touch()">
              <Icon name="check" :size="13" />改到 {{ sched.suggested_target_date }}
            </button>
          </div>
        </div>

        <div v-if="stat && stat.total" class="plan-stat">
          <div class="bar"><span :style="{ width: `${Math.round((stat.built / stat.total) * 100)}%` }" /></div>
          <div class="legend">
            <span>{{ kindOf(plan.kind).unit }} {{ stat.built }} / {{ stat.total }}</span>
            <template v-for="(n, k) in stat.counts" :key="k">
              <span v-if="n" class="chip" :class="MASTERY[k]?.cls">{{ k }} {{ n }}</span>
            </template>
          </div>
        </div>

        <section v-for="(stage, si) in plan.stages" :key="si" class="section stage">
          <div class="section-head">
            <input v-model="stage.name" class="stage-name" @input="touch" />
            <span v-if="sched?.stages?.[si]?.remaining_hours" class="dim" style="font-size: 11px"
                  :title="`这个阶段还剩 ${sched.stages[si].remaining_points} 个点没建`">
              {{ sched.stages[si].remaining_hours }}h
            </span>
            <input v-model="stage.deadline" class="stage-due"
                   :placeholder="sched?.stages?.[si]?.suggested_deadline || '截止（可选）'" @input="touch" />
            <button class="icon-btn ghost tiny" title="删掉这个阶段"
                    @click="plan.stages.splice(si, 1); touch()">
              <Icon name="trash" :size="13" />
            </button>
          </div>

          <ul>
            <li v-for="(pt, pi) in stage.points" :key="pt.id" class="edge-row point">
              <span class="chip" :class="MASTERY[masteryOf(pt.id)]?.cls"
                    :title="MASTERY[masteryOf(pt.id)]?.tip">{{ masteryOf(pt.id) }}</span>
              <span class="to" :class="{ link: masteryOf(pt.id) !== '未建' }"
                    @click="masteryOf(pt.id) !== '未建' && emit('goto', pt.id)">{{ pt.name || pt.id }}</span>
              <select v-model="pt.load" class="load-pick" title="学习负荷：排时间表的依据" @change="touch">
                <option v-for="l in LOADS" :key="l" :value="l">{{ l }}</option>
              </select>
              <span v-if="pt.why" class="yr why">{{ pt.why }}</span>
              <button v-if="masteryOf(pt.id) === '未建'" class="btn subtle tiny" title="现在就建这个知识点"
                      @click="emit('build', { ...pt, plan: pick, field: plan.field })">建</button>
              <button class="icon-btn ghost tiny" title="从计划里移除" @click="dropPoint(si, pi)">
                <Icon name="x" :size="13" />
              </button>
            </li>
          </ul>

          <div class="add-point">
            <input :value="adding[si]?.id || ''" placeholder="知识点（图里没有也行）"
                   @input="adding = { ...adding, [si]: { ...adding[si], id: $event.target.value } }"
                   @keydown.enter.prevent="addPoint(si)" />
            <input :value="adding[si]?.why || ''" placeholder="为什么要学它"
                   @input="adding = { ...adding, [si]: { ...adding[si], why: $event.target.value } }"
                   @keydown.enter.prevent="addPoint(si)" />
            <button class="icon-btn ghost tiny" title="加进这个阶段（回车也行）" @click="addPoint(si)">
              <Icon name="plus" :size="13" />
            </button>
          </div>
        </section>

        <div class="act-row">
          <button class="btn" @click="addStage">
            <Icon name="plus" :size="14" />加一个{{ plan.kind === '领域' ? '子领域' : '阶段' }}
          </button>
          <button v-if="stat && stat.total" class="btn" title="就这个计划里的点出题"
                  @click="emit('quiz', { ids: Object.keys(stat.points).filter((k) => stat.points[k] !== '未建'),
                                         style: plan.kind === '面试' ? '面试' : '复习',
                                         coach: plan.coach })">
            <Icon name="play" :size="14" />{{ plan.kind === '面试' ? '模拟面试' : '考这个计划' }}
          </button>
          <button class="btn subtle" @click="dropPlan"><Icon name="trash" :size="14" />删掉计划</button>
        </div>

        <p class="dim" style="font-size: 11px; margin-top: 14px; line-height: 1.7">
          计划只写进 <code>.knowrary/plans.json</code>，不碰 md 也不碰画布。
          「未建」的点不会在 vault 里生出空壳——点「建」才真正创建节点。
        </p>
      </template>
    </template>
  </Drawer>
</template>
