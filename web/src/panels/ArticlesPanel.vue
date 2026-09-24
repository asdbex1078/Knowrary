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
import { fetchArticle, fetchArticles } from '../api.js'

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

async function loadList() {
  error.value = ''
  try { list.value = await fetchArticles() } catch (err) { error.value = msgOf(err) }
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

    <!-- 阅读页 -->
    <div v-if="reading" ref="body" class="art-read scroll-thin">
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
      <p v-if="loading" class="dim">读取中…</p>
      <template v-else-if="list">
        <p class="dim art-list-head">原文目录 <code>{{ list.dir }}/</code> · {{ list.articles.length }} 篇（设置 → 知识库里能改）</p>
        <p v-if="!list.articles.length" class="dim">还没有原文。导入时勾着「保留原文」，原文就会存到这里。</p>
        <div v-for="a in list.articles" :key="a.path" class="art-row" :class="{ fresh: !a.nodes }">
          <button class="art-open" :title="a.path" @click="openArticle(a.path)">
            <Icon name="book" :size="13" />
            <span class="art-name">{{ a.title }}</span>
            <span v-if="a.nodes" class="chip m-mastered">{{ a.nodes }} 个点</span>
            <span v-else class="chip m-unbuilt">还没拆</span>
          </button>
          <button v-if="!a.nodes" class="btn tiny" data-act="import" title="打开导入面板，把这篇带过去（就地引用，不复制）"
                  @click="emit('import', a.path)">拆成知识点</button>
        </div>
      </template>
    </div>
  </Drawer>
</template>
