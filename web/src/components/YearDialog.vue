<script setup>
/**
 * year 批量回填：一次调用把缺 year 的节点补齐，逐条勾选后写回。
 *
 * **为什么值得单独做一个**：year 是历史视图的开关——没填的节点根本不出现在时间轴上。
 * 实盘 41 个节点缺 year，一个一个在检查器里问模型就是 41 次调用（按账本约 $9），
 * 而"这个概念是哪年出现的"根本用不着候选节点列表和关系类型表，一次问完几毛钱。
 *
 * **默认不是全选**：把握低于 0.6 的照样列出来，但不勾。错的 year 比空的 year 难发现得多——
 * 它会把节点摆到时间轴上一个看起来很正常的位置，没人会回头核。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  proposal: { type: Object, default: null },   // YearProposal，null = 还在问
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['apply', 'goto', 'close'])

const picked = ref(new Set())

// 模型给的 picked 就是初始勾选状态（服务端按 confidence 定的），人再改
watch(() => props.proposal, (p) => {
  picked.value = new Set((p?.suggestions || []).filter((s) => s.picked).map((s) => s.id))
}, { immediate: true })

const rows = computed(() => props.proposal?.suggestions || [])
const skipped = computed(() => props.proposal?.skipped || [])
const chosen = computed(() => rows.value.filter((r) => picked.value.has(r.id)))

function toggle(id) {
  const next = new Set(picked.value)
  next.has(id) ? next.delete(id) : next.add(id)
  picked.value = next
}

const allOn = computed(() => rows.value.length > 0 && chosen.value.length === rows.value.length)
function toggleAll() {
  picked.value = allOn.value ? new Set() : new Set(rows.value.map((r) => r.id))
}

function apply() {
  if (!chosen.value.length) return
  emit('apply', chosen.value.map((r) => ({ id: r.id, year: r.year })))
}

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog year-dialog">
    <header>
      <Icon name="clock" :size="15" />
      <span class="who">补 year</span>
      <span class="dim">历史视图只画填了 year 的节点</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body scroll-thin">
      <p v-if="!proposal" class="tip">正在问模型…这是**一次**调用，41 个点一起答，不是一个一个问。</p>

      <template v-else>
        <p class="tip">
          问了 {{ proposal.asked }} 个，模型给了 {{ rows.length }} 个。
          <span v-if="proposal.remaining">还剩 {{ proposal.remaining }} 个这轮没问（写完再点一次）。</span>
          <b>把握低的默认没勾</b>——错的 year 会把节点摆到时间轴上一个看起来很正常的位置，比空着难发现。
        </p>

        <div v-if="rows.length" class="year-head">
          <label class="pick">
            <input type="checkbox" :checked="allOn" @change="toggleAll" />
            全选
          </label>
          <span class="dim">已选 {{ chosen.length }} / {{ rows.length }}</span>
        </div>

        <ul class="year-list">
          <li v-for="r in rows" :key="r.id" :class="{ off: !picked.has(r.id) }">
            <label class="pick">
              <input type="checkbox" :checked="picked.has(r.id)" @change="toggle(r.id)" />
            </label>
            <div class="body">
              <div class="line">
                <span class="link" @click="emit('goto', r.id)">{{ r.name }}</span>
                <b class="yr">{{ r.year }}</b>
                <span class="tag" :class="{ warn: r.confidence < 0.6 }">
                  把握 {{ Math.round(r.confidence * 100) }}%
                </span>
              </div>
              <div v-if="r.why" class="dim why">{{ r.why }}</div>
            </div>
          </li>
        </ul>

        <!-- 模型没给的：提示词里就要求"拿不准别填"，所以这不是失败。
             摆出来是为了别让人以为已经补齐了。 -->
        <p v-if="skipped.length" class="tip skipped">
          <b>{{ skipped.length }} 个模型说拿不准</b>，没给年份：{{ skipped.join('、') }}。
          这些多半是跨越几十年、没有单一起点的通用概念，留空是对的。
        </p>
      </template>
    </div>

    <footer>
      <span class="dim">写回走 md 的 frontmatter，和别处一样先备份</span>
      <button class="btn primary" :disabled="busy || !chosen.length" @click="apply">
        <Icon name="check" :size="14" />写入 {{ chosen.length }} 个
      </button>
    </footer>
  </div>
</template>

<style scoped>
.year-dialog { width: min(640px, 92vw); display: flex; flex-direction: column; max-height: 78vh }
.year-dialog .rel-body { flex: 1; overflow: auto; padding: 10px 14px }
.year-head { display: flex; align-items: center; gap: 10px; margin: 8px 0 6px }
.year-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px }
.year-list li { display: flex; gap: 8px; padding: 6px 8px; border-radius: 6px; align-items: flex-start }
.year-list li:hover { background: var(--hover, rgba(127, 127, 127, 0.08)) }
.year-list li.off { opacity: 0.5 }
.pick { display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 12px }
.body { flex: 1; min-width: 0 }
.line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap }
.yr { font-variant-numeric: tabular-nums }
.why { font-size: 11.5px; line-height: 1.5 }
.tag.warn { color: var(--warn, #b26b00) }
.skipped { margin-top: 12px; line-height: 1.6 }
.year-dialog footer { display: flex; align-items: center; justify-content: space-between;
                      gap: 10px; padding: 10px 14px; border-top: 1px solid var(--line, #e3e3e3) }
</style>
