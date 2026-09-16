<script setup>
/**
 * 合并两张重复的卡。
 *
 * 重连关系时撞重复是必然的——同一个概念先后用两个名字各建了一张。欠账清单早就能报
 * 「重复候选」，但一直只能手工删一个再挨个改引用，那正是最容易改漏的活。
 *
 * **保留谁由人定**，方向可以一键对调。正文是追加不是智能合并：两段讲同一件事的话
 * 怎么揉只有你知道，这里只保证内容不丢。
 */
import { computed, onBeforeUnmount, onMounted } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  pair: { type: Object, required: true },      // { keep, drop }
  impact: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['preview', 'apply', 'swap', 'goto', 'close'])

const rows = computed(() => {
  const i = props.impact
  if (!i) return []
  return [
    { n: i.moved_edges.length, text: '条关系迁到保留的那张上', show: i.moved_edges.length > 0 },
    { n: i.dropped_edges.length, text: '条关系并完就没意义了', show: i.dropped_edges.length > 0 },
    { n: i.links, text: `处 [[链接]] 改指过来（${i.files.length} 个文件）`, show: i.links > 0 },
    { n: i.body_chars, text: '字正文追加到末尾（标注来源，不智能合并）', show: i.body_chars > 0 },
    { n: i.layout_edges, text: '条边的手工拐点', show: i.layout_edges > 0 },
    { n: i.refs, text: '张引用卡', show: i.refs > 0 },
    { n: i.reviews, text: '次复习记录并进来', show: i.reviews > 0 },
    { n: i.quiz, text: '条答题记录', show: i.quiz > 0 },
    { n: i.projects.length, text: `个项目（${i.projects.join('、')}）`, show: i.projects.length > 0 },
  ].filter((r) => r.show)
})

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog node-dialog">
    <header>
      <Icon name="layers" :size="15" />
      <span class="who">合并重复</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
    </header>

    <div class="rel-body">
      <div class="merge-dir">
        <div class="side keep">
          <span class="tag">保留</span>
          <span class="link" @click="emit('goto', pair.keep)">{{ pair.keep }}</span>
        </div>
        <button class="icon-btn ghost" title="对调：改成保留另一张" @click="emit('swap')">
          <Icon name="arrowLeft" :size="15" />
        </button>
        <div class="side drop">
          <span class="tag">丢弃</span>
          <span class="link" @click="emit('goto', pair.drop)">{{ pair.drop }}</span>
        </div>
      </div>
      <p class="dim" style="font-size: 11.5px; line-height: 1.6">
        丢弃的那张会被删掉，但它的关系、引用、正文、复习记录都并到保留的那张上。
      </p>

      <template v-if="impact">
        <div class="quiz-label">这次会动到</div>
        <div v-if="rows.length" class="card" style="padding: 10px 12px">
          <div v-for="(r, i) in rows" :key="i" class="rename-row">
            <b class="tnum">{{ r.n }}</b> {{ r.text }}
          </div>
        </div>
        <p v-else class="dim" style="font-size: 12px">两张卡都很空，只会删掉一个文件。</p>

        <div v-if="impact.dropped_edges.length" class="sum-line" style="flex-direction: column; align-items: stretch">
          <span class="sum-tag bad" style="align-self: flex-start">会丢掉的关系</span>
          <div v-for="(d, i) in impact.dropped_edges" :key="i" class="dim" style="font-size: 11.5px">{{ d }}</div>
        </div>
      </template>

      <footer>
        <span class="dim">{{ impact ? '确认无误再合并' : '先看看会动到什么' }}</span>
        <button v-if="!impact" class="btn primary" :disabled="busy" @click="emit('preview')">
          <Icon name="eye" :size="14" />{{ busy ? '计算中…' : '看影响' }}
        </button>
        <button v-else class="btn primary" :disabled="busy" @click="emit('apply')">
          <Icon name="check" :size="14" />{{ busy ? '合并中…' : '确认合并' }}
        </button>
      </footer>
    </div>
  </div>
</template>
