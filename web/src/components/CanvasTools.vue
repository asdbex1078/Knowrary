<script setup>
/**
 * 画布右上角浮层：改的都是"画布怎么显示 / 画布上放什么"，属于画布本身，不该占顶栏。
 */
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'
import { FAMILIES, FAMILY_STYLE } from '../canvas/shapes.js'

defineProps({
  visible: { type: Object, required: true },
  shownFamilies: { type: Number, default: 0 },
  collapsed: { type: Number, default: 0 },
  aggregate: { type: Boolean, default: true },
  autoLod: { type: Boolean, default: true },
  snap: { type: Boolean, default: true },
  avoidNodes: { type: Boolean, default: false },
  borrow: { type: Boolean, default: false },
  project: { type: Boolean, default: false },   // 只有项目画布才有"借来的点"这回事
  layouts: { type: Object, default: () => ({}) },
  canUndo: { type: Boolean, default: false },
  canRedo: { type: Boolean, default: false },
  locked: { type: Boolean, default: false },
})
const emit = defineEmits([
  'toggle-family', 'toggle-aggregate', 'toggle-lod', 'toggle-snap', 'toggle-avoid', 'toggle-borrow',
  'pick-layout',
  'add-note', 'add-image', 'undo', 'redo',
])
</script>

<template>
  <div class="float tools">
    <Popover align="end" :width="248">
      <template #trigger="{ toggle, open }">
        <button class="btn" :class="{ active: open }" title="关系族过滤 / 跨组边聚合 / 自动折叠" @click="toggle">
          <Icon name="layers" :size="15" />
          <span>{{ shownFamilies }} 族</span>
          <span v-if="collapsed" class="chip accent tiny">{{ collapsed }} 簇</span>
          <Icon name="chevronDown" :size="13" class="caret" />
        </button>
      </template>
      <template #default>
        <div class="pop-title">关系族</div>
        <label v-for="f in FAMILIES" :key="f" class="switch-row">
          <input type="checkbox" :checked="visible[f]" @change="emit('toggle-family', f)" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <i class="swatch" :style="{ borderTopColor: FAMILY_STYLE[f].stroke,
                                      borderTopStyle: FAMILY_STYLE[f].strokeDasharray ? 'dashed' : 'solid' }" />
          <span class="label">{{ f }}</span>
        </label>
        <div class="pop-sep" />
        <div class="pop-title">画法</div>
        <label class="switch-row">
          <input type="checkbox" :checked="aggregate" @change="emit('toggle-aggregate')" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">聚合跨组边<span class="sub">同一对分组之间 3 条以上才并成一束</span></span>
        </label>
        <label class="switch-row">
          <input type="checkbox" :checked="autoLod" @change="emit('toggle-lod')" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">自动折叠<span class="sub">缩小时分组收成簇卡片</span></span>
        </label>
        <label class="switch-row">
          <input type="checkbox" :checked="snap" @change="emit('toggle-snap')" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">对齐吸附<span class="sub">拖动出参考线，松手贴 8px 网格</span></span>
        </label>
        <label class="switch-row">
          <input type="checkbox" :checked="avoidNodes" @change="emit('toggle-avoid')" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">连线绕开卡片<span class="sub">直角走线，从卡片之间穿；手工拐过的边不受影响</span></span>
        </label>
        <label v-if="project" class="switch-row">
          <input type="checkbox" :checked="borrow" @change="emit('toggle-borrow')" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">周边一跳<span class="sub">把 GPU 前面的 CPU 这类外部邻居借过来画（↗ 靛蓝虚线，不可拖、不落盘；刷新后回到关）</span></span>
        </label>
      </template>
    </Popover>

    <Popover align="end" :width="236">
      <template #trigger="{ toggle, open }">
        <button class="btn" :class="{ active: open }" :disabled="locked" title="重排布局（先预览后落盘）" @click="toggle">
          <Icon name="grid" :size="15" />布局<Icon name="chevronDown" :size="13" class="caret" />
        </button>
      </template>
      <template #default="{ close }">
        <div class="pop-title">重排（先预览）</div>
        <button v-for="(spec, kind) in layouts" :key="kind" class="pop-item"
                @click="emit('pick-layout', kind); close()">
          <Icon name="grid" :size="15" />{{ spec.label }}
        </button>
      </template>
    </Popover>

    <Popover align="end" :width="212">
      <template #trigger="{ toggle, open }">
        <button class="icon-btn" :class="{ active: open }" :disabled="locked" title="往画布上加东西" @click="toggle">
          <Icon name="plus" :size="16" />
        </button>
      </template>
      <template #default="{ close }">
        <div class="pop-title">加到画布</div>
        <button class="pop-item" @click="emit('add-note'); close()">
          <Icon name="note" :size="15" />便签
        </button>
        <button class="pop-item" @click="emit('add-image'); close()">
          <Icon name="image" :size="15" />图片<span class="hint">素材库</span>
        </button>
        <div class="pop-sep" />
        <p class="pop-title" style="text-transform: none; letter-spacing: 0; font-weight: 400; line-height: 1.5;">
          便签和图片只存在 layout 里，不会写进任何 md。
        </p>
      </template>
    </Popover>

    <span class="sep" />

    <div class="btn-group">
      <button class="icon-btn" :disabled="!canUndo || locked" title="撤销（⌘Z）" @click="emit('undo')">
        <Icon name="undo" :size="16" />
      </button>
      <button class="icon-btn" :disabled="!canRedo || locked" title="重做（⇧⌘Z）" @click="emit('redo')">
        <Icon name="redo" :size="16" />
      </button>
    </div>
  </div>
</template>
