<script setup>
// 对比组目录：按领域分段，点一条进那个组自己的画布。
//
// **这份目录不是新数据**——服务端从 index 里按 `type: 对比组` 过滤出来现算的，
// 改名、删除自动跟着走。空格子数也是真算了一遍表得出的，不是存下来的计数
// （存计数就一定会有对不上的那天）。
import { computed } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  current: { type: String, default: '' },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'open', 'refresh'])

/** 按领域分段。领域名一样的收在一起，段内按名字排（服务端已经排好，这里只做分组）。 */
const sections = computed(() => {
  const out = []
  for (const it of props.items) {
    const field = it.field || '(未指定)'
    const last = out[out.length - 1]
    if (last && last.field === field) last.rows.push(it)
    else out.push({ field, rows: [it] })
  }
  return out
})

function detail(it) {
  const parts = [`${it.members} 个成员`, `${it.columns.length} 列`]
  if (it.gaps) parts.push(`${it.gaps} 格空`)
  if (it.unbuilt) parts.push(`${it.unbuilt} 个还没建`)
  return parts.join(' · ')
}
</script>

<template>
  <Drawer side="left" title="对比" icon="table" storage-key="compare" :default-width="290"
          @close="emit('close')">
    <template #head-actions>
      <button class="icon-btn ghost" :disabled="busy" title="重新算一遍（成员 md 改过之后）"
              @click="emit('refresh')">
        <Icon name="refresh" :size="15" />
      </button>
    </template>

    <template #default>
      <p class="dim" style="font-size: 12.5px; line-height: 1.6">
        一组技术按同几个维度摆成一张表。点一条进<b>它自己的画布</b>——
        对比组不上全局图，那张图已经够挤了。
      </p>

      <p v-if="!items.length" class="dim" style="font-size: 12px; line-height: 1.7; margin-top: 14px">
        还没有对比组。建一个：在 <code>fields/对比组/</code> 下写一个 md，
        frontmatter 里 <code>type: 对比组</code> 加一行 <code>dimensions</code>，
        正文 <code>## 关系</code> 段里每个成员写一行 <code>- 包含:: [[节点]]</code>。
        <br><br>
        成员顺序就是表的行序——想调顺序去挪那几行。
      </p>

      <div v-for="sec in sections" :key="sec.field" class="section">
        <div class="section-head">{{ sec.field }}</div>
        <ul class="tl-list">
          <li v-for="it in sec.rows" :key="it.id">
            <button class="tl-btn cmp-btn" :class="{ on: it.id === current }" @click="emit('open', it.id)">
              <Icon :name="it.id === current ? 'check' : 'table'" :size="14" />
              <span class="cmp-name">
                {{ it.name }}
                <span class="sub">{{ detail(it) }}</span>
              </span>
            </button>
          </li>
        </ul>
      </div>
    </template>
  </Drawer>
</template>

<style scoped>
.cmp-btn { align-items: flex-start; height: auto; padding-top: 7px; padding-bottom: 7px }
.cmp-name { display: flex; flex-direction: column; gap: 2px; text-align: left; line-height: 1.35 }
.cmp-name .sub { font-size: 10.5px; opacity: .62 }
code { font-size: 11px; padding: 1px 4px; border-radius: 4px; background: var(--chip, rgba(127,127,127,.14)) }
</style>
