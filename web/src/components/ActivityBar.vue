<script setup>
/**
 * 左侧活动栏：一列图标 = 一个工具窗口，互斥开合（IDE 的工具窗口条）。
 * 角标直接把"有多少待办"摆在眼前，不用点进去才知道。
 */
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  active: { type: String, default: '' },
  mode: { type: String, default: 'structure' },
  inbox: { type: Number, default: 0 },
  due: { type: Number, default: 0 },
  theme: { type: String, default: 'light' },
  project: { type: String, default: '' },      // 空串 = 「🌐 全局」
})
const emit = defineEmits(['select', 'toggle-theme'])

// 每个工具窗口标上**作用域**，并且**按当前是不是在某个项目下过滤**：
// 在一个项目里的时候，你要的是"这个项目能干什么"，全图层面的欠账 / 日历 / Inbox
// 只会让人分心；反过来，"新建项目"这种事本来就该在「🌐 全局」下做。
// 所以不是把两组摆在一起让人挑，而是按当前作用域只给该给的。
//
// `scopes` = 在哪个作用域下出现（project = 选着某个项目时，global = 选着「🌐 全局」时）；
// `modes`  = 在哪几个视图下有意义（贴图要有画布，时间线只属于历史）。
const ITEMS = [
  { id: 'plans', icon: 'checklist', name: '清单', scopes: ['project'],
    tip: '这个项目的学习计划 / 面试方案 / 领域地图', modes: ['chat', 'project', 'structure', 'lineage'] },
  { id: 'study', icon: 'rotate', name: '今日', gold: true, badge: 'due', scopes: ['project', 'global'],
    tip: '今天该建什么、该复习什么（复习是全局的，建设按当前项目过滤）',
    modes: ['chat', 'project', 'structure', 'lineage'] },
  { id: 'import', icon: 'file', name: '导入', scopes: ['project', 'global'],
    tip: '把一篇笔记拆成知识点导进图谱（项目下先认领清单里没建的点）', modes: ['chat', 'project', 'structure', 'lineage'] },
  { id: 'assets', icon: 'image', name: '素材', scopes: ['project', 'global'],
    tip: '往当前这块画布上贴图、加便签', modes: ['project', 'structure'] },

  { id: 'plans', icon: 'checklist', name: '项目', scopes: ['global'], key: 'plans-global',
    tip: '所有项目：新建、切换、改配置', modes: ['chat', 'project', 'structure', 'lineage'] },
  { id: 'inbox', icon: 'inbox', name: 'Inbox', badge: 'inbox', scopes: ['global'],
    tip: '待上全局图的知识点', modes: ['project', 'structure'] },
  { id: 'digest', icon: 'layers', name: '欠账', scopes: ['global'],
    tip: '草稿 / 桥 / 连边建议 / 重复 / stub（整张图的）', modes: ['chat', 'project', 'structure', 'lineage'] },
  { id: 'stats', icon: 'chart', name: '参数量', scopes: ['global'],
    tip: '填了 params 的知识点：怎么涨上来的、谁更大', modes: ['structure', 'lineage', 'history'] },
  { id: 'calendar', icon: 'clock', name: '日历', scopes: ['global'],
    tip: '每天建了多少、复习了多少（全是算出来的）', modes: ['chat', 'project', 'structure', 'history', 'lineage'] },
  { id: 'timeline', icon: 'timeline', name: '时间线', scopes: ['global'],
    tip: '历史视图的泳道与过滤', modes: ['history'] },
]

/** 角标是响应式的，所以列表里只存"取哪个数"，值在这里现取。 */
const badgeOf = (it) => (it.badge === 'inbox' ? props.inbox : it.badge === 'due' ? props.due : 0)

/** 当前作用域下该有哪些工具。
 *
 * **作用域跟着视图走，不只跟着项目选择走**：顶栏已经把视图分成了项目级（对话 / 项目图）
 * 和全局级（全局图 / 历史）。站在全局图或历史视图上时，就算选着某个项目，
 * 你要的也是整张图的工具（Inbox / 欠账 / 时间线）——按项目过滤会把它们藏起来，
 * 于是"历史视图里调不出时间线面板"。
 */
const groups = computed(() => {
  const projectView = props.mode === 'chat' || props.mode === 'project'
  const scope = projectView && props.project ? 'project' : 'global'
  const items = ITEMS.filter((it) => it.scopes.includes(scope) && it.modes.includes(props.mode))
  return items.length ? [{ scope, items }] : []
})
</script>

<template>
  <nav class="rail">
    <template v-for="(g, gi) in groups" :key="g.scope">
      <button v-for="it in g.items" :key="it.key || it.id" class="rail-btn"
              :class="{ on: active === it.id }"
              :data-tip="`${g.scope === 'project' ? '项目' : '全局'} · ${it.name} · ${it.tip}`"
              @click="emit('select', it.id)">
        <Icon :name="it.icon" :size="18" />
        <span v-if="badgeOf(it)" class="badge" :class="{ gold: it.gold }">
          {{ badgeOf(it) > 99 ? '99+' : badgeOf(it) }}
        </span>
      </button>
    </template>

    <span class="spacer" />

    <button class="rail-btn" :data-tip="theme === 'dark' ? '切到浅色' : '切到深色'" @click="emit('toggle-theme')">
      <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="17" />
    </button>
  </nav>
</template>
