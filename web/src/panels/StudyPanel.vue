<script setup>
/**
 * 今日清单（教练）：今天可以动手的事，按固定优先级排。
 *
 *     逾期错题 > 到期复习 > 当前阶段「未建」的点 > 「只有壳」的点 > Inbox 里待上图的
 *
 * 前两项是**保鲜**（图谱不腐烂），中间两项是**建设**（图谱长出来）。
 * 空图时前两项自然为空，清单从第三项开始照样排得出东西——学习计划本来就不需要图里先有节点。
 *
 * **这一屏不调 LLM。** "今天干什么"是排序不是生成，模型只在制定计划、出题、批改时出场。
 */
import { computed, ref } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  today: { type: Object, default: null },    // { items, counts, plans, generated_at }
  busy: { type: Boolean, default: false },   // 出题中
})
const emit = defineEmits(['goto', 'quiz', 'review', 'build', 'write', 'place', 'plans', 'refresh', 'close'])

// 每一类怎么呈现、点下去干什么。act 为空的只跳转定位。
const KIND = {
  wrong:   { label: '错题',   icon: 'warn',      cls: 'm-due',      act: '再考一次', event: 'quiz' },
  due:     { label: '待复习', icon: 'rotate',    cls: 'm-due',      act: '考一下',   event: 'quiz' },
  unbuilt: { label: '未建',   icon: 'plus',      cls: 'm-unbuilt',  act: '建',       event: 'build' },
  shell:   { label: '只有壳', icon: 'pencil',    cls: 'm-shell',    act: '写正文',   event: 'write' },
  inbox:   { label: 'Inbox',  icon: 'inbox',     cls: 'm-learned',  act: '放上去',   event: 'place' },
}
const ORDER = ['wrong', 'due', 'unbuilt', 'shell', 'inbox']

const items = computed(() => props.today?.items || [])
const groups = computed(() =>
  ORDER.map((k) => ({ kind: k, ...KIND[k], rows: items.value.filter((i) => i.kind === k) }))
       .filter((g) => g.rows.length))
// 出题范围。默认「今日」＝错题+到期；范围里只会有已经建出来的点，
// 「未建」「只有壳」没有正文，出不了题，服务端已经过滤过了。
const POOLS = ['今日', '没考过', '已建全部']
const pool = ref('今日')
const quizable = computed(() => props.today?.pools?.[pool.value] || [])
const poolCount = (k) => (props.today?.pools?.[k] || []).length
</script>

<template>
  <Drawer side="left" title="今日" icon="rotate" storage-key="study" :default-width="330"
          @close="emit('close')">
    <template #head-actions>
      <span v-if="items.length" class="head-count">{{ items.length }}</span>
      <button class="icon-btn ghost tiny" title="重新排一遍" @click="emit('refresh')">
        <Icon name="refresh" :size="14" />
      </button>
    </template>

    <template #default>
      <div v-if="!today" class="dim">排清单中…</div>

      <template v-else>
        <!-- 计划进度：一行一个计划，点了进计划面板 -->
        <div v-if="today.plans.length" class="plan-lines">
          <div v-for="p in today.plans" :key="p.id" class="plan-line link" @click="emit('plans', p.id)">
            <span class="nm">{{ p.name }}</span>
            <span class="dim">{{ p.done ? '这份建完了' : p.stage || '还没排阶段' }}</span>
            <!-- 落后 = 已经过了阶段截止日、却还没建出来的点。只看计划里写着的 deadline，
                 建议日不参与判定，否则天天变脸。 -->
            <span v-if="p.behind" class="chip m-due" :title="`已经过了截止日还没建出来 ${p.behind} 个点`">
              落后 {{ p.behind }}
            </span>
            <span v-else-if="p.stage_deadline && !p.done" class="dim" style="font-size: 10.5px"
                  :title="p.days_left !== null ? `距目标日还有 ${p.days_left} 天` : '当前阶段的截止日'">
              → {{ p.stage_deadline.slice(5) }}
            </span>
            <span class="yr">{{ p.built }}/{{ p.total }}</span>
          </div>
        </div>

        <div class="pool-row">
          <button v-for="k in POOLS" :key="k" class="btn subtle tiny" :class="{ on: pool === k }"
                  :title="k === '没考过' ? '有正文但还没自测过的' : k === '已建全部' ? '所有建出来的点' : '错题 + 今天到期'"
                  @click="pool = k">{{ k }} {{ poolCount(k) }}</button>
        </div>
        <button class="btn primary" :disabled="busy || !quizable.length"
                style="width: 100%; justify-content: center; margin: 8px 0 12px"
                @click="emit('quiz', quizable)">
          <Icon name="play" :size="14" />
          {{ busy ? '出题中…' : quizable.length ? `开始测验（${quizable.length}）` : '这个范围里没有可考的' }}
        </button>

        <section v-for="g in groups" :key="g.kind" class="section" :class="{ first: g === groups[0] }">
          <div class="section-head">
            <Icon :name="g.icon" :size="13" />{{ g.label }}
            <span class="count">{{ g.rows.length }}</span>
          </div>
          <ul>
            <li v-for="it in g.rows" :key="it.id" class="edge-row point">
              <span class="to" :class="{ link: it.kind !== 'unbuilt' }"
                    @click="it.kind !== 'unbuilt' && emit('goto', it.id)">{{ it.name }}</span>
              <span class="yr">{{ it.detail }}</span>
              <button class="btn subtle tiny" :title="it.why || it.detail"
                      @click="emit(g.event, it)">{{ g.act }}</button>
            </li>
          </ul>
          <p v-if="g.kind === 'unbuilt' && g.rows[0]?.why" class="dim why-note">
            {{ g.rows[0].plan_name }} · {{ g.rows[0].stage }}：{{ g.rows[0].why }}
          </p>
        </section>

        <div v-if="!items.length" class="empty">
          <Icon name="check" :size="30" :width="1.3" />
          <span class="t">今天没什么要做的</span>
          <span class="s">复习清空了，计划里的点也都建出来了。<br>
            想学新东西就去「学习计划」立一份。</span>
          <button class="btn" style="margin-top: 12px" @click="emit('plans', null)">
            <Icon name="checklist" :size="14" />打开学习计划
          </button>
        </div>

        <p class="dim" style="font-size: 11px; margin-top: 14px">排于 {{ today.generated_at }}</p>
      </template>
    </template>
  </Drawer>
</template>
