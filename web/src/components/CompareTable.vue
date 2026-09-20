<script setup>
// 对比表：行 = 成员（顺序跟着对比组 md 里 `- 包含::` 的书写顺序），列 = dimensions + 残差列。
//
// 三条显示规矩，都是为了 20 行 × 8 列还能读：
//
// 1. **列可选**，勾选状态存 localStorage **不回写 md**。`dimensions` 是"这个组按哪些维度
//    对比"的真相，"我这次只想看 3 列"是视图偏好。混在一起的话，看一眼表就改一次 md。
// 2. **整列一致的默认收起**。对比表的信息量全在差异上，一列 20 行写着同一句话只是在占宽度。
//    有空格子的列不算一致（那是"还没填"，收起来等于把该补的藏了）——这条判断在服务端。
// 3. **长文本截断，点一下展开**。
//
// 残差列 = 成员 `## 速查` 里没被 dimensions 认领的键。它一列干两件事：只有一个成员有的
// 是真·独特点；两个以上成员都有的是"这其实是个维度"，表头会提示升成一列。
// 没有这一列的话，写错名的格子只会显示成"空"，你看到的是"这篇没写"，
// 而看不出"写了但名字不对"。
import { computed, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  table: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['goto', 'refresh', 'fill', 'save'])

const hidden = ref(new Set())        // 手动收起的列
const opened = ref(new Set())        // 展开了全文的格子
const picking = ref(false)

const storageKey = computed(() => `knowrary-compare-cols:${props.table?.id || ''}`)

/** 换了对比组就换一份勾选状态。读不到就按"整列一致的收起"来。 */
watch(() => props.table?.id, () => {
  opened.value = new Set()
  picking.value = false
  let saved = null
  try { saved = JSON.parse(localStorage.getItem(storageKey.value) || 'null') } catch { saved = null }
  hidden.value = new Set(Array.isArray(saved) ? saved : (props.table?.uniform || []))
}, { immediate: true })

function persist() {
  try { localStorage.setItem(storageKey.value, JSON.stringify([...hidden.value])) } catch { /* 无痕模式 */ }
}

function toggleCol(dim) {
  const next = new Set(hidden.value)
  next.has(dim) ? next.delete(dim) : next.add(dim)
  hidden.value = next
  persist()
}

const columns = computed(() => (props.table?.columns || []).filter((c) => !hidden.value.has(c)))
const hasExtra = computed(() => (props.table?.rows || []).some((r) => Object.keys(r.extra).length))
const promoted = computed(() => props.table?.promote || [])

// 正在编辑哪一格。**只有来自 `## 速查` 的格子能这样改**：frontmatter 那几列
// （年份 / 参数量 / 抽象层）的真相在 frontmatter 里，改它们要走 update_frontmatter，
// 从正文写第二份就是双源。所以那几格这里只读，点了给一句话说明。
const editing = ref(null)     // { rid, dim }
const draft = ref('')

function startEdit(row, dim) {
  const cell = row.cells[dim]
  if (cell.source === 'frontmatter') {
    emit('goto', row.id)
    return
  }
  if (!row.built || row.stub) return
  editing.value = { rid: row.id, dim }
  draft.value = cell.value || ''
}

const isEditing = (rid, dim) => editing.value?.rid === rid && editing.value?.dim === dim

function commit() {
  const at = editing.value
  if (!at) return
  editing.value = null
  const before = props.table.rows.find((r) => r.id === at.rid)?.cells[at.dim]?.value || ''
  const next = draft.value.trim()
  if (next === before.trim()) return
  // 空 = 删掉这一行。表格里清空一格，本意就是"这条我没有"
  emit('save', { id: at.rid, key: at.dim, value: next })
}

const CUT = 34
const cellKey = (rid, dim) => `${rid}\u0000${dim}`
const isLong = (text) => (text || '').length > CUT
function shown(rid, dim, text) {
  if (!text) return ''
  return isLong(text) && !opened.value.has(cellKey(rid, dim)) ? `${text.slice(0, CUT)}…` : text
}
function toggleCell(rid, dim, text) {
  if (!isLong(text)) return
  const next = new Set(opened.value)
  const k = cellKey(rid, dim)
  next.has(k) ? next.delete(k) : next.add(k)
  opened.value = next
}

const extraText = (row) => Object.entries(row.extra).map(([k, v]) => `${k}：${v}`).join('；')

/** 进编辑态就把光标放进去——双击之后还要再点一下才能打字，那就不叫"改一格"了。 */
const vFocus = { mounted: (el) => { el.focus(); el.select() } }
</script>

