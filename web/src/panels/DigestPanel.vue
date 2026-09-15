<script setup>
// 欠账清单：图谱里"还没处理完"的东西。每一条都能点，点了就在画布上定位过去。
//
// 只管图谱本身的欠账（草稿 / 桥 / 重复 / stub）。"今天该复习什么、哪些老错"
// 是我脑子里的欠账，在「学习」面板，不在这里重复一遍。
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({ digest: { type: Object, default: null } })
const emit = defineEmits(['goto', 'close', 'refresh', 'merge'])

const total = () => {
  const c = props.digest?.counts
  if (!c) return 0
  return (c.drafts || 0) + (c.duplicates || 0) + (c.stubs || 0) + (c.cycles || 0)
}
</script>

<template>
  <Drawer side="left" title="欠账清单" icon="checklist" storage-key="digest" :default-width="312"
          @close="emit('close')">
    <template #head-actions>
      <span v-if="digest" class="head-count">{{ total() }}</span>
      <button class="icon-btn ghost tiny" title="重新统计" @click="emit('refresh')">
        <Icon name="refresh" :size="14" />
      </button>
    </template>

    <template #default>
      <p v-if="!digest" class="dim">统计中…</p>
      <template v-else>
        <section v-if="digest.drafts.length" class="section" style="margin-top: 0">
          <div class="section-head">
            <Icon name="pencil" :size="13" />草稿 <span class="count">{{ digest.counts.drafts }}</span>
            <span v-if="digest.counts.stale_drafts" class="tail warn-text">
              {{ digest.counts.stale_drafts }} 个放太久
            </span>
          </div>
          <ul>
            <li v-for="d in digest.drafts" :key="d.id" class="edge-row">
              <span class="to link" :class="{ 'warn-text': d.stale }" @click="emit('goto', d.id)">{{ d.id }}</span>
              <span class="yr">{{ d.days ?? '?' }} 天</span>
            </li>
          </ul>
        </section>

        <section v-if="digest.bridges.length" class="section">
          <div class="section-head">
            <Icon name="link" :size="13" />跨分组的桥 <span class="count">{{ digest.counts.bridges }}</span>
          </div>
          <p class="dim" style="font-size: 11.5px; margin-bottom: 6px">连接两个领域的关系，通常最值得整理。</p>
          <ul>
            <li v-for="b in digest.bridges" :key="`${b.from}->${b.to}`" class="edge-row">
              <span class="to">{{ b.from_name }} → {{ b.to_name }}</span>
              <span class="yr">{{ b.count }} 条</span>
            </li>
          </ul>
        </section>

        <section v-if="digest.duplicates.length" class="section">
          <div class="section-head">
            <Icon name="layers" :size="13" />重复候选 <span class="count">{{ digest.counts.duplicates }}</span>
          </div>
          <ul>
            <li v-for="x in digest.duplicates" :key="`${x.a}|${x.b}`" class="card" style="padding: 8px 10px">
              <div>
                <span class="link" @click="emit('goto', x.a)">{{ x.a }}</span>
                <span class="dim"> / </span>
                <span class="link" @click="emit('goto', x.b)">{{ x.b }}</span>
              </div>
              <div class="dim" style="font-size: 11.5px">{{ x.reason }}</div>
              <button class="btn subtle tiny" style="margin-top: 6px"
                      title="并成一张：关系、引用、正文、复习记录都迁过去"
                      @click="emit('merge', { keep: x.a, drop: x.b })">
                <Icon name="layers" :size="12" />合并
              </button>
            </li>
          </ul>
        </section>

        <section v-if="digest.stubs.length" class="section">
          <div class="section-head">
            <Icon name="file" :size="13" />待补的 stub <span class="count">{{ digest.counts.stubs }}</span>
          </div>
          <ul>
            <li v-for="id in digest.stubs" :key="id" class="edge-row">
              <span class="to link" @click="emit('goto', id)">{{ id }}</span>
            </li>
          </ul>
        </section>

        <section v-if="digest.cycles.length" class="section">
          <div class="section-head warn-text">
            <Icon name="warn" :size="13" />方向矛盾 <span class="count">{{ digest.counts.cycles }}</span>
          </div>
          <ul>
            <li v-for="(m, i) in digest.cycles" :key="i" class="card warn-text">{{ m }}</li>
          </ul>
        </section>

        <div v-if="!total()" class="empty">
          <Icon name="check" :size="30" :width="1.3" />
          <span class="t">没有欠账</span>
          <span class="s">草稿、桥、重复和 stub 都清空了。</span>
        </div>

        <p class="dim" style="font-size: 11px; margin-top: 16px">统计于 {{ digest.generated_at }}</p>
      </template>
    </template>
  </Drawer>
</template>
