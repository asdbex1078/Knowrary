<script setup>
// 专题目录（2026-10-13）+ 文件夹（2026-10-14）：一个专题一张自己画的画布，专题可以放进文件夹，文件夹套文件夹。
//
// 目录 = 建了画布的 ∪ 有中心点在 frontmatter 里写了 `专题:` 的，服务端现算；文件夹是 .knowrary/topics/ 下的真目录。
// 挪位置：拖到文件夹上，或右键「移到…」。专题名全库唯一，挪了不改名，md 一个字不动。
// 真正的动作（调接口、刷新、提示）在 App.vue：这里只抛 `op`。
import { computed, ref } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import ContextMenu from '../components/ContextMenu.vue'
import TopicTree from './TopicTree.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  folders: { type: Array, default: () => [] },
  current: { type: String, default: '' },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'open', 'create', 'op', 'refresh'])

const KEY = 'knowrary.topics.collapsed'
const draft = ref('')
const draftEl = ref(null)
const selected = ref('')           // 新专题 / 新文件夹建在哪个文件夹里（空 = 顶层）
const collapsed = ref(new Set(load()))
const menu = ref(null)

function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] }
}
function toggle(f) {
  const next = new Set(collapsed.value)
  next.has(f) ? next.delete(f) : next.add(f)
  collapsed.value = next
  try { localStorage.setItem(KEY, JSON.stringify([...next])) } catch { /* 记不住折叠状态也不影响用 */ }
}
function select(f) {
  selected.value = selected.value === f ? '' : f
}

function create() {
  const name = draft.value.trim()
  if (!name) return
  emit('create', { topic: name, folder: selected.value })
  draft.value = ''
}

function newFolder(parent = selected.value) {
  const name = window.prompt(`在「${parent || '顶层'}」里新建文件夹，名字：`, '')
  if (name?.trim()) emit('op', { op: 'make-folder', parent, name: name.trim() })
}

/** 拖进文件夹（`to` 空串 = 顶层）。 */
function onDrop({ kind, id, to }) {
  if (kind === 'topic') emit('op', { op: 'move-topic', topic: id, folder: to })
  else emit('op', { op: 'move-folder', path: id, to })
}

function onRootDrop(ev) {
  const topic = ev.dataTransfer.getData('application/x-knowrary-topic')
  const folder = ev.dataTransfer.getData('application/x-knowrary-topic-folder')
  if (topic || folder) onDrop({ kind: topic ? 'topic' : 'folder', id: topic || folder, to: '' })
}

const empty = (f) => !props.items.some((t) => t.folder === f || (t.folder || '').startsWith(`${f}/`))
  && !props.folders.some((x) => x.startsWith(`${f}/`))

/** 「移到…」的目标：顶层 + 每个文件夹；文件夹不能挪进自己和自己的子孙，也不列它现在就在的那层。 */
function moveTargets(kind, id, here) {
  const all = ['', ...props.folders].filter((f) => f !== here)
  const ok = kind === 'folder' ? all.filter((f) => f !== id && !f.startsWith(`${id}/`)) : all
  return ok.map((f) => ({ id: `move:${f}`, label: `移到：${f || '顶层'}`, icon: f ? 'folder' : 'home' }))
}

const menuItems = computed(() => {
  const m = menu.value
  if (!m) return []
  if (m.kind === 'topic') {
    const here = props.items.find((t) => t.topic === m.id)?.folder || ''
    return [{ id: 'open', label: '打开', icon: 'pencil' }, { sep: true }, ...moveTargets('topic', m.id, here),
            ...(m.canvas ? [{ sep: true }, { id: 'remove', label: '删掉这张画布（先备份，md 不动）', icon: 'trash', danger: true }] : [])]
  }
  const parent = m.id.includes('/') ? m.id.slice(0, m.id.lastIndexOf('/')) : ''
  return [{ id: 'new-topic', label: '在这里新建专题', icon: 'plus' },
          { id: 'new-folder', label: '新建子文件夹…', icon: 'folder' },
          { id: 'rename', label: '重命名…', icon: 'pencil' },
          { sep: true }, ...moveTargets('folder', m.id, parent),
          { sep: true }, { id: 'delete', label: empty(m.id) ? '删除文件夹' : '删除文件夹（要先清空）', icon: 'trash',
                           danger: true, disabled: !empty(m.id) }]
})

