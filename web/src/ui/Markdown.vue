<script setup>
/**
 * 把模型回的一段话渲染成图文：Markdown（标题、列表、表格、代码）+ ```mermaid 画的图。
 *
 * **先转义再解析**：源文里的 `<` `>` 一律变实体，所以模型写什么都不会变成真 HTML，
 * 也就没有 XSS 那条路——比引一个消毒库更彻底，代价只是"模型不能写 HTML"，
 * 而它本来也不该写。
 *
 * mermaid 两三兆，**按需加载**：不画图的那些对话一个字节都不下。
 */
import { marked } from 'marked'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  text: { type: String, default: '' },
})

marked.setOptions({ breaks: true, gfm: true })

const FENCE = /```mermaid\s*\n([\s\S]*?)(?:```|$)/g

function escape(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 拆成「文字段 / 图段」交替的一串。图段单独渲染，文字段走 Markdown。 */
const parts = computed(() => {
  const out = []
  let last = 0
  const src = props.text || ''
  for (const m of src.matchAll(FENCE)) {
    if (m.index > last) out.push({ kind: 'md', text: src.slice(last, m.index) })
    out.push({ kind: 'mermaid', text: m[1] })
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ kind: 'md', text: src.slice(last) })
  return out.filter((p) => p.text.trim())
})

const html = (text) => marked.parse(escape(text))

// ---- mermaid：懒加载 + 每段一块 svg ----

let engine = null
const svgs = ref({})       // { 第几段: svg 字符串 }
const bad = ref({})        // 画不出来的那些：退回代码块，别让整条消息空掉

async function draw() {
  const jobs = parts.value.map((p, i) => [i, p]).filter(([, p]) => p.kind === 'mermaid')
  if (!jobs.length) return
  if (!engine) {
    const mod = await import('mermaid')
    engine = mod.default
    engine.initialize({ startOnLoad: false, securityLevel: 'strict',
                        theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default' })
  }
  for (const [i, p] of jobs) {
    if (svgs.value[i] || bad.value[i]) continue
    try {
      const { svg } = await engine.render(`kg-mmd-${Date.now()}-${i}`, p.text.trim())
      svgs.value = { ...svgs.value, [i]: svg }
    } catch (err) {
      // 流式时图往往只到一半，画不出来很正常；等它写完再试，实在不行就当代码看
      bad.value = { ...bad.value, [i]: String(err?.message || err) }
    }
  }
}

watch(() => props.text, () => { svgs.value = {}; bad.value = {}; draw() }, { immediate: true })
</script>

<template>
  <div class="md">
    <template v-for="(p, i) in parts" :key="i">
      <!-- eslint-disable-next-line vue/no-v-html -- 源文已整体转义，这里不存在原始 HTML -->
      <div v-if="p.kind === 'md'" v-html="html(p.text)" />
      <!-- eslint-disable-next-line vue/no-v-html -- mermaid 自己生成的 svg，securityLevel: strict -->
      <div v-else-if="svgs[i]" class="mmd" v-html="svgs[i]" />
      <pre v-else class="mmd-raw" :title="bad[i] || '还在画…'"><code>{{ p.text.trim() }}</code></pre>
    </template>
  </div>
</template>
