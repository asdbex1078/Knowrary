<script setup>
/**
 * 新建知识点：在画布上右键就能开一张新卡片，不用切去 Obsidian 再回来。
 *
 * 只收规范 3 的三个必填字段（name / field / desc）加一个落盘目录——
 * 其余（year、tags、正文）留到 Obsidian 里慢慢写，这里要的是"想到了先记下"。
 * 默认勾上"接着连关系"：孤立的新节点是知识图谱里最容易烂掉的东西。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  fields: { type: Array, default: () => [] },     // 现有领域名
  dirs: { type: Array, default: () => [] },       // nodes/ 下现有目录
  defaults: { type: Object, default: () => ({}) },// { field, dir, groupName, name, asDoc }
  taken: { type: Set, default: () => new Set() }, // 已存在的 id
})
const emit = defineEmits(['create', 'close'])

const name = ref(props.defaults.name || '')
const desc = ref('')
const field = ref(props.defaults.field || props.fields[0] || '')
const year = ref('')     // 可选：填了才进历史视图的时间轴
const dir = ref(props.defaults.dir || props.dirs[0] || 'nodes')
const thenRelate = ref(true)
const nameEl = ref(null)

const id = computed(() => name.value.trim())
const bad = computed(() => {
  if (!id.value) return ''
  if (/[\\/:*?"<>|\s]/.test(id.value)) return '名称里不能有 / \\ : * ? " < > | 和空格（它同时是文件名）'
  if (props.taken.has(id.value)) return `已经有一个叫「${id.value}」的知识点了`
  return ''
})
const ready = computed(() => !!id.value && !bad.value && !!desc.value.trim() && !!field.value.trim())

function submit() {
  if (!ready.value) return
  emit('create', { id: id.value, name: id.value, desc: desc.value.trim(),
                   field: field.value.trim(), dir: dir.value, thenRelate: thenRelate.value,
                   year: /^\d{3,4}$/.test(year.value.trim()) ? Number(year.value.trim()) : null })
}

function onKey(ev) {
  if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') }
  else if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); submit() }
}

onMounted(() => {
  document.addEventListener('keydown', onKey, true)
  nextTick(() => nameEl.value?.focus())
})
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog node-dialog">
    <header>
      <Icon name="plus" :size="15" />
      <span class="who">{{ defaults.asDoc ? '新建总览文档' : '新建知识点' }}</span>
      <span v-if="defaults.groupName" class="dim">放进「{{ defaults.groupName }}」</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <label class="fld">
        <span class="lb">名称<i>必填</i></span>
        <input ref="nameEl" v-model="name" placeholder="例如：控制器" @keydown.enter.prevent="desc || submit()" />
        <span class="hint">它同时是文件名和 id：<code>{{ dir }}/{{ id || '名称' }}.md</code></span>
      </label>
      <p v-if="bad" class="warn-text">{{ bad }}</p>

      <label class="fld">
        <span class="lb">一句话摘要<i>必填</i></span>
        <input v-model="desc" placeholder="它是什么、解决什么问题" />
      </label>

      <div class="two">
        <label class="fld">
          <span class="lb">年份<i>可选</i></span>
          <input v-model="year" inputmode="numeric" placeholder="如 2017，填了才进历史视图" />
        </label>
        <label class="fld">
          <span class="lb">领域</span>
          <input v-model="field" list="kg-fields" placeholder="field" />
          <datalist id="kg-fields"><option v-for="f in fields" :key="f" :value="f" /></datalist>
        </label>
      </div>
      <label class="fld">
        <span class="lb">存到</span>
        <select v-model="dir">
          <option v-for="d in dirs" :key="d" :value="d">{{ d }}/</option>
        </select>
      </label>

      <label class="switch-row" style="margin: 2px -9px 0">
        <input type="checkbox" v-model="thenRelate" />
        <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
        <span class="label">创建后接着连关系<span class="sub">新节点最容易变成孤岛，趁热挂上去</span></span>
      </label>

      <footer>
        <span class="dim">⌘↵ 创建</span>
        <button class="btn primary" :disabled="!ready" @click="submit">
          <Icon name="check" :size="14" />创建并放到画布
        </button>
      </footer>
    </div>
  </div>
</template>
