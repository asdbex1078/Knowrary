<script setup>
/**
 * 模型谱系（2026-09-30，模型谱系第 3 步）：谱系视图里「知识演化 / 模型谱系」切到后者时盖在画布上。
 *
 * 一个家族一条泳道，按发布顺序等距排开；点一样大，参数写在名字旁，名字一上一下交错。
 * 同家族实线连走向（人写的「前代」和按时间补的），跨家族的「基于」画虚线。
 * 悬停出卡片，点一下钉住（才点得到里面的技术点和出处）；Esc 或点空白处放开。
 * 滚轮缩放、拖空白处平移，进来默认铺满一屏宽。左边的家族名跟着上下平移、不跟着左右走。
 *
 * 筛选三样：开源 / 闭源、收起某个家族（点左边的家族名）、按技术点高亮（用到它的亮，其它变淡）。
 * 没指定模型目录 / 目录里还没有家族文件时是引导页：选目录、看格式样例、去原文面板抽。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { COL, LEFT, computeLanes, techOptions } from '../canvas/modelLanes.js'
import Icon from '../ui/Icon.vue'
import ModelCard from './ModelCard.vue'
import ModelsDirPick from './ModelsDirPick.vue'

const props = defineProps({
  doc: { type: Object, default: null },          // /api/models 的结果
  focus: { type: Object, default: null },        // { id, nonce }：从检查器点过来的型号，钉住并挪到视野中间
})
const emit = defineEmits(['goto', 'article', 'saved-dir', 'refresh', 'open-articles'])

const PALETTE = ['#4f7bd9', '#d9774f', '#4fa37a', '#9a5fc8', '#c9a227', '#d45c8a', '#3fa3b8', '#7a8a4f']
const KEY = 'knowrary.modelLineage'
const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} } })()
const filters = reactive({ open: saved.open || 'all', hidden: new Set(saved.hidden || []), tech: saved.tech || '' })
watch(() => [filters.open, [...filters.hidden], filters.tech], () => {
  try { localStorage.setItem(KEY, JSON.stringify({ open: filters.open, hidden: [...filters.hidden], tech: filters.tech })) } catch { /* 存不上不要紧 */ }
})

const box = ref(null)
const view = reactive({ scale: 1, tx: 0, ty: 0 })
const hover = ref(null)
const pinned = ref(null)
const mouse = reactive({ x: 0, y: 0 })
const changingDir = ref(false)
const showWarnings = ref(false)

const ready = computed(() => props.doc?.configured && props.doc.families.length)
const plan = computed(() => computeLanes(props.doc, { open: filters.open, hidden: filters.hidden, tech: filters.tech || null }))
const techs = computed(() => techOptions(props.doc))
const colorOf = computed(() => new Map((props.doc?.families || []).map((f, i) => [f.id, PALETTE[i % PALETTE.length]])))
const card = computed(() => pinned.value || hover.value)
const cardStyle = computed(() => ({ left: `${Math.min(mouse.x + 16, (box.value?.clientWidth || 800) - 330)}px`,
                                    top: `${Math.max(8, Math.min(mouse.y + 12, (box.value?.clientHeight || 600) - 360))}px` }))

function fit() {
  const w = box.value?.clientWidth || 1000
  view.scale = Math.max(0.35, Math.min(1, (w - 8) / Math.max(plan.value.width, 1)))
  view.tx = 0; view.ty = 0
}
watch(ready, (ok) => { if (ok) nextTick(() => { fit(); showFocus() }) })
watch(() => props.focus?.nonce, () => nextTick(showFocus))
// 目录重读回来之后再试一次：刚挪进来的型号，第一次找的时候可能还不在旧数据里
let shown = null
watch(() => props.doc, () => { if (props.focus && shown !== props.focus.nonce) nextTick(showFocus) })

