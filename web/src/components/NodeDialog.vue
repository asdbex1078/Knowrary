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
  defaults: { type: Object, default: () => ({}) },// { field, dir, groupName, name, asDoc, desc, body, contains, wrap }
  taken: { type: Set, default: () => new Set() }, // 已存在的 id
})
const emit = defineEmits(['create', 'close'])

const name = ref(props.defaults.name || '')
const desc = ref(props.defaults.desc || props.defaults.planWhy || '')
// 概括节点（第六步）：正文由模型起草预填，和 layer / year 一样挂「AI 建议」标记，改过就消失。
// `contains` 是要被概括的子节点：创建时每个连一条「包含」边。`wrap` = 顺手建一个框把它们圈起来。
const advisedText = { name: props.defaults.aiDraft ? (props.defaults.name || '') : '',
                      desc: props.defaults.aiDraft ? (props.defaults.desc || '') : '',
                      body: props.defaults.aiDraft ? (props.defaults.body || '') : '' }
const body = ref(props.defaults.body || '')
const contains = props.defaults.contains || []
const wrap = ref(!!props.defaults.wrap)
const aiName = computed(() => !!advisedText.name && name.value === advisedText.name)
const aiDesc = computed(() => !!advisedText.desc && desc.value === advisedText.desc)
const aiBody = computed(() => !!advisedText.body && body.value === advisedText.body)
const field = ref(props.defaults.field || props.fields[0] || '')
// 抽象层：历史视图按它分泳道，新节点也按它落到对应的那条道里。
// 不填的话，一个没有边的新点只能落在领域那个大框里——正好在所有泳道之外。
const LAYERS = ['理论', '硬件', '体系结构', '汇编接口', '系统软件', '高级语言', 'AI应用']

// 从计划点进来时，这两个格子是**拆计划那一次调用顺手给的建议**（server/projects.py 的 _LAYER_YEAR），
// 不是我填的。直接预填省一次选择，但必须看得出来是机器猜的——
// 所以旁边挂一个「AI 建议」小标，值一被改动就消失：机器猜的和我自己填的不能长成一个样。
const advised = {
  layer: props.defaults.layer || '',
  year: props.defaults.year ? String(props.defaults.year) : '',
}
const layer = ref(advised.layer)
const year = ref(advised.year)   // 可选：填了才进历史视图的时间轴
const aiLayer = computed(() => !!advised.layer && layer.value === advised.layer)
const aiYear = computed(() => !!advised.year && year.value === advised.year)
const dir = ref(props.defaults.dir || props.dirs[0] || 'nodes')
// 目录可以手敲：开一个新领域时 vault 里还没有那个文件夹，只能选已有的就卡死了
const badDir = computed(() => {
  const d = dir.value.trim()
  if (!d) return '目录不能为空'
  if (!/^(nodes|fields)(\/|$)/.test(d)) return '只能建在 nodes/ 或 fields/ 下'
  if (/\.\.|\/\//.test(d)) return '路径里不能有 .. 或空目录段'
  return ''
})
const thenRelate = ref(true)
const nameEl = ref(null)

const id = computed(() => name.value.trim())
const bad = computed(() => {
  if (!id.value) return ''
  if (/[\\/:*?"<>|\s]/.test(id.value)) return '名称里不能有 / \\ : * ? " < > | 和空格（它同时是文件名）'
  if (props.taken.has(id.value)) return `已经有一个叫「${id.value}」的知识点了`
  return ''
})
const ready = computed(() =>
  !!id.value && !bad.value && !badDir.value && !!desc.value.trim() && !!field.value.trim())

function submit() {
  if (!ready.value) return
  emit('create', { id: id.value, name: id.value, desc: desc.value.trim(),
                   field: field.value.trim(), dir: dir.value, thenRelate: thenRelate.value && !contains.length,
                   layer: layer.value || null,
                   year: /^\d{3,4}$/.test(year.value.trim()) ? Number(year.value.trim()) : null,
                   body: body.value.trim() || null, contains, wrap: wrap.value })
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
      <span class="who">{{ contains.length ? `概括 ${contains.length} 个点` : defaults.asDoc ? '新建总览文档' : '新建知识点' }}</span>
      <span v-if="defaults.groupName" class="dim">放进「{{ defaults.groupName }}」</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <label class="fld">
        <span class="lb">名称<i v-if="aiName" class="hot">AI 建议</i><i v-else class="req">必填</i></span>
        <input ref="nameEl" v-model="name" placeholder="例如：控制器" @keydown.enter.prevent="desc || submit()" />
        <span class="hint">它同时是文件名和 id：<code>{{ dir }}/{{ id || '名称' }}.md</code></span>
      </label>
      <p v-if="bad" class="warn-text">{{ bad }}</p>

      <label class="fld">
        <span class="lb">一句话摘要<i v-if="aiDesc" class="hot">AI 建议</i><i v-else class="req">必填</i></span>
        <input v-model="desc" placeholder="它是什么、解决什么问题" />
      </label>

      <template v-if="contains.length">
        <label class="fld">
          <span class="lb">正文<i v-if="aiBody" class="hot">AI 建议</i><i v-else>可选</i></span>
          <textarea v-model="body" rows="9" class="scroll-thin"
                    placeholder="从 ## 描述 开始；## 关系 由系统写，别写"></textarea>
          <span class="hint">模型按子节点的摘要起的草稿，改成你自己的判断再存；创建时给下面每个点连一条「包含」边</span>
        </label>
        <div class="contains dim">包含：{{ contains.join('、') }}</div>
        <label v-if="defaults.wrapOption" class="switch-row" style="margin: 2px -9px 0">
          <input type="checkbox" v-model="wrap" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">顺手建一个框把它们圈起来<span class="sub">框是排版，节点是知识；框会绑这个节点当总览</span></span>
        </label>
      </template>

      <div class="two">
        <label class="fld">
          <span class="lb">年份<i v-if="aiYear" class="hot">AI 建议</i><i v-else>可选</i></span>
          <input v-model="year" inputmode="numeric" placeholder="如 2017，填了才进历史视图" />
        </label>
        <label class="fld">
          <span class="lb">抽象层<i v-if="aiLayer" class="hot">AI 建议</i><i v-else>可选</i></span>
          <select v-model="layer">
            <option value="">不分层</option>
            <option v-for="l in LAYERS" :key="l" :value="l">{{ l }}</option>
          </select>
          <span class="hint">历史视图按它分泳道；不填的话新点只能落在领域那个大框里</span>
        </label>
        <label class="fld">
          <span class="lb">领域<i class="req">必填</i></span>
          <input v-model="field" list="kg-fields" placeholder="例如：计算机系统" />
          <datalist id="kg-fields"><option v-for="f in fields" :key="f" :value="f" /></datalist>
          <span class="hint">决定它归到图上哪个框；可以从已有领域里挑，也能直接敲个新的</span>
        </label>
      </div>
      <label class="fld">
        <span class="lb">存到</span>
        <input v-model="dir" list="kg-node-dirs" placeholder="nodes/……" />
        <datalist id="kg-node-dirs"><option v-for="d in dirs" :key="d" :value="d" /></datalist>
        <span class="hint">目录不存在会顺手建出来；新开一个领域时直接敲新路径即可。</span>
      </label>
      <p v-if="badDir" class="warn-text">{{ badDir }}</p>

      <label v-if="!contains.length" class="switch-row" style="margin: 2px -9px 0">
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
