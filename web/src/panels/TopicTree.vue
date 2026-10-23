<script setup>
// 专题树的一层（递归）：这一层的文件夹在前、专题在后；文件夹能折叠、能拖进别的文件夹，专题也能拖。
// 拖拽 / 右键只往上抛事件，真正的动作在 TopicsPanel（它认识接口）。
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const DRAG_TOPIC = 'application/x-knowrary-topic'
const DRAG_FOLDER = 'application/x-knowrary-topic-folder'

const props = defineProps({
  path: { type: String, default: '' },
  folders: { type: Array, default: () => [] },
  topics: { type: Array, default: () => [] },
  current: { type: String, default: '' },
  selected: { type: String, default: '' },
  collapsed: { type: Object, default: () => new Set() },
  depth: { type: Number, default: 0 },
})
const emit = defineEmits(['open', 'select', 'toggle', 'drop', 'menu'])

const parentOf = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '')
const baseName = (p) => p.slice(p.lastIndexOf('/') + 1)
const subFolders = computed(() => props.folders.filter((f) => parentOf(f) === props.path).sort())
const here = computed(() => props.topics.filter((t) => (t.folder || '') === props.path))
const countIn = (f) => props.topics.filter((t) => t.folder === f || (t.folder || '').startsWith(`${f}/`)).length
const indent = computed(() => ({ paddingLeft: `${props.depth * 14}px` }))

function dragStart(ev, kind, id) {
  ev.dataTransfer.setData(kind === 'topic' ? DRAG_TOPIC : DRAG_FOLDER, id)
  ev.dataTransfer.effectAllowed = 'move'
}

function canDrop(ev) {
  const types = [...(ev.dataTransfer?.types || [])]
  return types.includes(DRAG_TOPIC) || types.includes(DRAG_FOLDER)
}

/** 松手在某个文件夹上：把拖来的专题 / 文件夹放进去。 */
function onDrop(ev, folder) {
  const topic = ev.dataTransfer.getData(DRAG_TOPIC)
  const from = ev.dataTransfer.getData(DRAG_FOLDER)
  if (topic) emit('drop', { kind: 'topic', id: topic, to: folder })
  else if (from) emit('drop', { kind: 'folder', id: from, to: folder })
}

function detail(it) {
  if (it.center) return `中心：${it.name || it.center}${it.canvas ? '' : ' · 还没画'}`
  return '还没有中心点'
}
</script>

<template>
  <ul class="tl-list topic-tree">
    <li v-for="f in subFolders" :key="f">
      <div class="topic-folder" :class="{ on: selected === f }" :style="indent" draggable="true" :data-folder="f"
           @click="emit('select', f)" @dragstart="dragStart($event, 'folder', f)"
           @dragover="canDrop($event) && $event.preventDefault()" @drop.prevent.stop="onDrop($event, f)"
           @contextmenu.prevent="emit('menu', { kind: 'folder', id: f, x: $event.clientX, y: $event.clientY })">
        <button class="icon-btn ghost tiny" :title="collapsed.has(f) ? '展开' : '折叠'" @click.stop="emit('toggle', f)">
          <Icon :name="collapsed.has(f) ? 'chevronRight' : 'chevronDown'" :size="12" />
        </button>
        <Icon name="folder" :size="14" />
        <span class="tf-name">{{ baseName(f) }}</span>
        <span class="dim tf-count">{{ countIn(f) }}</span>
      </div>
      <TopicTree v-if="!collapsed.has(f)" :path="f" :folders="folders" :topics="topics" :current="current"
                 :selected="selected" :collapsed="collapsed" :depth="depth + 1"
                 @open="emit('open', $event)" @select="emit('select', $event)" @toggle="emit('toggle', $event)"
                 @drop="emit('drop', $event)" @menu="emit('menu', $event)" />
    </li>
    <li v-for="it in here" :key="it.topic" class="topic-row" :style="indent">
      <button class="tl-btn cmp-btn" :class="{ on: it.topic === current }" :data-topic="it.topic" draggable="true"
              @dragstart="dragStart($event, 'topic', it.topic)" @click="emit('open', it.topic)"
              @contextmenu.prevent="emit('menu', { kind: 'topic', id: it.topic, canvas: it.canvas, x: $event.clientX, y: $event.clientY })">
        <Icon :name="it.topic === current ? 'check' : 'pencil'" :size="14" />
        <span class="cmp-name">
          {{ it.topic }}
          <span class="sub">{{ detail(it) }}</span>
        </span>
      </button>
    </li>
  </ul>
</template>

<style scoped>
.topic-tree { margin: 0; padding: 0; }
.topic-folder {
  display: flex; align-items: center; gap: 5px; padding: 4px 6px; border-radius: 6px; cursor: pointer; font-size: 12.5px;
}
.topic-folder:hover { background: var(--surface-3); }
.topic-folder.on { background: var(--accent-soft); color: var(--accent-text); }
.tf-name { font-weight: 600; }
.tf-count { margin-left: auto; font-size: 11px; }
.topic-row { display: flex; align-items: center; gap: 4px; }
.topic-row .tl-btn { flex: 1; min-width: 0; }
.cmp-btn { align-items: flex-start; height: auto; padding-top: 7px; padding-bottom: 7px }
.cmp-name { display: flex; flex-direction: column; gap: 2px; text-align: left; line-height: 1.35 }
.cmp-name .sub { font-size: 10.5px; opacity: .62 }
</style>
