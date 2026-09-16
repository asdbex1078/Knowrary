<script setup>
/**
 * 学习日历：每天建了几个、复习了几次、答了几道、烧了多少钱。
 *
 * **全部派生，不新增任何记录**（重构方案 §6）：`learned` + review-log + quiz-log + llm-usage
 * 已经够画一张有信息量的图了。**学习时长没做**——手动计时要处理"忘了停表"，
 * 一旦有脏数据整张日历就不可信；而"这天学得多不多"，现有数据已经回答得了。
 */
import { computed, ref } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  data: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['goto', 'refresh', 'close'])

const picked = ref('')

// 热力等级：建一个节点算 3 分，复习 / 答题各 1 分。**不算模型调用**——
// 那是花销不是学习量，算进去会让"跟模型聊了一下午"看起来像"学了一整天"。
const weight = (c) => (c ? c.built * 3 + c.reviews + c.answers : 0)
const level = (c) => {
  const w = weight(c)
  return w === 0 ? 0 : w <= 2 ? 1 : w <= 5 ? 2 : w <= 10 ? 3 : 4
}

/** 按周分列铺：一列一周，周一在上。热力图的标准排法，一眼看得出节奏。 */
const weeks = computed(() => {
  const d = props.data
  if (!d) return []
  const out = []
  const start = new Date(`${d.from}T00:00:00`)
  const end = new Date(`${d.to}T00:00:00`)
  const cursor = new Date(start)
  cursor.setDate(cursor.getDate() - ((cursor.getDay() + 6) % 7))   // 回退到周一
  while (cursor <= end) {
    const col = []
    for (let i = 0; i < 7; i += 1) {
      const iso = cursor.toISOString().slice(0, 10)
      col.push(iso >= d.from && iso <= d.to ? iso : null)
      cursor.setDate(cursor.getDate() + 1)
    }
    out.push(col)
  }
  return out
})

const cell = (iso) => (iso ? props.data?.days?.[iso] : null)
const detail = computed(() => (picked.value ? props.data?.detail?.[picked.value] : null))
const tip = (iso) => {
  const c = cell(iso)
  if (!iso) return ''
  if (!c) return `${iso}：没动静`
  const bits = []
  if (c.built) bits.push(`建 ${c.built}`)
  if (c.reviews) bits.push(`复习 ${c.reviews}${c.lapses ? `（忘 ${c.lapses}）` : ''}`)
  if (c.answers) bits.push(`答题 ${c.answers}`)
  if (c.cost_usd) bits.push(`$${c.cost_usd}`)
  return `${iso}：${bits.join(' · ') || '没动静'}`
}
</script>

<template>
  <Drawer side="left" title="日历" icon="clock" storage-key="calendar" :default-width="340"
          @close="emit('close')">
    <template #head-actions>
      <button class="icon-btn ghost tiny" title="重新算一遍" @click="emit('refresh')">
        <Icon name="refresh" :size="14" />
      </button>
    </template>

    <template #default>
      <div v-if="!data" class="dim">{{ busy ? '算着…' : '加载中…' }}</div>

      <template v-else>
        <div class="cal-sum">
          <span class="chip m-mastered">连续 {{ data.streak }} 天</span>
          <span class="dim">{{ data.totals.active_days }} 天有动静 · 建 {{ data.totals.built }} ·
            复习 {{ data.totals.reviews }} · 答题 {{ data.totals.answers }}</span>
        </div>

        <div class="heat">
          <div v-for="(col, i) in weeks" :key="i" class="heat-col">
            <span v-for="(iso, j) in col" :key="j" class="heat-cell"
                  :class="[`lv-${level(cell(iso))}`, { void: !iso, on: iso === picked }]"
                  :title="tip(iso)" @click="iso && (picked = picked === iso ? '' : iso)" />
          </div>
        </div>
        <p class="dim" style="font-size: 10.5px; margin: 6px 0 0">
          颜色只算学习量（建 ×3、复习 / 答题 ×1），**不算模型调用**——那是花销不是学习量。
        </p>

        <div v-if="picked" class="cal-day">
          <div class="section-head">
            <Icon name="clock" :size="13" />{{ picked }}
            <span class="dim">{{ tip(picked).split('：')[1] }}</span>
            <button class="icon-btn ghost tiny" title="收起" @click="picked = ''">
              <Icon name="x" :size="13" />
            </button>
          </div>

          <div v-if="!detail" class="dim" style="font-size: 11.5px">这天没留下什么。</div>

          <template v-else>
            <ul v-if="detail.built?.length">
              <li v-for="n in detail.built" :key="n.id" class="edge-row point">
                <span class="chip m-learned">建</span>
                <span class="to link" @click="emit('goto', n.id)">{{ n.name || n.id }}</span>
              </li>
            </ul>
            <ul v-if="detail.reviews?.length">
              <li v-for="(r, i) in detail.reviews" :key="`r${i}`" class="edge-row point">
                <span class="chip" :class="r.grade === '忘了' ? 'm-due' : 'm-mastered'">{{ r.grade }}</span>
                <span class="to link" @click="emit('goto', r.id)">{{ r.id }}</span>
              </li>
            </ul>
            <ul v-if="detail.answers?.length">
              <li v-for="(a, i) in detail.answers" :key="`a${i}`" class="edge-row point">
                <span class="chip" :class="a.grade === '忘了' ? 'm-due' : 'm-learned'">{{ a.grade }}</span>
                <span class="to">{{ a.stem || (a.points || []).join('、') }}</span>
              </li>
            </ul>
          </template>
        </div>

        <p class="dim" style="font-size: 11px; margin-top: 14px; line-height: 1.7">
          这一屏全是算出来的，没有第二份记录。**学习时长没做**：手动计时会有"忘了停表"的脏数据，
          被动推断会把"开着页面去吃饭"算进去——而"这天学得多不多"，上面这些已经回答得了。
        </p>
      </template>
    </template>
  </Drawer>
</template>