function pick(action) {
  const m = menu.value
  if (action.startsWith('move:')) {
    const to = action.slice(5)
    emit('op', m.kind === 'topic' ? { op: 'move-topic', topic: m.id, folder: to } : { op: 'move-folder', path: m.id, to })
  } else if (action === 'open') emit('open', m.id)
  else if (action === 'remove' && window.confirm(`删掉「${m.id}」这张画布？只删画（先备份），图里升级出来的知识点 md 一个不碰。`)) {
    emit('op', { op: 'remove-topic', topic: m.id })
  } else if (action === 'new-topic') { selected.value = m.id; draftEl.value?.focus() }
  else if (action === 'new-folder') newFolder(m.id)
  else if (action === 'rename') {
    const name = window.prompt('文件夹改名为：', m.id.slice(m.id.lastIndexOf('/') + 1))
    if (name?.trim()) emit('op', { op: 'rename-folder', path: m.id, name: name.trim() })
  } else if (action === 'delete') emit('op', { op: 'delete-folder', path: m.id })
}
</script>

<template>
  <Drawer side="left" title="专题" icon="pencil" storage-key="topics" :default-width="290"
          @close="emit('close')">
    <template #head-actions>
      <button class="icon-btn ghost" :disabled="busy" title="在选中的文件夹里新建文件夹" data-act="new-folder"
              @click="newFolder()">
        <Icon name="folder" :size="15" />
      </button>
      <button class="icon-btn ghost" :disabled="busy" title="重新拉一遍" @click="emit('refresh')">
        <Icon name="refresh" :size="15" />
      </button>
    </template>

    <template #default>
      <p class="dim" style="font-size: 12.5px; line-height: 1.6">
        围绕一个主题自己画一张总结图。专题可以放进文件夹（拖到文件夹上，或右键「移到…」），
        挪了不改名，<b>md 一个字不动</b>。
      </p>

      <form class="topic-new" @submit.prevent="create">
        <input ref="draftEl" v-model="draft" data-topic-name placeholder="新专题名，如：文件系统" spellcheck="false" />
        <button class="btn primary tiny" :disabled="busy || !draft.trim()" data-act="create-topic">
          <Icon name="plus" :size="13" />新建
        </button>
      </form>
      <p class="dim topic-where">建到：<b>{{ selected || '顶层' }}</b><span v-if="selected">（再点一下那个文件夹回到顶层）</span></p>

      <div class="topic-root" :class="{ on: !selected }" data-folder=""
           @click="selected = ''" @dragover="$event.preventDefault()" @drop.prevent="onRootDrop">
        <Icon name="home" :size="13" />顶层 <span class="dim">（拖到这里移出文件夹）</span>
      </div>
      <p v-if="!items.length && !folders.length" class="dim" style="font-size: 12px; line-height: 1.7; margin-top: 10px">
        还没有专题。在上面起个名字就能开画。
      </p>
      <TopicTree :folders="folders" :topics="items" :current="current" :selected="selected" :collapsed="collapsed"
                 @open="emit('open', $event)" @select="select" @toggle="toggle" @drop="onDrop"
                 @menu="menu = $event" />
    </template>
  </Drawer>
  <ContextMenu v-if="menu" :x="menu.x" :y="menu.y" :title="menu.id" :subtitle="menu.kind === 'topic' ? '专题' : '文件夹'"
               :items="menuItems" @pick="pick" @close="menu = null" />
</template>

<style scoped>
.topic-new { display: flex; gap: 6px; margin-top: 12px; }
.topic-new input { flex: 1; min-width: 0; }
.topic-where { font-size: 11.5px; margin: 6px 0 8px; }
.topic-root {
  display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 6px; cursor: pointer;
  font-size: 12px; border: 1px dashed var(--line); margin-bottom: 6px;
}
.topic-root.on { border-color: var(--accent); }
</style>
