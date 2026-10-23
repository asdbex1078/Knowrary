<script setup>
/**
 * 原文（原文层第 4 步）：读原文目录里的长文，看它拆出了哪些点。
 *
 * **只读**：原文是快照，写完不和节点同步（之后以节点为准）。要改原文走 Obsidian。
 * 两种状态：列表（哪几篇、各拆出几个点、哪几篇还没拆）和一篇的阅读页。
 *
 * 阅读页按标题**自己切段**再逐段交给 Markdown 渲染，而不是整篇丢给它：
 * 每节标题旁要挂「出自这一节的点」（Vue 组件，塞不进 marked 吐出来的 HTML），
 * 标题还要当锚点——从检查器的来源链接、从点上跳过来，直接滚到那一节。
 *
 * 打开一篇时把它拆出的点报给 App（`highlight`），画布上把它们点亮；关掉 / 回到列表时清掉。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import Markdown from '../ui/Markdown.vue'
import ModelsExtract from '../components/ModelsExtract.vue'
import { fetchArticle, fetchArticles, fetchWorks } from '../api.js'

const props = defineProps({
  // 从别处要求打开的一篇：{ path, section, nonce }。nonce 变一次开一次（同一篇再点一次也要滚过去）
  open: { type: Object, default: null },
})
const emit = defineEmits(['close', 'goto', 'import', 'highlight'])

const list = ref(null)            // { dir, articles }
const reading = ref(null)         // ArticleRead
const loading = ref(false)
const error = ref('')
const body = ref(null)            // 阅读页的滚动容器
const extracting = ref(null)      // 抽成模型谱系：[{ path, title }]，有值时整块面板换成抽取流程

/** 列表按原文目录下的第一级子目录分组（`llm/deepseek/x.md` 归 `llm`），给「整组一起抽」用。 */
const groups = computed(() => {
  const dir = list.value?.dir || ''
  const out = new Map()
  for (const a of list.value?.articles || []) {
    const rel = a.path.startsWith(dir + '/') ? a.path.slice(dir.length + 1) : a.path
    const key = rel.includes('/') ? rel.split('/')[0] : ''
    if (!out.has(key)) out.set(key, [])
    out.get(key).push(a)
  }
  return [...out.entries()].map(([name, articles]) => ({ name, articles }))
    .sort((x, y) => (x.name === '') - (y.name === '') || x.name.localeCompare(y.name))
})

function extract(articles) { extracting.value = articles.map((a) => ({ path: a.path, title: a.title })) }
function endExtract() { extracting.value = null; if (!reading.value) loadList() }

async function loadList() {
  error.value = ''
  try { list.value = await fetchArticles() } catch (err) { error.value = msgOf(err) }
}

// —— 成品页签（成品层第 4 步，2026-10-22）——
// 成品是交互式 HTML 深度文档：不在应用内读（新标签页打开原页面），这里只列清单和覆盖情况。
const tab = ref('原文')
const works = ref(null)           // { dir, works }
async function loadWorks() {
  error.value = ''
  try { works.value = await fetchWorks() } catch (err) { error.value = msgOf(err) }
}
watch(tab, (t) => { if (t === '成品') loadWorks() })
const workUrl = (path, section = '') =>
  `/api/work?path=${encodeURIComponent(path)}${section ? '#' + encodeURIComponent(section) : ''}`
const coveredOf = (w) => {
  const seen = new Map()
  for (const r of w.refs) if (!seen.has(r.id)) seen.set(r.id, r)
  return [...seen.values()]
}

async function openArticle(path, section = '') {
  loading.value = true; error.value = ''
  try {
    reading.value = await fetchArticle(path)
    emit('highlight', [...new Set(reading.value.nodes.map((n) => n.id))])
    await nextTick()
    scrollTo(section)
  } catch (err) {
    error.value = msgOf(err)
  } finally {
    loading.value = false
  }
}

function back() {
  reading.value = null
  emit('highlight', [])
  loadList()
}

function close() {
  emit('highlight', [])
  emit('close')
}

function msgOf(err) { return err?.body?.detail?.message || err?.body?.detail || err?.message || '读不到' }

onMounted(() => { if (!props.open?.path) loadList() })
// 被别的面板顶掉（活动栏上点了别的）也要把画布上的高亮收回来
onBeforeUnmount(() => emit('highlight', []))
watch(() => props.open?.nonce, () => { if (props.open?.path) openArticle(props.open.path, props.open.section) },
      { immediate: true })

