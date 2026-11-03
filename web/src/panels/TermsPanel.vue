<script setup>
// 术语面板（名词解释，2026-10-27）：边学边记攒下的名词，搜 / 浏 / 加 / 点词跳到知识点。
//
// 数据来自索引的 `glossary`（`术语/*.md` 合并来的），**文件即语境**：全局一组、各专题一组。
// 同词多义就在各自语境组里各出一条、各标出处（语境只分组不隐藏）。
// 加词：顶部「+」展开表单；正文里选字「记为名词」会带着词和语境 prefill 进来，自动展开填好。
import { computed, nextTick, reactive, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  entries: { type: Array, default: () => [] },      // [{ term, gloss, scope, aka[], see }]
  nodeName: { type: Function, default: (id) => id },   // 详见目标 id → 名字
  current: { type: String, default: '' },            // 当前专题名：它那组排在全局之后、置顶，也当加词默认语境
  prefill: { type: Object, default: null },          // 选字记词带进来的 { term, scope }（每次都换对象引用）
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'goto', 'refresh', 'add'])

const GLOBAL = '全局'
const q = ref('')

/** 搜词 / 别名 / 解释正文，大小写不敏感。 */
function hit(e, kw) {
  if (!kw) return true
  const hay = [e.term, e.gloss, ...(e.aka || [])].join('\n').toLowerCase()
  return hay.includes(kw)
}

// 分组：全局最前，当前专题紧随，其余按名字。组内按词排。
const groups = computed(() => {
  const kw = q.value.trim().toLowerCase()
  const by = new Map()
  for (const e of props.entries) {
    if (!hit(e, kw)) continue
    if (!by.has(e.scope)) by.set(e.scope, [])
    by.get(e.scope).push(e)
  }
  const rank = (s) => (s === GLOBAL ? 0 : s === props.current ? 1 : 2)
  return [...by.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0], 'zh'))
    .map(([scope, list]) => ({ scope, list: list.sort((a, b) => a.term.localeCompare(b.term, 'zh')) }))
})

const total = computed(() => props.entries.length)
const shown = computed(() => groups.value.reduce((n, g) => n + g.list.length, 0))

// ---- 加词 ----
const adding = ref(false)
const termEl = ref(null)
const form = reactive({ term: '', gloss: '', scope: GLOBAL, aka: '' })

// 语境下拉：全局 + 库里已有的语境 + 当前专题，去重；默认选当前专题（没有就全局）。
const scopeOptions = computed(() => {
  const set = new Set([GLOBAL, ...props.entries.map((e) => e.scope)])
  if (props.current) set.add(props.current)
  return [...set].sort((a, b) => (a === GLOBAL ? -1 : b === GLOBAL ? 1 : a.localeCompare(b, 'zh')))
})

function openAdd(seed = {}) {
  adding.value = true
  form.term = seed.term || ''
  form.gloss = ''
  form.scope = seed.scope || props.current || GLOBAL
  form.aka = ''
  nextTick(() => termEl.value?.focus())
}

// 选字记词：prefill 换了就展开表单、把词和语境填上
watch(() => props.prefill, (p) => { if (p) openAdd(p) })

function submit() {
  const term = form.term.trim()
  if (!term) return
  emit('add', {
    term,
    gloss: form.gloss.trim(),
    scope: form.scope || GLOBAL,
    aka: form.aka.split(/[,，、]/).map((s) => s.trim()).filter(Boolean),
  })
  adding.value = false
}
</script>

