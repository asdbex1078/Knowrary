<script setup>
// 选字查术语（名词解释第 4 步，2026-10-28）：正文里选中一段字时浮在上方。
//
// 选中的正好是术语 → 弹它的解释（多义按语境排好、各标出处，有「详见」能跳知识点）；
// 不是 → 退回一个「记为名词」按钮（接 Step 3 的随手记）。不自动描正文，只按需查。
import Icon from '../ui/Icon.vue'

defineProps({
  at: { type: Object, required: true },             // { x, y } 选区上方的锚点
  senses: { type: Array, default: () => [] },        // 已按语境排好的义项；空 = 不是术语
  nodeName: { type: Function, default: (id) => id },
})
const emit = defineEmits(['add', 'goto'])
</script>

<template>
  <!-- mousedown.prevent：点卡片里的东西不清掉选区（否则按钮还没响应选区就没了） -->
  <div class="term-lookup" :style="{ left: at.x + 'px', top: at.y + 'px' }" data-term-lookup @mousedown.prevent>
    <template v-if="senses.length">
      <div v-for="(s, i) in senses" :key="i" class="tl-sense" :data-scope="s.scope">
        <div class="tl-head">
          <b class="tl-term">{{ s.term }}</b>
          <span class="scope-tag" :class="{ global: s.scope === '全局' }">{{ s.scope }}</span>
          <button v-if="s.see" class="tl-see" :title="`去看知识点「${nodeName(s.see)}」`"
                  data-act="goto" @click="emit('goto', s.see)">
            <Icon name="arrowRight" :size="11" />{{ nodeName(s.see) }}
          </button>
        </div>
        <p v-if="s.gloss" class="tl-gloss">{{ s.gloss }}</p>
      </div>
      <button class="tl-add" data-act="add-more" @click="emit('add')">
        <Icon name="plus" :size="11" />也记一条
      </button>
    </template>
    <button v-else class="tl-capture" data-term-capture @click="emit('add')">
      <Icon name="bookmark" :size="12" />记为名词
    </button>
  </div>
</template>

<style scoped>
.term-lookup {
  position: fixed; transform: translate(-50%, -100%); z-index: 120;
  max-width: min(320px, 86vw);
}
.tl-capture {
  display: inline-flex; align-items: center; gap: 4px; white-space: nowrap;
  padding: 4px 9px; font-size: 11.5px; font-weight: 600; cursor: pointer;
  color: #fff; background: var(--accent); border: none; border-radius: 999px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, .28);
}
.tl-capture:hover { background: var(--accent-hover); }
/* 解释卡 */
.tl-sense, .tl-add {
  background: var(--bg-1, var(--bg)); border: 1px solid var(--line);
  box-shadow: 0 4px 16px rgba(0, 0, 0, .22);
}
.tl-sense { padding: 7px 10px; border-radius: 8px 8px 0 0; }
.tl-sense + .tl-sense { border-top: none; border-radius: 0; }
.tl-head { display: flex; align-items: baseline; gap: 7px; }
.tl-term { font-size: 13px; }
.scope-tag {
  font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent-text);
}
.scope-tag.global { background: color-mix(in srgb, var(--gold, #c98a12) 16%, transparent); color: var(--gold, #c98a12); }
.tl-see {
  margin-left: auto; display: inline-flex; align-items: center; gap: 2px;
  font-size: 10.5px; color: var(--accent-text); background: none; border: none; cursor: pointer; padding: 0;
}
.tl-see:hover { text-decoration: underline; }
.tl-gloss { font-size: 12px; line-height: 1.5; margin: 3px 0 0; color: var(--text-2); white-space: pre-wrap; }
.tl-add {
  display: flex; align-items: center; justify-content: center; gap: 3px; width: 100%;
  padding: 4px; font-size: 11px; color: var(--text-2); cursor: pointer;
  border-top: none; border-radius: 0 0 8px 8px;
}
.tl-add:hover { color: var(--accent-text); background: var(--accent-soft); }
</style>
