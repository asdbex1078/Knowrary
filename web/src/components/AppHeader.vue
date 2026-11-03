<script setup>
/**
 * 顶栏：只放"全局"的东西——身份、视图切换、搜索、保存状态、系统菜单。
 * 画布操作（缩放 / 布局 / 撤销）一律下放到画布浮层，内容面板下放到左侧活动栏。
 */
import { computed, ref } from 'vue'
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'

const props = defineProps({
  mode: { type: String, default: 'chat' },
  projects: { type: Object, default: () => ({}) },   // { id: {name} }，顶栏最左的切换器
  project: { type: String, default: '' },
  projectTopics: { type: Array, default: () => [] },  // 竣工图：当前项目挂着的专题名
  allTopics: { type: Array, default: () => [] },      // 库里全部专题名（挂新图时挑）
  hits: { type: Array, default: () => [] },
  status: { type: String, default: 'saved' },
  statusText: { type: String, default: '' },
  theme: { type: String, default: 'light' },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['switch-mode', 'switch-project', 'search', 'goto', 'toggle-theme', 'reload', 'rebuild',
                          'help', 'settings', 'topics-menu', 'open-topic', 'bind-topic', 'unbind-topic'])

// 竣工图下拉：还没挂上的专题（挂上 = 加进项目的 topics 并跳过去）
const unbound = computed(() => props.allTopics.filter((t) => !props.projectTopics.includes(t)))

const q = ref('')
const cursor = ref(0)
const inputEl = ref(null)

function onInput(v) {
  q.value = v
  cursor.value = 0
  emit('search', v)
}

function pick(id) {
  q.value = ''
  emit('search', '')
  emit('goto', id)
  inputEl.value?.blur()
}

function onKey(ev) {
  if (!props.hits.length) return
  if (ev.key === 'ArrowDown') { ev.preventDefault(); cursor.value = (cursor.value + 1) % props.hits.length }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); cursor.value = (cursor.value - 1 + props.hits.length) % props.hits.length }
  else if (ev.key === 'Enter') { ev.preventDefault(); pick(props.hits[cursor.value].id) }
  else if (ev.key === 'Escape') { onInput(''); inputEl.value?.blur() }
}