<template>
  <Drawer side="left" title="术语" icon="bookmark" storage-key="terms" :default-width="300" @close="emit('close')">
    <template #head-actions>
      <button class="icon-btn ghost" :title="adding ? '收起加词' : '加一个名词'" data-act="toggle-add"
              @click="adding ? (adding = false) : openAdd()">
        <Icon :name="adding ? 'x' : 'plus'" :size="15" />
      </button>
      <button class="icon-btn ghost" title="重新拉一遍" @click="emit('refresh')">
        <Icon name="refresh" :size="15" />
      </button>
    </template>

    <template #default>
      <form v-if="adding" class="term-add" data-term-add @submit.prevent="submit">
        <input ref="termEl" v-model="form.term" data-add-term placeholder="词条，如 GDT" spellcheck="false" />
        <textarea v-model="form.gloss" data-add-gloss rows="2" placeholder="一句话解释" spellcheck="false" />
        <div class="term-add-row">
          <select v-model="form.scope" data-add-scope title="记在哪个语境（= 哪个文件）。拿不准就记当前专题，以后能改">
            <option v-for="s in scopeOptions" :key="s" :value="s">{{ s }}</option>
          </select>
          <input v-model="form.aka" data-add-aka placeholder="别名 / 缩写（逗号分隔）" spellcheck="false" />
        </div>
        <div class="term-add-act">
          <button class="btn primary tiny" :disabled="busy || !form.term.trim()" data-act="submit-add">
            <Icon name="check" :size="13" />记下
          </button>
          <button type="button" class="btn ghost tiny" @click="adding = false">取消</button>
        </div>
      </form>

      <p v-else class="dim" style="font-size: 12px; line-height: 1.6; margin: 0 0 10px">
        看书随手记下的名词。按语境分组——全局的、各专题的；同一个词在不同语境各一条。
        正文里选中一段字，会冒出「记为名词」。
      </p>

      <div class="term-search">
        <Icon name="search" :size="14" class="dim" />
        <input v-model="q" data-term-search placeholder="搜词 / 别名 / 解释" spellcheck="false" />
        <button v-if="q" class="icon-btn ghost tiny" title="清空" @click="q = ''"><Icon name="x" :size="13" /></button>
      </div>

      <p v-if="!total" class="dim term-empty">
        还没有术语。点右上角「+」加一个，或在正文里选字「记为名词」。
      </p>
      <p v-else-if="!shown" class="dim term-empty">没有匹配「{{ q }}」的术语。</p>

      <div v-for="g in groups" :key="g.scope" class="term-group" :data-scope="g.scope">
        <div class="term-group-head">
          <span class="scope-tag" :class="{ global: g.scope === GLOBAL }">{{ g.scope }}</span>
          <span class="dim count">{{ g.list.length }}</span>
        </div>
        <div v-for="(e, i) in g.list" :key="e.term + i" class="term-row" :data-term="e.term">
          <div class="term-line">
            <b class="term-name">{{ e.term }}</b>
            <span v-if="e.aka?.length" class="term-aka dim">{{ e.aka.join('、') }}</span>
            <button v-if="e.see" class="term-see" :title="`去看知识点「${nodeName(e.see)}」`"
                    data-act="goto" @click="emit('goto', e.see)">
              <Icon name="arrowRight" :size="12" />{{ nodeName(e.see) }}
            </button>
          </div>
          <p v-if="e.gloss" class="term-gloss">{{ e.gloss }}</p>
        </div>
      </div>

      <p v-if="total" class="dim term-foot">共 {{ total }} 条{{ shown < total ? `，显示 ${shown}` : '' }}</p>
    </template>
  </Drawer>
</template>

<style scoped>
.term-add {
  display: flex; flex-direction: column; gap: 7px; padding: 10px;
  border: 1px solid var(--accent); border-radius: 8px; margin-bottom: 12px;
  background: var(--accent-soft);
}
.term-add input, .term-add textarea, .term-add select {
  width: 100%; box-sizing: border-box; font-size: 12.5px;
}
.term-add textarea { resize: vertical; }
.term-add-row { display: flex; gap: 6px; }
.term-add-row select { flex: 0 0 38%; min-width: 0; }
.term-add-row input { flex: 1; min-width: 0; }
.term-add-act { display: flex; gap: 6px; }
.term-search {
  display: flex; align-items: center; gap: 6px; padding: 5px 8px;
  border: 1px solid var(--line); border-radius: 7px; margin-bottom: 12px;
}
.term-search input { flex: 1; min-width: 0; border: none; background: none; outline: none; font-size: 13px; }
.term-empty { font-size: 12px; line-height: 1.7; }
.term-group { margin-bottom: 14px; }
.term-group-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.scope-tag {
  font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent-text);
}
.scope-tag.global { background: color-mix(in srgb, var(--gold, #c98a12) 16%, transparent); color: var(--gold, #c98a12); }
.term-group-head .count { font-size: 11px; }
.term-row { padding: 6px 0; border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent); }
.term-line { display: flex; align-items: baseline; gap: 7px; flex-wrap: wrap; }
.term-name { font-size: 13.5px; }
.term-aka { font-size: 11px; }
.term-see {
  margin-left: auto; display: inline-flex; align-items: center; gap: 2px;
  font-size: 11px; color: var(--accent-text); background: none; border: none; cursor: pointer; padding: 0;
}
.term-see:hover { text-decoration: underline; }
.term-gloss { font-size: 12.5px; line-height: 1.55; margin: 3px 0 0; white-space: pre-wrap; color: var(--text-2); }
.term-foot { font-size: 11px; text-align: center; margin: 4px 0 0; }
</style>
