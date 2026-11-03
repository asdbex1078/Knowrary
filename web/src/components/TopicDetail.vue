<script setup>
/**
 * 专题图上点方块看细节（版本演进 A 方案第 5 步，2026-10-27）：右侧只读抽屉。
 *
 * 方块「升级为知识点」绑了 md 的，双击它 / 编辑卡上点「看细节」就在这里展开那篇正文。
 * 正文按 `## 0.12` / `## 2.6` 这样的二级标题分版本小节（标题名对上中心的 `版本` 轴），
 * 全展开，当前滑块那一版描金高亮、滚到眼前，其余淡着仍可读——和画布上方块的显隐一个节奏。
 * 只读：改正文还是去右边检查器，这里专心看。
 */
import { computed, nextTick, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import Markdown from '../ui/Markdown.vue'
import { splitByVersion } from '../canvas/drawing.js'

const props = defineProps({
  node: { type: Object, default: null },        // { id, name }
  body: { type: String, default: '' },          // 去过 frontmatter、切到 `## 关系` 的正文
  order: { type: Array, default: () => [] },     // 版本轴
  current: { type: String, default: '' },        // 当前滑块的版本
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'goto'])

const blocks = computed(() => splitByVersion(props.body, props.order))
const hasVersions = computed(() => blocks.value.some((b) => b.ver))

const scroller = ref(null)
// 换版本（滑块动了 / 刚打开）→ 把高亮那节滚到眼前。只滚抽屉自己这块，不动页面。
watch([() => props.current, () => props.node?.id, blocks], async () => {
  await nextTick()
  const el = scroller.value?.querySelector('.ver-sec.on')
  if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
})
</script>

<template>
  <Drawer side="right" title="细节" icon="file" storage-key="topic-detail"
          :default-width="380" :min="300" :max="620" @close="emit('close')">
    <template #head-actions>
      <button v-if="node" class="icon-btn ghost" title="去检查器看它的关系 / 改正文"
              data-act="goto-node" @click="emit('goto', node.id)">
        <Icon name="sidebar" :size="15" />
      </button>
    </template>
    <template #default>
      <div ref="scroller" class="topic-detail">
        <h3 class="td-title">{{ node?.name || node?.id || '' }}</h3>
        <div v-if="loading" class="dim" style="padding: 8px 0">正在读…</div>
        <div v-else-if="!body" class="empty">
          <Icon name="file" :size="28" :width="1.3" />
          <span class="t">这篇还没写正文</span>
          <span class="s">在检查器里点「写内容」补上，或按 `## {{ order[0] || '版本' }}` 分版本写。</span>
        </div>
        <template v-else>
          <!-- 没分版本小节：整篇当前言摆着，抽屉退化成普通只读正文 -->
          <p v-if="!hasVersions" class="dim td-note">
            正文里还没有按 `## 版本` 分小节。写上 `## {{ order.join('` / `## ') || '版本' }}`，这里就会分版本高亮。
          </p>
          <template v-for="(b, i) in blocks" :key="i">
            <div v-if="!b.ver" class="td-intro">
              <Markdown :text="b.text" />
            </div>
            <section v-else class="ver-sec" :class="{ on: b.ver === current }" :data-ver="b.ver">
              <div class="ver-sec-head">
                <span class="ver-tag">{{ b.ver }}</span>
                <span v-if="b.ver === current" class="dim now">当前</span>
              </div>
              <Markdown :text="b.text" />
            </section>
          </template>
        </template>
      </div>
    </template>
  </Drawer>
</template>

<style scoped>
.topic-detail { margin: -14px; padding: 14px; height: 100%; overflow: auto; }
.td-title { margin: 0 0 10px; font-size: 16px; }
.td-note { font-size: 11.5px; margin: 0 0 10px; }
.td-intro { margin-bottom: 4px; }
.ver-sec {
  margin: 10px 0; padding: 8px 10px 8px 12px;
  border-left: 2px solid transparent; border-radius: 0 6px 6px 0;
  opacity: .45; transition: opacity .18s var(--ease), border-color .18s var(--ease), background .18s var(--ease);
}
.ver-sec.on {
  opacity: 1; border-left-color: var(--gold, #d8a838);
  background: color-mix(in srgb, var(--gold, #d8a838) 8%, transparent);
}
.ver-sec-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.ver-tag {
  font: 600 11px/1.4 var(--mono, ui-monospace, monospace);
  padding: 1px 7px; border-radius: 999px;
  background: color-mix(in srgb, var(--gold, #d8a838) 18%, transparent);
  color: var(--gold, #d8a838);
}
.ver-sec:not(.on) .ver-tag { background: var(--line, rgba(128, 128, 128, .18)); color: inherit; }
.ver-sec-head .now { font-size: 10.5px; }
</style>
