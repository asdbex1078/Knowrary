<script setup>
/**
 * 模型谱系里一个型号的卡片：悬停出来，点一下钉住（钉住了才点得到里面的技术点和链接）。
 *
 * 顺序：时间、这张图 `谱系.md` 声明的字段（大模型的参数 / 激活 / 上下文 / 训练数据，CPU 的制程 / 晶体管…）、
 * 前代 / 基于、技术点、亮点、突破、论文；没登记的字段放最底下「其它」。
 * 技术点对上了图里的知识点就能点过去，对不上的只是文字。
 */
import { computed } from 'vue'
import { humanNumber } from '../canvas/modelLanes.js'
import Icon from '../ui/Icon.vue'
import Markdown from '../ui/Markdown.vue'

const props = defineProps({
  item: { type: Object, required: true },         // modelLanes 算出来的点：{ model, family }
  spec: { type: Object, default: null },          // 这张图的声明（/api/models 的 lineages 里那一项）
  pinned: { type: Boolean, default: false },
  style: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['goto', 'article', 'close'])

const m = computed(() => props.item.model)
/** 声明过的字段照声明顺序列；数值的写原文，原文和量级不一样时括号里补一个读得快的写法。 */
const declared = computed(() => (props.spec?.fields || []).map((f) => {
  const raw = m.value.values?.[f.key]
  const n = m.value.numbers?.[f.key]
  const short = f.numeric && typeof n === 'number' ? humanNumber(n, f.unit) : ''
  return [f.key, raw && short && !raw.replace(/\s/g, '').includes(short) ? `${raw}（≈${short}）` : raw]
}))
const rows = computed(() => [
  ...declared.value, ['前代', m.value.prev], ['基于', (m.value.base || []).join('、')],
].filter(([, v]) => v))
/** 家族 frontmatter 里的出处：`[[articles/…]]` 拆成可以在原文面板打开的路径。 */
const sources = computed(() => (props.item.family.sources || []).map((s) => {
  const hit = String(s).match(/\[\[([^\]|#]+)/)
  return hit ? { path: `${hit[1]}.md`, label: hit[1].split('/').pop() } : null
}).filter(Boolean))
</script>

<template>
  <div class="model-card" :class="{ pinned }" :style="style" data-model-card @mousedown.stop>
    <header>
      <b>{{ m.name }}</b>
      <span class="dim">{{ item.family.name }}<template v-if="item.family.open != null"> · {{ item.family.open ? '开源' : '闭源' }}</template></span>
      <button v-if="pinned" class="icon-btn ghost tiny" style="margin-left: auto" title="关掉（Esc）" @click="emit('close')">
        <Icon name="x" :size="12" /></button>
    </header>
    <p class="model-card-line">{{ m.date || '发布时间没写' }}</p>
    <dl>
      <template v-for="[k, v] in rows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
    </dl>
    <div v-if="m.tech?.length" class="model-card-tech">
      <template v-for="t in m.tech" :key="t.text">
        <button v-if="t.node" class="art-chip" :title="`去图上看「${t.text}」`" data-act="goto-tech"
                @click="emit('goto', t.node)">{{ t.text }}</button>
        <span v-else class="chip">{{ t.text }}</span>
      </template>
    </div>
    <p v-if="m.highlight"><span class="k">亮点</span>{{ m.highlight }}</p>
    <p v-if="m.breakthrough"><span class="k">突破</span>{{ m.breakthrough }}</p>
    <p v-if="m.paper"><span class="k">论文</span>
      <a v-if="/^https?:/.test(m.paper)" :href="m.paper" target="_blank" rel="noopener">{{ m.paper }}</a>
      <template v-else>{{ m.paper }}</template></p>
    <details v-if="m.notes" class="model-card-notes">
      <summary>笔记（{{ m.notes.length }} 字）</summary>
      <Markdown :text="m.notes" />
    </details>
    <dl v-if="Object.keys(m.extra || {}).length" class="model-card-extra">
      <template v-for="(v, k) in m.extra" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
    </dl>
    <footer class="dim">
      <button v-for="s in sources" :key="s.path" class="art-chip" data-act="open-source"
              :title="`在原文面板里打开 ${s.path}`" @click="emit('article', s.path)">出处《{{ s.label }}》</button>
      <span :title="'家族文件里的这一行，要改就去这儿改'">{{ item.family.path }}:{{ m.line }}</span>
    </footer>
  </div>
</template>