// —— 按标题切段（跳过 fenced code 里的 #）——
const RE_HEAD = /^(#{1,6})\s+(.+?)\s*#*\s*$/
const RE_FENCE = /^\s*(```|~~~)/

const blocks = computed(() => {
  const text = reading.value?.text || ''
  const out = [{ level: 0, title: '', lines: [] }]
  let fence = null
  for (const ln of text.split('\n')) {
    const f = ln.match(RE_FENCE)
    if (f) fence = fence === f[1] ? null : (fence || f[1])
    const h = !fence && !f && ln.match(RE_HEAD)
    if (h) out.push({ level: h[1].length, title: h[2].trim(), lines: [] })
    else out[out.length - 1].lines.push(ln)
  }
  return out.map((b, i) => ({ ...b, id: `art-sec-${i}`, text: b.lines.join('\n') }))
    .filter((b) => b.level || b.text.trim())
})

// 标题比较：去掉空白和标点再比，和服务端 find_heading 同一个口径
const norm = (s) => String(s || '').replace(/[\s\p{P}\p{S}]+/gu, '').toLowerCase()

/** 出自某一节的点。链整篇的（section 为空）不挂在任何一节上，只进顶上总表。 */
function nodesOf(title) {
  const key = norm(title)
  return (reading.value?.nodes || []).filter((n) => n.section && norm(n.section) === key)
}

/** 顶上总表：去重；每个点标上它链的是哪一节（链整篇的写「整篇」）。 */
const summary = computed(() => {
  const seen = new Map()
  for (const n of reading.value?.nodes || []) {
    const row = seen.get(n.id) || { id: n.id, name: n.name, sections: [] }
    row.sections.push(n.section || '整篇')
    seen.set(n.id, row)
  }
  return [...seen.values()]
})

function scrollTo(section) {
  if (!section || !body.value) return
  const key = norm(section)
  const hit = blocks.value.find((b) => b.level && norm(b.title) === key)
  const el = hit && body.value.querySelector(`#${hit.id}`)
  if (el) {
    el.scrollIntoView({ block: 'start' })
    el.classList.add('art-flash')
    setTimeout(() => el.classList.remove('art-flash'), 1600)
  }
}

const HEAD_TAG = (level) => `h${Math.min(Math.max(level, 1), 6)}`
</script>

<template>
  <Drawer side="left" title="原文" icon="book" storage-key="articles" :default-width="480" :max="960" expandable
          @close="close">
    <template #head-actions>
      <button v-if="reading" class="icon-btn ghost tiny" title="回到原文列表" data-act="back" @click="back">
        <Icon name="arrowLeft" :size="14" />
      </button>
    </template>

    <p v-if="error" class="imp-error">{{ error }}</p>

    <!-- 抽成模型谱系：就地走完指定目录 → 抽 → 预览 → 核对 → 写入 -->
    <div v-if="extracting" class="art-list scroll-thin">
      <ModelsExtract :articles="extracting" @close="endExtract" />
    </div>

    <!-- 阅读页 -->
    <div v-else-if="reading" ref="body" class="art-read scroll-thin">
      <header class="art-head">
        <h2 class="art-title">{{ reading.title }}</h2>
        <div class="art-meta dim">
          <code>{{ reading.path }}</code>
          <span v-if="reading.imported"> · 导入于 {{ reading.imported }}</span>
          <span v-if="reading.origin"> · 来自 {{ reading.origin }}</span>
        </div>
        <p v-if="!reading.in_dir" class="art-note warn">这篇不在原文目录里（旧年代散放的位置），有节点链着它才读得到。</p>
        <template v-if="summary.length">
          <p class="art-note">已拆成 <b>{{ summary.length }}</b> 个点，之后以节点为准——原文是当时的样子，不会跟着节点改。</p>
          <ul class="art-summary">
            <li v-for="n in summary" :key="n.id">
              <button class="art-chip" :title="`在图上定位 ${n.id}`" @click="emit('goto', n.id)">{{ n.name }}</button>
              <span class="dim">{{ n.sections.join('、') }}</span>
            </li>
          </ul>
        </template>
        <div v-else class="art-note">
          还没拆：没有节点链着这篇。
          <button class="btn primary tiny" data-act="import" @click="emit('import', reading.path)">
            <Icon name="search" :size="12" />拆成知识点
          </button>
        </div>
        <p class="art-note">讲的是一串型号的迭代？
          <button class="btn tiny" data-act="extract-models" title="把这篇里的型号抽进模型目录（预览后再写）"
                  @click="extract([reading])"><Icon name="timeline" :size="12" />抽成谱系</button></p>
      </header>

      <section v-for="b in blocks" :key="b.id" class="art-sec">
        <component :is="HEAD_TAG(b.level)" v-if="b.level" :id="b.id" class="art-h">
          {{ b.title }}
          <span v-if="nodesOf(b.title).length" class="art-h-nodes">
            <button v-for="n in nodesOf(b.title)" :key="n.id" class="art-chip" :title="`出自这一节：${n.id}，点一下在图上定位`"
                    @click="emit('goto', n.id)">{{ n.name }}</button>
          </span>
        </component>
        <Markdown v-if="b.text.trim()" :text="b.text" />
      </section>
    </div>

    <!-- 列表 -->
    <div v-else class="art-list scroll-thin">
      <div class="seg small art-tabs">
        <button :class="{ on: tab === '原文' }" data-tab="原文" @click="tab = '原文'">原文</button>
        <button :class="{ on: tab === '成品' }" data-tab="成品" @click="tab = '成品'">成品</button>
      </div>

      <!-- 成品：交互式 HTML 深度文档。点开是新标签页的原页面，不在这儿读 -->
      <template v-if="tab === '成品'">
        <template v-if="works">
          <p class="dim art-list-head">成品目录 <code>{{ works.dir }}/</code> · {{ works.works.length }} 个
            （设置 → 知识库里能改）。<b>成品要你自己整理进这个目录</b>——库不自动同步别的仓库里的活文件。</p>
          <p v-if="!works.works.length" class="dim">
            还没有成品。把在别处让模型产出的 HTML 深度文档（讲解页 / 实验台 / 演示器）拷进
            <code>{{ works.dir }}/</code>，这里就会列出来；再「拆成知识点」，它就进了图谱和复习闭环。</p>
          <template v-for="w in works.works" :key="w.path">
            <div class="art-row" :class="{ fresh: !w.refs.length }" :data-work="w.path">
              <a class="art-open" :href="workUrl(w.path)" target="_blank" rel="noopener"
                 :title="`新标签页打开 ${w.path}`">
                <Icon name="play" :size="13" />
                <span class="art-name">{{ w.title }}</span>
                <span v-if="w.refs.length" class="chip m-mastered">{{ coveredOf(w).length }} 个点</span>
                <span v-else class="chip m-unbuilt">待导入</span>
              </a>
              <button v-if="!w.refs.length" class="btn tiny" data-act="import"
                      title="打开导入面板，把这个成品带过去（成品即原文，不存副本）"
                      @click="emit('import', w.path)">拆成知识点</button>
            </div>
            <div v-if="w.refs.length" class="art-work-refs">
              <button v-for="n in coveredOf(w)" :key="n.id" class="art-chip"
                      :title="`在图上定位 ${n.id}`" @click="emit('goto', n.id)">{{ n.name }}</button>
            </div>
          </template>
        </template>
        <p v-else class="dim">读取中…</p>
      </template>

      <p v-else-if="loading" class="dim">读取中…</p>
      <template v-else-if="list">
        <p class="dim art-list-head">原文目录 <code>{{ list.dir }}/</code> · {{ list.articles.length }} 篇（设置 → 知识库里能改）</p>
        <p v-if="!list.articles.length" class="dim">还没有原文。导入时勾着「保留原文」，原文就会存到这里。</p>
        <section v-for="g in groups" :key="g.name" class="art-group" :data-group="g.name">
          <div v-if="g.name" class="art-group-head">
            <Icon name="folder" :size="13" /><b>{{ g.name }}/</b><span class="dim">{{ g.articles.length }} 篇</span>
            <button class="btn tiny" style="margin-left: auto" data-act="extract-models-all"
                    :title="`把 ${g.name}/ 下这 ${g.articles.length} 篇一篇一篇抽进模型目录，最后一起预览`"
                    @click="extract(g.articles)"><Icon name="timeline" :size="12" />这 {{ g.articles.length }} 篇一起抽成谱系</button>
          </div>
          <div v-for="a in g.articles" :key="a.path" class="art-row" :class="{ fresh: !a.nodes }" :data-article="a.path">
            <button class="art-open" :title="a.path" @click="openArticle(a.path)">
              <Icon name="book" :size="13" />
              <span class="art-name">{{ a.title }}</span>
              <span v-if="a.nodes" class="chip m-mastered">{{ a.nodes }} 个点</span>
              <span v-else class="chip m-unbuilt">还没拆</span>
            </button>
            <button v-if="!a.nodes" class="btn tiny" data-act="import" title="打开导入面板，把这篇带过去（就地引用，不复制）"
                    @click="emit('import', a.path)">拆成知识点</button>
            <button class="btn subtle tiny" data-act="extract-models" title="把这篇里的型号抽进模型目录（预览后再写）"
                    @click="extract([a])">抽成谱系</button>
          </div>
        </section>
      </template>
    </div>
  </Drawer>
</template>
