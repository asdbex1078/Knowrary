<script setup>
/**
 * 对比表补空格子：**一次调用**问完整张表，逐格勾选后写回。
 *
 * 为什么一次问完而不是一格一问：5 个成员 × 4 列就是 20 次调用，而"这张表在比什么"
 * 每次都要重讲一遍。一次给整张表，模型还能横着看——同一列已经填好的那几格就是口径，
 * 这正是逐格问拿不到的东西。
 *
 * **默认不是全选**：把握低于 0.6 的照样列出来，但不勾。填错一句会被当成笔记里的结论
 * 反复看到，而看起来很通顺的错话没人会回头核。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  proposal: { type: Object, default: null },   // CompareProposal，null = 还在问
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['apply', 'goto', 'close'])

const picked = ref(new Set())
const edited = ref({})          // 人改过的值：写回前可改，是这套东西的老规矩

const keyOf = (r) => `${r.id}\u0000${r.key}`

watch(() => props.proposal, (p) => {
  picked.value = new Set((p?.suggestions || []).filter((s) => s.picked).map(keyOf))
  edited.value = {}
}, { immediate: true })

const rows = computed(() => props.proposal?.suggestions || [])
const skipped = computed(() => props.proposal?.skipped || [])
const chosen = computed(() => rows.value.filter((r) => picked.value.has(keyOf(r))))
const valueOf = (r) => (keyOf(r) in edited.value ? edited.value[keyOf(r)] : r.value)

function toggle(r) {
  const next = new Set(picked.value)
  const k = keyOf(r)
  next.has(k) ? next.delete(k) : next.add(k)
  picked.value = next
}

const allOn = computed(() => rows.value.length > 0 && chosen.value.length === rows.value.length)
function toggleAll() {
  picked.value = allOn.value ? new Set() : new Set(rows.value.map(keyOf))
}

function apply() {
  const rowsOut = chosen.value
    .map((r) => ({ id: r.id, key: r.key, value: (valueOf(r) || '').trim() }))
    .filter((r) => r.value)
  if (rowsOut.length) emit('apply', rowsOut)
}

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog year-dialog">
    <header>
      <Icon name="table" :size="15" />
      <span class="who">补这张表</span>
      <span class="dim">只填空格子，已经写好的一个不动</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body scroll-thin">
      <p v-if="!proposal" class="tip">正在问模型…<b>这是一次调用</b>，整张表一起答，不是一格一问。</p>

      <template v-else>
        <p class="tip">
          问了 {{ proposal.asked }} 格，模型给了 {{ rows.length }} 格。
          <span v-if="proposal.remaining">还剩 {{ proposal.remaining }} 格这轮没问（写完再点一次）。</span>
          <b>把握低的默认没勾</b>，而且<b>写入前可以改</b>——填错一句会被当成笔记里的结论反复看到。
        </p>

        <div v-if="rows.length" class="year-head">
          <label class="pick">
            <input type="checkbox" :checked="allOn" @change="toggleAll" />
            全选
          </label>
          <span class="dim">已选 {{ chosen.length }} / {{ rows.length }}</span>
        </div>

        <ul class="year-list">
          <li v-for="r in rows" :key="keyOf(r)" :class="{ off: !picked.has(keyOf(r)) }">
            <label class="pick">
              <input type="checkbox" :checked="picked.has(keyOf(r))" @change="toggle(r)" />
            </label>
            <div class="body">
              <div class="line">
                <span class="link" @click="emit('goto', r.id)">{{ r.name }}</span>
                <span class="dim">·</span><b>{{ r.key }}</b>
                <span class="tag" :class="{ warn: r.confidence < 0.6 }">
                  把握 {{ Math.round(r.confidence * 100) }}%
                </span>
              </div>
              <input class="cell-input" :value="valueOf(r)"
                     @input="edited = { ...edited, [keyOf(r)]: $event.target.value }" />
              <div v-if="r.why" class="dim why">{{ r.why }}</div>
            </div>
          </li>
        </ul>

        <!-- 模型没给的：提示词里就要求"拿不准别填"，所以这不是失败。
             摆出来是为了别让人以为这张表已经补齐了。 -->
        <p v-if="skipped.length" class="tip skipped">
          <b>{{ skipped.length }} 格模型说拿不准</b>，没填：{{ skipped.join('、') }}。
          多半是这一列对那个技术根本不适用——留空是对的。
        </p>
      </template>
    </div>

    <footer>
      <span class="dim">一格写一行 `## 速查`，先备份再写，改动可以在 md 里回看</span>
      <button class="btn primary" :disabled="busy || !chosen.length" @click="apply">
        <Icon name="check" :size="14" />写入 {{ chosen.length }} 格
      </button>
    </footer>
  </div>
</template>

<style scoped>
.year-dialog { width: min(680px, 92vw); display: flex; flex-direction: column; max-height: 78vh }
.year-dialog .rel-body { flex: 1; overflow: auto; padding: 10px 14px }
.year-head { display: flex; align-items: center; gap: 10px; margin: 8px 0 6px }
.year-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px }
.year-list li { display: flex; gap: 8px; padding: 6px 8px; border-radius: 6px; align-items: flex-start }
.year-list li:hover { background: var(--hover, rgba(127, 127, 127, 0.08)) }
.year-list li.off { opacity: 0.5 }
.pick { display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 12px }
.body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px }
.line { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap }
.cell-input {
  width: 100%; font: inherit; font-size: 12px; padding: 4px 7px;
  border: 1px solid var(--line); border-radius: 6px; background: var(--surface-1); color: var(--text-1);
}
.cell-input:focus { outline: none; border-color: var(--accent) }
.why { font-size: 11.5px; line-height: 1.5 }
</style>
