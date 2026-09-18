<script setup>
/**
 * 左侧活动栏：一列图标 = 一个工具窗口，互斥开合（IDE 的工具窗口条）。
 * 角标直接把"有多少待办"摆在眼前，不用点进去才知道。
 *
 * 工具清单（谁在哪个作用域 / 哪个视图 / 哪一段）在 activity-items.js，
 * App.vue 切视图时也读同一张表来关掉已经没有入口的面板。
 */
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import { railGroups } from './activity-items.js'

const props = defineProps({
  active: { type: String, default: '' },
  mode: { type: String, default: 'structure' },
  inbox: { type: Number, default: 0 },
  due: { type: Number, default: 0 },
  theme: { type: String, default: 'light' },
  project: { type: String, default: '' },      // 空串 = 「🌐 全局」
})
const emit = defineEmits(['select', 'toggle-theme'])

/** 角标是响应式的，所以清单里只存"取哪个数"，值在这里现取。 */
const badgeOf = (it) => (it.badge === 'inbox' ? props.inbox : it.badge === 'due' ? props.due : 0)

const groups = computed(() => railGroups(props.mode, props.project))
</script>

<template>
  <!-- 一个工具都没有的视图（谱系）整条收起，把 72px 还给画布。
       主题切换在顶栏系统菜单里另有入口，不会跟着丢。 -->
  <nav v-if="groups.length" class="rail">
    <template v-for="(g, gi) in groups" :key="g.group">
      <span v-if="gi" class="rail-split" />
      <button v-for="it in g.items" :key="it.key || it.id" class="rail-btn"
              :class="{ on: active === it.id }"
              :data-tip="`${g.scope === 'project' ? '项目' : '全局'} · ${it.name} · ${it.tip}`"
              @click="emit('select', it.id)">
        <span class="rail-ico">
          <Icon :name="it.icon" :size="18" />
          <span v-if="badgeOf(it)" class="badge" :class="{ gold: it.gold }">
            {{ badgeOf(it) > 99 ? '99+' : badgeOf(it) }}
          </span>
        </span>
        <span class="rail-name">{{ it.name }}</span>
      </button>
    </template>

    <span class="spacer" />

    <button class="rail-btn" :data-tip="theme === 'dark' ? '切到浅色' : '切到深色'" @click="emit('toggle-theme')">
      <span class="rail-ico"><Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="17" /></span>
      <span class="rail-name">{{ theme === 'dark' ? '浅色' : '深色' }}</span>
    </button>
  </nav>
</template>