/** 钉住从外面点过来的那个型号；它所在的家族收起着就先展开，再把它挪到视野中间。 */
function showFocus() {
  const id = props.focus?.id
  if (!id || !ready.value) return
  const fam = id.split('/')[0]
  if (filters.hidden.has(fam)) toggleFamily(fam)
  if (filters.open !== 'all') filters.open = 'all'
  nextTick(() => {
    const it = plan.value.items.find((x) => x.id === id)
    if (!it || !box.value) return
    pinned.value = it
    shown = props.focus.nonce
    view.tx = box.value.clientWidth / 2 - it.x * view.scale
    view.ty = box.value.clientHeight / 2 - it.y * view.scale
    mouse.x = box.value.clientWidth / 2; mouse.y = box.value.clientHeight / 2
  })
}
onMounted(() => { if (ready.value) nextTick(() => { fit(); showFocus() }); document.addEventListener('keydown', onKey) })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))
function onKey(ev) { if (ev.key === 'Escape' && pinned.value) { ev.stopPropagation(); pinned.value = null } }

function onWheel(ev) {
  const rect = box.value.getBoundingClientRect()
  const px = ev.clientX - rect.left
  const py = ev.clientY - rect.top
  const next = Math.max(0.3, Math.min(3, view.scale * (ev.deltaY < 0 ? 1.1 : 1 / 1.1)))
  view.tx = px - (px - view.tx) * (next / view.scale)
  view.ty = py - (py - view.ty) * (next / view.scale)
  view.scale = next
}

let drag = null
function onDown(ev) {
  if (ev.button !== 0) return
  drag = { x: ev.clientX, y: ev.clientY, tx: view.tx, ty: view.ty, moved: false }
}
function onMove(ev) {
  const rect = box.value.getBoundingClientRect()
  mouse.x = ev.clientX - rect.left; mouse.y = ev.clientY - rect.top
  if (!drag) return
  const dx = ev.clientX - drag.x
  const dy = ev.clientY - drag.y
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true
  view.tx = drag.tx + dx; view.ty = drag.ty + dy
}
function onUp() {
  if (drag && !drag.moved) pinned.value = null       // 点空白处：放开钉住的卡片
  drag = null
}
function pin(it) { pinned.value = pinned.value?.id === it.id ? null : it }
function toggleFamily(id) {
  const next = new Set(filters.hidden)
  if (next.has(id)) next.delete(id); else next.add(id)
  filters.hidden = next
}
function onDir(read) { changingDir.value = false; emit('saved-dir', read) }
</script>

