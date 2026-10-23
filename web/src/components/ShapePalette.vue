<script setup>
// 专题画布左边的图形栏（2026-10-13，照 draw.io）：图标按住拖到画布上，松手的点就是它的位置；
// 拖进大框里就挂在那个大框下面。下面的搜索框找已有知识点，搜到的也是拖进画布。
// 点一下图标也行（放到画面中间），但主路径是拖。
import { ref } from 'vue'
import Icon from '../ui/Icon.vue'
import { DRAG_NODE, DRAG_SHAPE, SHAPE_KINDS } from '../canvas/drawing.js'

const props = defineProps({
  topic: { type: String, default: '' },
  hits: { type: Array, default: () => [] },
  // 第 5 步：按关系算出的、还没上图的相关点（[{ key, name, items: [{ id, hop, via, why }] }]）
  related: { type: Array, default: () => [] },
  center: { type: String, default: '' },      // 专题中心；没有就不能出初稿
  depth: { type: Object, default: () => ({}) },
  names: { type: Object, default: () => ({}) },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['add', 'search', 'add-node', 'draft', 'depth', 'ai'])
const DEPTH_SIDES = [['up', '上'], ['down', '下'], ['left', '左'], ['right', '右']]
const relatedCount = () => props.related.reduce((n, g) => n + g.items.length, 0)

const q = ref('')

// 图标：都画在 36×28 的格子里，和画布上的样子一致
const ICON = {
  rect: '<rect x="3" y="5" width="30" height="18" rx="3"/>',
  frame: '<rect x="2" y="2" width="32" height="24" rx="4" stroke-dasharray="4 3"/><rect x="7" y="11" width="9" height="9" rx="1.5"/><rect x="19" y="11" width="9" height="9" rx="1.5"/>',
  text: '<text x="18" y="19" text-anchor="middle" font-size="13" stroke="none" fill="currentColor">Aa</text>',
  ellipse: '<ellipse cx="18" cy="14" rx="14" ry="11"/>',
  triangle: '<polygon points="4,25 18,3 32,25"/>',
  diamond: '<polygon points="3,14 18,3 33,14 18,25"/>',
  cylinder: '<path d="M7 7 A11 3.5 0 0 0 29 7 A11 3.5 0 0 0 7 7 Z M7 7 L7 21 A11 3.5 0 0 0 29 21 L29 7"/>',
}

function dragShape(ev, kind) {
  ev.dataTransfer.setData(DRAG_SHAPE, kind)
  ev.dataTransfer.effectAllowed = 'copy'
}
function dragNode(ev, id) {
  ev.dataTransfer.setData(DRAG_NODE, id)
  ev.dataTransfer.effectAllowed = 'copy'
}
function onSearch(v) {
  q.value = v
  emit('search', v)
}
</script>

<template>
  <aside class="shape-palette" data-palette>
    <div class="pal-head"><Icon name="pencil" :size="13" /><span>{{ topic }}</span>
      <button class="btn tiny pal-ai" data-act="open-topic-ai" title="审这张图 / 问 AI 该画什么（点了才调模型）"
              @click="emit('ai')"><Icon name="eye" :size="12" />AI</button>
    </div>
    <div class="pal-sub">图形 · 拖到画布上</div>
    <div class="pal-grid">
      <button v-for="k in SHAPE_KINDS" :key="k.kind" class="pal-item" draggable="true" :data-palette-shape="k.kind"
              :title="`${k.name}：拖到画布上（点一下放到画面中间）`"
              @dragstart="dragShape($event, k.kind)" @click="emit('add', k.kind)">
        <svg viewBox="0 0 36 28" width="36" height="28" fill="none" stroke="currentColor" stroke-width="1.6"
             v-html="ICON[k.kind]" />
        <span>{{ k.name }}</span>
      </button>
    </div>
    <div class="pal-sub">知识点 · 搜到拖进来</div>
    <input :value="q" class="pal-search" data-palette-search placeholder="搜知识点…" spellcheck="false"
           @input="onSearch($event.target.value)" @keydown.esc="onSearch('')" />
    <ul v-if="q && hits.length" class="pal-hits scroll-thin">
      <li v-for="h in hits" :key="h.id" draggable="true" :data-palette-node="h.id" :title="h.desc || ''"
          @dragstart="dragNode($event, h.id)" @dblclick="emit('add-node', h.id)">
        <span class="nm">{{ h.name || h.id }}</span><span class="fd">{{ h.field || '' }}</span>
      </li>
    </ul>
    <p v-else-if="q" class="pal-none">没搜到</p>

    <div class="pal-sub">按关系 · 还没上图的相关点</div>
    <template v-if="center">
      <button class="btn tiny primary" data-act="topic-draft" :disabled="busy || !relatedCount()"
              title="按已有关系摆一版：中心大框 + 部件方块，上下按跳数一行行往外，左右是演化链；已经摆好的不动，⌘Z 撤销"
              @click="emit('draft')">一键初稿（{{ relatedCount() }} 个点）</button>
      <div class="pal-depth" title="每个方向沿关系走几跳（这张图自己记着）">
        <label v-for="[k, label] in DEPTH_SIDES" :key="k">{{ label }}
          <input type="number" min="0" max="10" :value="depth[k] ?? 3" :data-depth="k"
                 @change="emit('depth', { ...depth, [k]: Math.max(0, Math.min(10, Number($event.target.value) || 0)) })" />
        </label>
      </div>
      <div v-for="g in related" :key="g.key" class="pal-group">
        <div class="pal-group-head">{{ g.name }} <span class="dim">{{ g.items.length }}</span></div>
        <ul class="pal-hits">
          <li v-for="it in g.items" :key="it.id" draggable="true" :data-palette-node="it.id" :data-related="g.key"
              :title="it.why ? `${it.why}（经 ${names[it.via] || it.via}）` : `第 ${it.hop} 跳，经 ${names[it.via] || it.via}`"
              @dragstart="dragNode($event, it.id)" @dblclick="emit('add-node', it.id)">
            <span class="nm">{{ names[it.id] || it.id }}</span><span class="fd">{{ it.why ? '' : `${it.hop} 跳` }}</span>
          </li>
        </ul>
      </div>
      <p v-if="!relatedCount()" class="pal-none">关系能连到的点都在图上了</p>
    </template>
    <p v-else class="pal-none">先把一个大框右键升级成知识点，它就是这个专题的中心；之后这里按关系列出相关的点</p>
    <p class="pal-tip">右键图形：连线、换形状、删除<br>双击图形：改字</p>
  </aside>
</template>
