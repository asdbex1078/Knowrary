<script setup>
/**
 * 域工具条：浮在当前这个域（分组框 / 簇卡片）的上沿，把"对这一块能做的事"摆出来。
 *
 * 为什么是 HTML 浮层而不是画在 cell 里：画布是 SVG，按钮要有 hover、tooltip、禁用态，
 * 用 SVG 手搓一套等于重写一遍按钮；而浮层只要跟着缩放平移重算一次坐标就行。
 */
import Icon from '../ui/Icon.vue'

defineProps({
  name: { type: String, default: '' },
  at: { type: Object, required: true },      // { x, y } 屏幕坐标（域的左上角）
  doc: { type: String, default: null },      // 绑定的总览文档节点 id
  count: { type: Number, default: 0 },
  folded: { type: Boolean, default: false },
})
const emit = defineEmits(['open-doc', 'write', 'relate', 'new-doc', 'new-node', 'fold', 'unfold', 'close'])
</script>

<template>
  <div class="float groupbar" :style="{ left: `${at.x}px`, top: `${at.y}px` }">
    <span class="gb-name" :title="`${name}（${count} 个知识点）`">{{ name }}</span>
    <span class="sep" />
    <template v-if="doc">
      <button class="btn" title="打开这个域的总览文档" @click="emit('open-doc')">
        <Icon name="file" :size="14" />总览
      </button>
      <button class="icon-btn" title="写内容（改总览文档的正文）" @click="emit('write')">
        <Icon name="pencil" :size="15" />
      </button>
      <button class="icon-btn" title="从总览文档建立关系" @click="emit('relate')">
        <Icon name="link" :size="15" />
      </button>
    </template>
    <button v-else class="btn" title="给这个域建一篇总览文档（就是一个普通知识点）"
            @click="emit('new-doc')">
      <Icon name="file" :size="14" />加总览文档
    </button>
    <span class="sep" />
    <button class="icon-btn" title="在这个域里新建知识点" @click="emit('new-node')">
      <Icon name="plus" :size="15" />
    </button>
    <button class="icon-btn" :title="folded ? '展开这个域' : '折叠成簇卡片'"
            @click="emit(folded ? 'unfold' : 'fold')">
      <Icon :name="folded ? 'unfold' : 'fold'" :size="15" />
    </button>
    <button class="icon-btn" title="收起工具条（Esc）" @click="emit('close')">
      <Icon name="x" :size="14" />
    </button>
  </div>
</template>
