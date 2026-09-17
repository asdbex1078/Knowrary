<script setup>
/**
 * 把模型回的一段话渲染成图文：Markdown（标题、列表、表格、代码）+ ```mermaid 画的图。
 *
 * **先转义再解析**：源文里的 `<` `>` 一律变实体，所以模型写什么都不会变成真 HTML，
 * 也就没有 XSS 那条路——比引一个消毒库更彻底，代价只是"模型不能写 HTML"，
 * 而它本来也不该写。
 *
 * mermaid 两三兆，**按需加载**：不画图的那些对话一个字节都不下（实测首屏 972 KB、
 * 第一张图才多下 2.2 MB，之后每张图 15~140 ms）。
 *
 * **图按源文缓存，不按段序号**：流式时每来一个增量整条消息的 `text` 都会重新赋值，
 * 按序号缓存就得整条清空重画——已经画好的图会消失一下再出现（闪），而且每张图
 * 15~140 ms 全是白烧的。claude-cli 是块级增量所以只闪几下，换成逐 token 的
 * provider（anthropic / openai）就是几百次重画，直接卡死。
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
    // key = 这张图的源文本身：源文没变就不用重画（流式时这是最要紧的一点）
    out.push({ kind: 'mermaid', text: m[1], key: m[1].trim() })
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ kind: 'md', text: src.slice(last) })
  return out.filter((p) => p.text.trim())
})

const html = (text) => marked.parse(escape(text))

// ---- mermaid：懒加载 + 每段一块 svg ----

// 引擎、主题、id 计数器都是**模块级单例**：这个组件每条消息一份，
// 挂在组件上等于开几十个 observer、几十份引擎句柄。
let engine = null
let engineTheme = ''
let seq = 0

const themeOf = () => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default')
const themeNow = ref(themeOf())
if (typeof MutationObserver !== 'undefined') {
  // 主题切在 <html data-theme> 上（App.vue 的 applyThemeNow）。不盯着它的话，引擎只在
  // 第一次画图那一刻定死主题——之后切到深色，新画的图仍然是浅色那张惨白的。
  new MutationObserver(() => { themeNow.value = themeOf() })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
}

const svgs = ref({})       // { 图的源文: svg 字符串 }
const bad = ref({})        // 画不出来的那些：退回代码块，别让整条消息空掉
const doing = new Set()    // 正在画的：流式时 draw() 会被连着叫好几次，别把同一张图画两遍

async function boot() {
  if (!engine) {
    engine = (await import('mermaid')).default
  }
  if (engineTheme !== themeNow.value) {
    engine.initialize({ startOnLoad: false, securityLevel: 'strict', theme: themeNow.value })
    engineTheme = themeNow.value
  }
  return engine
}

async function draw() {
  const jobs = parts.value.filter((p) => p.kind === 'mermaid')
  if (!jobs.length) return
  let eng
  try {
    eng = await boot()
  } catch (err) {
    // 引擎都没拉下来（chunk 404、刷新时请求被打断…）。**必须记一笔**：
    // 不记的话这几张图会永远停在"还在画…"，屏幕上看不出是坏了还是慢。
    const why = `mermaid 没加载起来：${err?.message || err}`
    bad.value = { ...bad.value, ...Object.fromEntries(jobs.map((p) => [p.key, why])) }
    return
  }
  for (const p of jobs) {
    if (svgs.value[p.key] || bad.value[p.key] || doing.has(p.key)) continue
    doing.add(p.key)
    let timer = 0
    try {
      // **画不出来要有个头**：按源文缓存之后，一张图只有一次机会（源文不变就不再重画），
      // 所以 render 万一卡住不 resolve，这张图会永远停在"还在画…"——屏幕上分不清是坏了还是慢。
      const svg = await Promise.race([
        eng.render(`kg-mmd-${seq++}`, p.key).then((r) => r.svg),
        new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('画了 10 秒还没出来')), 10000) }),
      ])
      svgs.value = { ...svgs.value, [p.key]: svg }
    } catch (err) {
      // 流式时图往往只到一半，画不出来很正常；写完之后源文变了就是另一个 key，自然会再试
      bad.value = { ...bad.value, [p.key]: String(err?.message || err) }
    } finally {
      clearTimeout(timer)
      doing.delete(p.key)
    }
  }
}

/** 只留这条消息里还在的那几张图。流式时每个中间态都会留下一条"画失败"的记录，不清会一直涨。 */
function prune() {
  const live = new Set(parts.value.filter((p) => p.kind === 'mermaid').map((p) => p.key))
  const keep = (m) => Object.fromEntries(Object.entries(m).filter(([k]) => live.has(k)))
  svgs.value = keep(svgs.value)
  bad.value = keep(bad.value)
}

watch(() => props.text, () => { prune(); draw() }, { immediate: true })
// 换主题要整条重画：mermaid 把配色烤进 svg 里，不重画就一直是旧主题那张
watch(themeNow, () => { svgs.value = {}; bad.value = {}; draw() })
</script>

<template>
  <div class="md">
    <template v-for="(p, i) in parts" :key="i">
      <!-- eslint-disable-next-line vue/no-v-html -- 源文已整体转义，这里不存在原始 HTML -->
      <div v-if="p.kind === 'md'" v-html="html(p.text)" />
      <!-- eslint-disable-next-line vue/no-v-html -- mermaid 自己生成的 svg，securityLevel: strict -->
      <div v-else-if="svgs[p.key]" class="mmd" v-html="svgs[p.key]" />
      <pre v-else class="mmd-raw" :title="bad[p.key] || '还在画…'"><code>{{ p.key }}</code></pre>
    </template>
  </div>
</template>
