<script setup>
// 底部状态栏：常驻但不占地方的数字。原来这些挤在顶栏，和按钮混成一排。
import Icon from '../ui/Icon.vue'

defineProps({
  mode: { type: String, default: 'structure' },
  stats: { type: Object, default: () => ({ nodes: 0, edges: 0, stubs: 0 }) },
  edgesShown: { type: Number, default: 0 },
  aggShown: { type: Number, default: 0 },
  histPlan: { type: Object, default: null },
  range: { type: Array, default: () => [0, 0] },
  indexRevision: { type: Number, default: 0 },
  revision: { type: Number, default: 0 },
  focusName: { type: String, default: '' },
  problems: { type: Array, default: () => [] },
  usage: { type: Object, default: null },     // 模型调用账本；null = 还没拉到
  llmBusy: { type: Boolean, default: false }, // 有调用正在跑
})
const emit = defineEmits(['exit-focus', 'show-problems', 'help', 'show-usage'])

/** 两位有效数字就够看趋势了；不足一分钱显示 <$0.01，免得一排 $0.00 让人以为没花钱。 */
const money = (n) => (n >= 0.01 ? `$${n.toFixed(2)}` : n > 0 ? '<$0.01' : '$0.00')
const kilo = (n) => (n >= 10000 ? `${(n / 1000).toFixed(0)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`)
</script>

<template>
  <footer class="statusbar">
    <template v-if="mode === 'structure'">
      <span class="stat"><b class="tnum">{{ stats.nodes }}</b> 节点</span>
      <span class="stat" :title="`组内边 ${edgesShown} · 跨组 ${aggShown} 束 · 共 ${stats.edges} 条`">
        <b class="tnum">{{ edgesShown + aggShown }}</b> 连线
      </span>
      <span v-if="stats.stubs" class="stat"><b class="tnum">{{ stats.stubs }}</b> stub</span>
    </template>
    <template v-else>
      <span class="stat"><b class="tnum">{{ histPlan?.placed.size ?? 0 }}</b> 个有年份</span>
      <span class="stat"><b class="tnum">{{ histPlan?.lanes.length ?? 0 }}</b> 泳道</span>
      <span class="stat tnum">{{ range[0] }}–{{ range[1] }}</span>
    </template>

    <template v-if="focusName">
      <span class="divider" />
      <button class="sb-btn" title="退出聚焦（Esc）" @click="emit('exit-focus')">
        <Icon name="target" :size="12" />聚焦：{{ focusName }}<Icon name="x" :size="11" />
      </button>
    </template>

    <span class="grow" />

    <button v-if="problems.length" class="sb-btn alert" @click="emit('show-problems')">
      <Icon name="warn" :size="12" />{{ problems.length }} 项待处理
    </button>
    <button v-if="usage" class="sb-btn" :class="{ busy: llmBusy }" data-act="usage"
            :title="`今天 ${usage.today.calls} 次模型调用` +
                    (usage.cost_known ? ` · ${money(usage.today.cost_usd)}` : '（这个 provider 不报价）') +
                    ` · 累计 ${usage.totals.calls} 次。点开看明细`"
            @click="emit('show-usage')">
      <Icon name="network" :size="12" />
      <span v-if="llmBusy">调用中…</span>
      <template v-else>
        {{ usage.today.calls }} 次<template v-if="usage.cost_known"> · {{ money(usage.today.cost_usd) }}</template>
        <template v-else> · {{ kilo(usage.today.input_tokens + usage.today.output_tokens) }} tok</template>
      </template>
    </button>
    <span class="rev" :title="`索引 revision ${indexRevision} · 布局 revision ${revision}`">
      r{{ indexRevision }}/{{ revision }}
    </span>
    <button class="sb-btn" title="快捷键" @click="emit('help')"><Icon name="help" :size="12" />?</button>
  </footer>
</template>
