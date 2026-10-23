<script setup>
// 把专题画布上的一个框绑定到已有知识点（2026-10-13）。
// 只挑点；绑完要补哪些 md 关系（大框 = 中心 + 部件，小框 = 被大框的点「部件」）由父组件算好给人看。
import { computed, nextTick, onMounted, ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  shapeText: { type: String, default: '' },
  frame: { type: Boolean, default: false },
  nodes: { type: Array, default: () => [] },
})
const emit = defineEmits(['pick', 'close'])

const q = ref(props.shapeText)
const cur = ref(0)
const box = ref(null)

const hits = computed(() => {
  const k = q.value.trim().toLowerCase()
  const all = props.nodes.filter((n) => !n.virtual)
  if (!k) return all.slice(0, 12)
  return all.filter((n) => `${n.id} ${n.name || ''} ${(n.aliases || []).join(' ')}`.toLowerCase().includes(k)).slice(0, 12)
})

function onKey(ev) {
  if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') }
  else if (ev.key === 'ArrowDown') { ev.preventDefault(); cur.value = Math.min(cur.value + 1, hits.value.length - 1) }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); cur.value = Math.max(cur.value - 1, 0) }
  else if (ev.key === 'Enter' && hits.value[cur.value]) { ev.preventDefault(); emit('pick', hits.value[cur.value].id) }
}

onMounted(async () => { await nextTick(); box.value?.focus(); box.value?.select() })
</script>

<template>
  <div class="rel-dialog node-dialog" data-bind-dialog @keydown="onKey">
    <header>
      <Icon name="link" :size="15" />
      <span class="who">绑定已有知识点</span>
      <span class="dim">{{ shapeText || '（没写字的框）' }}</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>
    <div class="rel-body">
      <input ref="box" v-model="q" data-bind-search placeholder="搜知识点…" spellcheck="false" @input="cur = 0" />
      <ul class="hits">
        <li v-for="(h, i) in hits" :key="h.id" :class="{ cur: i === cur }" :data-bind-hit="h.id"
            @mouseenter="cur = i" @click="emit('pick', h.id)">
          <span class="nm">{{ h.name || h.id }}</span>
          <span class="ds">{{ h.desc || '' }}</span>
        </li>
        <li v-if="!hits.length" class="none">没搜到。要新建的话，关掉这个窗口，右键「升级为知识点：新建文档」</li>
      </ul>
      <p class="dim" style="font-size: 11.5px; line-height: 1.6; margin: 8px 0 0">
        <template v-if="frame">大框绑定后，这个点就是专题的中心（md 里写上 <code>专题</code>），框里已经绑定的小框补成它的「部件」。</template>
        <template v-else>绑定后框上显示它的名字，悬停显示它的一句话摘要；套在已绑定的大框里的话，补一条「部件」关系。</template>
        写 md 照常过审核。
      </p>
    </div>
  </div>
</template>