<template>
  <section v-if="table" class="cmp">
    <header class="cmp-head">
      <b class="cmp-title">{{ table.name }}</b>
      <span class="dim cmp-sub">
        {{ table.rows.length }} 行 · {{ table.columns.length }} 列
        <template v-if="table.gaps">· <span class="warn-text">{{ table.gaps }}/{{ table.cells }} 格空着</span></template>
      </span>
      <button v-if="table.gaps" class="btn tiny primary" :disabled="busy"
              title="一次调用问完整张表的空格子；写入前逐格可勾可改"
              @click="emit('fill')">
        <Icon name="warn" :size="13" />补这 {{ table.gaps }} 格
      </button>
      <button class="btn tiny" :class="{ on: picking }" title="选要显示的列（只影响这一屏，不改 md）"
              @click="picking = !picking">
        <Icon name="fold" :size="13" />列
      </button>
      <button class="icon-btn ghost" :disabled="busy" title="重新算一遍" @click="emit('refresh')">
        <Icon name="refresh" :size="15" />
      </button>
    </header>

    <div v-if="picking" class="cmp-cols">
      <button v-for="dim in table.columns" :key="dim" class="chip" :class="{ on: !hidden.has(dim) }"
              @click="toggleCol(dim)">
        <Icon v-if="!hidden.has(dim)" name="check" :size="11" :width="2.6" />{{ dim }}
        <span v-if="table.uniform.includes(dim)" class="dim" style="font-size: 10px">全一致</span>
      </button>
      <span class="dim" style="font-size: 11px; align-self: center">
        「全一致」的列默认收起——对比表的信息量全在差异上
      </span>
    </div>

    <p v-if="promoted.length" class="cmp-promote">
      <Icon name="warn" :size="13" />
      <span>
        <b>{{ promoted.map((p) => p.key).join('、') }}</b>
        在 {{ promoted[0].count }} 个成员上都写了，但不在 dimensions 里——
        要么它其实是一个维度（加进对比组的 <code>dimensions</code>），要么是某几篇的措辞跑偏了。
      </span>
    </p>

    <div class="cmp-scroll">
      <table class="cmp-table">
        <thead>
          <tr>
            <th class="sticky">成员</th>
            <th v-for="dim in columns" :key="dim">{{ dim }}</th>
            <th v-if="hasExtra" class="cmp-extra-col">其它（速查里没归类的）</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in table.rows" :key="row.id">
            <th class="sticky row-head">
              <button class="linkish" @click="emit('goto', row.id)">{{ row.name }}</button>
              <span v-if="!row.built" class="tag ghost-tag">还没建</span>
              <span v-else-if="row.stub" class="tag stub-tag">只有壳</span>
            </th>
            <td v-for="dim in columns" :key="dim"
                :class="{ gap: !row.cells[dim].value, fm: row.cells[dim].source === 'frontmatter',
                          long: isLong(row.cells[dim].value), editing: isEditing(row.id, dim) }"
                :title="row.cells[dim].source === 'frontmatter'
                  ? `来自 frontmatter 的 ${row.cells[dim].field}，改它要走节点详情`
                  : (row.built && !row.stub ? '双击改这一格（清空 = 删掉这一行）' : '')"
                @click="toggleCell(row.id, dim, row.cells[dim].value)"
                @dblclick="startEdit(row, dim)">
              <input v-if="isEditing(row.id, dim)" class="cell-edit" v-model="draft"
                     @keydown.enter.prevent="commit" @keydown.esc.prevent="editing = null"
                     @blur="commit" v-focus />
              <template v-else>{{ shown(row.id, dim, row.cells[dim].value) || '—' }}</template>
            </td>
            <td v-if="hasExtra" class="cmp-extra-col dim">{{ extraText(row) || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.cmp { display: flex; flex-direction: column; min-height: 0; border-top: 1px solid var(--line); background: var(--surface-1) }
.cmp-head { display: flex; align-items: center; gap: 10px; padding: 7px 12px; border-bottom: 1px solid var(--line) }
.cmp-title { font-size: 13px }
.cmp-sub { font-size: 11.5px; margin-right: auto }
.cmp-cols { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px; border-bottom: 1px solid var(--line) }
.chip {
  display: inline-flex; align-items: center; gap: 4px; padding: 3px 9px; font-size: 11.5px;
  border: 1px solid var(--line); border-radius: 999px; background: none; color: var(--text-2); cursor: pointer;
}
.chip.on { background: var(--accent-soft); color: var(--accent-text); border-color: transparent }
.cmp-promote {
  display: flex; gap: 7px; align-items: flex-start; margin: 0; padding: 8px 12px;
  font-size: 11.5px; line-height: 1.6; color: var(--text-2); border-bottom: 1px solid var(--line);
}
.cmp-scroll { overflow: auto; min-height: 0 }
.cmp-table { border-collapse: collapse; font-size: 12px; width: 100% }
.cmp-table th, .cmp-table td {
  border-bottom: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top;
  max-width: 300px;
}
.cmp-table thead th {
  position: sticky; top: 0; z-index: 2; background: var(--surface-2); font-weight: 650;
  font-size: 11px; letter-spacing: .04em; white-space: nowrap;
}
.sticky { position: sticky; left: 0; z-index: 1; background: var(--surface-1) }
.cmp-table thead th.sticky { z-index: 3; background: var(--surface-2) }
.row-head { white-space: nowrap; font-weight: 550 }
.linkish { background: none; border: 0; padding: 0; color: inherit; font: inherit; cursor: pointer; text-align: left }
.linkish:hover { color: var(--accent) }
.tag { margin-left: 6px; padding: 0 5px; border-radius: 4px; font-size: 10px; font-weight: 500 }
.ghost-tag { border: 1px dashed var(--line); color: var(--text-3) }
.stub-tag { background: var(--warn-soft, rgba(200,120,0,.14)); color: var(--warn-text, #a86400) }
td.gap { color: var(--text-3) }
td.fm { color: var(--text-2); font-variant-numeric: tabular-nums }
td.long { cursor: pointer }
td.editing { padding: 2px 6px }
.cell-edit {
  width: 100%; font: inherit; font-size: 12px; padding: 3px 5px;
  border: 1px solid var(--accent); border-radius: 5px; background: var(--surface-1); color: var(--text-1);
}
.cell-edit:focus { outline: none }
td.long:hover { background: var(--surface-2) }
.cmp-extra-col { font-size: 11px; max-width: 260px }
code { font-size: 11px; padding: 1px 4px; border-radius: 4px; background: var(--chip, rgba(127,127,127,.14)) }
</style>