const statusClass = computed(() => props.status)
defineExpose({ focus: () => inputEl.value?.focus() })
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <span class="mark"><Icon name="cube" :size="14" :width="1.9" /></span>
      Knowrary
    </div>

    <!-- 项目切换器放最左：**它决定了后面三个模式看到的范围**，不该藏在某个面板里 -->
    <!-- 「全局」是一条真线，不是占位：不属于任何项目的对话落在 _scratch，
         随手问一句不用先建项目。没有这一项的话那条线在界面上根本没有入口。 -->
    <select class="proj-switch" :value="project"
            title="当前项目：决定对话、项目图、今日清单和出题范围；选「全局」就是不绑项目"
            @change="emit('switch-project', $event.target.value)">
      <option value="">🌐 全局（不绑项目）</option>
      <option v-for="(p, id) in projects" :key="id" :value="id">{{ p.name || id }}</option>
    </select>

    <!-- 跟着当前项目走的两个视图 -->
    <div class="seg" role="tablist">
      <button :class="{ on: mode === 'chat' }" role="tab"
              :title="`项目 · 聊着学（1）${project ? '' : '——现在是「全局」那条线，不绑项目'}`"
              @click="emit('switch-mode', 'chat')">
        <Icon name="network" :size="14" />对话
      </button>
      <button :class="{ on: mode === 'project' }" role="tab" :disabled="!project"
              :title="project ? '项目 · 只看当前项目的点，还没建的画成幽灵（2）' : '先在左边选一个项目'"
              @click="emit('switch-mode', 'project')">
        <Icon name="checklist" :size="14" />项目图
      </button>
      <!-- 竣工图（2026-10-23）：学到一半切到这个项目挂着的专题图，看「我在结构里的哪儿」。
           学过的框带学/考方块、没学出来的淡显（染色在专题画布上常显）。 -->
      <Popover :width="236" :disabled="!project">
        <template #trigger="{ toggle, open }">
          <button :class="{ active: open }" data-act="finished-map" :disabled="!project"
                  :title="project ? '竣工图 · 这个项目挂着的专题图：切过去看学到结构里的哪儿了' : '先在左边选一个项目'"
                  @click="emit('topics-menu'); toggle()">
            <Icon name="map" :size="14" />竣工图
          </button>
        </template>
        <template #default="{ close }">
          <div v-if="projectTopics.length" class="pop-label">挂着的</div>
          <div v-for="t in projectTopics" :key="t" class="pop-item topic-row">
            <button class="topic-open" :title="`切到专题「${t}」`" @click="emit('open-topic', t); close()">
              <Icon name="pencil" :size="14" />{{ t }}</button>
            <button class="icon-btn ghost tiny" :title="`从这个项目摘下「${t}」（专题图本身不删）`"
                    @click.stop="emit('unbind-topic', t)"><Icon name="x" :size="12" /></button>
          </div>
          <div v-if="projectTopics.length && unbound.length" class="pop-sep" />
          <div v-if="unbound.length" class="pop-label">挂一张（挂上就切过去）</div>
          <button v-for="t in unbound" :key="t" class="pop-item" :title="`把「${t}」挂到这个项目并切过去`"
                  @click="emit('bind-topic', t); close()">
            <Icon name="plus" :size="14" />{{ t }}</button>
          <div v-if="!projectTopics.length && !unbound.length" class="pop-item muted">
            还没有专题图——先去「专题」里画一张</div>
        </template>
      </Popover>
    </div>

    <!-- 整个图谱层面的两个视图。**和上面那组分开**：
         它们不跟着项目走，混在一排里会让人不知道自己在看哪个范围。 -->
    <div class="seg global" role="tablist">
      <button :class="{ on: mode === 'structure' }" role="tab"
              :title="project ? '全局 · 整张图（3）——会高亮当前项目的点' : '全局 · 整张图（3）'"
              @click="emit('switch-mode', 'structure')">
        <Icon name="map" :size="14" />全局图
      </button>
      <!-- 2026-10-12 历史入口合一：原来「历史」「谱系」两个按钮，要先想清楚某条历史在哪个视图。
           现在只有一个「历史」，进去顶上切 时间线 / 演化树 / 谱系图（回到上次看的那档）。
           三张图坐标轴不同（年份 / 谁长出谁 / 家族泳道），所以还是三张图，只是同一个门。 -->
      <button :class="{ on: mode === 'history' || mode === 'lineage' }" role="tab"
              title="全局 · 历史：时间线 / 演化树 / 谱系图（4）"
              @click="emit('switch-mode', 'history-entry')">
        <Icon name="clock" :size="14" />历史
      </button>
      <!-- 2026-10-13 专题：围绕一个主题自己画的总结图（画图工具只在这儿开，全局图保持干净） -->
      <button :class="{ on: mode === 'topic' }" role="tab" data-mode="topic"
              title="全局 · 专题：自己画的总结图（结构 / 上下层 / 演化），框可以升级成知识点"
              @click="emit('switch-mode', 'topic')">
        <Icon name="pencil" :size="14" />专题
      </button>
    </div>

    <span class="divider" />

    <div class="search">
      <div class="field">
        <Icon name="search" :size="14" />
        <input ref="inputEl" :value="q" placeholder="搜索知识点…" spellcheck="false"
               @input="onInput($event.target.value)" @keydown="onKey" />
        <kbd v-if="!q">/</kbd>
        <button v-else class="icon-btn ghost tiny" title="清空" @click="onInput('')">
          <Icon name="x" :size="12" />
        </button>
      </div>
      <ul v-if="hits.length" class="hits scroll-thin">
        <li v-for="(h, i) in hits" :key="h.id" :class="{ cursor: i === cursor }"
            @mouseenter="cursor = i" @mousedown.prevent="pick(h.id)">
          <span class="nm">{{ h.name || h.id }}</span>
          <span class="fd">{{ h.field || '未分类' }}</span>
        </li>
      </ul>
    </div>

    <span class="grow" />

    <span class="save-state" :class="statusClass" :title="statusText">
      <span class="dot" />{{ statusText }}
    </span>

    <!-- 设置是常用入口，不该藏进「更多」菜单的第三层 -->
    <button class="icon-btn" title="设置（复习与出题、画布、外观）" @click="emit('settings')">
      <Icon name="grid" :size="16" />
    </button>

    <Popover align="end" :width="212">
      <template #trigger="{ toggle, open }">
        <button class="icon-btn" :class="{ active: open }" title="更多" @click="toggle">
          <Icon name="more" :size="16" />
        </button>
      </template>
      <template #default="{ close }">
        <button class="pop-item" @click="emit('toggle-theme'); close()">
          <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="15" />
          {{ theme === 'dark' ? '切到浅色' : '切到深色' }}
        </button>
        <div class="pop-sep" />
        <button class="pop-item" :disabled="busy" @click="emit('reload'); close()">
          <Icon name="refresh" :size="15" />重新加载
        </button>
        <button class="pop-item" title="画布没反应时点这里重建（不影响已保存的布局）"
                @click="emit('rebuild'); close()">
          <Icon name="rotate" :size="15" />恢复画布
        </button>
        <div class="pop-sep" />
        <button class="pop-item" @click="emit('help'); close()">
          <Icon name="help" :size="15" />快捷键<span class="hint">?</span>
        </button>
      </template>
    </Popover>
  </header>
</template>