<template>
  <div class="model-lineage" data-model-lineage>
    <!-- 引导：没指定目录 / 目录里还没有家族文件 -->
    <div v-if="!doc" class="ml-empty dim">读取中…</div>
    <div v-else-if="!ready || changingDir" class="ml-empty" data-ml-guide>
      <h3>模型谱系</h3>
      <p v-if="!doc.configured" class="dim">还没有指定模型目录。把一个家族的型号写在一个 md 里（一个型号一节），
        这里按家族铺成泳道：谁接着谁、参数多大、用了什么技术、突破在哪。</p>
      <p v-else-if="!changingDir" class="dim">目录 <code>{{ doc.dir }}/</code> 里还没有家族文件（frontmatter 写了
        <code>kind: family</code> 的 md）。</p>
      <ModelsDirPick :candidates="doc.candidates" :sample="doc.sample" :current="doc.dir" @saved="onDir" />
      <p class="ml-empty-tip">也可以先去「原文」把讲模型迭代的文章抽成谱系：
        <button class="btn tiny" data-act="open-articles" @click="emit('open-articles')">
          <Icon name="book" :size="12" />打开原文</button>
        <button v-if="changingDir" class="btn subtle tiny" @click="changingDir = false">不换了</button></p>
    </div>

    <template v-else>
      <div class="ml-bar">
        <div class="seg" role="tablist">
          <button v-for="[k, label] in [['all', '全部'], ['open', '开源'], ['closed', '闭源']]" :key="k"
                  :class="{ on: filters.open === k }" :data-open="k" @click="filters.open = k">{{ label }}</button>
        </div>
        <select v-model="filters.tech" data-input="ml-tech" title="按技术点高亮：用到它的型号亮，其它变淡">
          <option value="">按技术点高亮…</option>
          <option v-for="t in techs" :key="t.key" :value="t.key">{{ t.label }}（{{ t.count }}）</option>
        </select>
        <span class="dim">{{ plan.items.length }} 个型号 · {{ plan.lanes.length }} 个家族 · <code>{{ doc.dir }}/</code></span>
        <button v-if="doc.warnings.length" class="btn subtle tiny" data-act="ml-warnings"
                @click="showWarnings = !showWarnings">⚠ {{ doc.warnings.length }} 处格式问题</button>
        <span style="margin-left: auto" />
        <button class="btn subtle tiny" title="重新读一遍家族文件（在 Obsidian 里改过之后）" data-act="ml-refresh"
                @click="emit('refresh')"><Icon name="refresh" :size="12" />重新读取</button>
        <button class="btn subtle tiny" data-act="ml-change-dir" @click="changingDir = true">换目录</button>
        <button class="btn subtle tiny" title="铺满一屏宽" @click="fit"><Icon name="fit" :size="12" /></button>
      </div>
      <ul v-if="showWarnings" class="ml-warnings">
        <li v-for="(w, i) in doc.warnings" :key="i"><code>{{ w.path }}:{{ w.line }}</code> {{ w.message }}</li>
      </ul>

      <div ref="box" class="ml-stage" @wheel.prevent="onWheel" @mousedown="onDown" @mousemove="onMove"
           @mouseup="onUp" @mouseleave="drag = null; hover = null">
        <svg class="ml-svg" :width="'100%'" :height="'100%'">
          <g :transform="`translate(${view.tx},${view.ty}) scale(${view.scale})`">
            <rect v-for="(lane, i) in plan.lanes" :key="'bg' + lane.family.id" :x="0" :y="lane.y" :height="lane.h"
                  :width="plan.width" :class="['ml-lane', i % 2 ? 'odd' : 'even']" />
            <g v-for="lane in plan.lanes.filter((l) => !l.folded)" :key="'t' + lane.family.id">
              <line :x1="LEFT" :x2="LEFT + lane.family.models.length * COL" :y1="lane.y + lane.h / 2" :y2="lane.y + lane.h / 2"
                    class="ml-axis" />
              <text v-for="t in lane.ticks" :key="t.year" :x="t.x - COL / 2 + 4" :y="lane.y + 11" class="ml-year">{{ t.year }}</text>
            </g>
            <path v-for="l in plan.links" :key="l.source + l.target + l.kind" :d="l.d"
                  :class="['ml-link', l.kind]" :stroke="colorOf.get(l.source.split('/')[0])" />
            <g v-for="it in plan.items" :key="it.id" class="ml-item" :class="{ dim: it.dim, on: card?.id === it.id }"
               :data-model="it.model.name" @mouseenter="hover = it" @mouseleave="hover = null"
               @mousedown.stop @click.stop="pin(it)">
              <circle :cx="it.x" :cy="it.y" r="6" :fill="colorOf.get(it.family.id)" />
              <text :x="it.x" :y="it.labelUp ? it.y - 13 : it.y + 22" text-anchor="middle" class="ml-label">{{ it.label }}</text>
            </g>
          </g>
        </svg>
        <!-- 家族名一栏：跟着上下平移，不跟着左右走 -->
        <div class="ml-names" :style="{ transform: `translateY(${view.ty}px)` }">
          <button v-for="lane in plan.lanes" :key="lane.family.id" class="ml-name" :class="{ folded: lane.folded }"
                  :style="{ top: `${lane.y * view.scale}px`, height: `${lane.h * view.scale}px` }"
                  :data-family="lane.family.id" :title="lane.folded ? '展开这个家族' : '收起这个家族'"
                  @mousedown.stop @click="toggleFamily(lane.family.id)">
            <i :style="{ background: colorOf.get(lane.family.id) }" />
            <b>{{ lane.family.name }}</b>
            <span class="dim">{{ lane.family.models.length }}<template v-if="lane.family.open != null"> · {{ lane.family.open ? '开源' : '闭源' }}</template></span>
          </button>
        </div>
        <ModelCard v-if="card" :item="card" :pinned="!!pinned" :style="cardStyle"
                   @goto="emit('goto', $event)" @article="emit('article', $event)" @close="pinned = null" />
      </div>
    </template>
  </div>
</template>
