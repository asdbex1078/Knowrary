<script setup>
// 欠账清单：图谱里"还没处理完"的东西。每一条都能点，点了就在画布上定位过去。
defineProps({
  digest: { type: Object, default: null },
})
const emit = defineEmits(['goto', 'close', 'refresh', 'review'])

const plural = (n) => (n ? String(n) : '0')
</script>

<template>
  <aside class="digest">
    <h3>欠账清单<button class="mini" title="收起" @click="emit('close')">✕</button></h3>
    <p v-if="!digest" class="muted">加载中…</p>
    <template v-else>
      <div class="row">
        <span class="muted">{{ digest.generated_at }}</span>
        <button class="mini" @click="emit('refresh')">刷新</button>
      </div>

      <section v-if="digest.drafts.length">
        <strong>草稿 {{ plural(digest.counts.drafts) }}
          <span v-if="digest.counts.stale_drafts" class="warn">（{{ digest.counts.stale_drafts }} 个放太久了）</span>
        </strong>
        <ul class="edges">
          <li v-for="d in digest.drafts" :key="d.id" :class="{ warn: d.stale }">
            <a @click="emit('goto', d.id)">{{ d.id }}</a>
            <span class="fam">放了 {{ d.days ?? '?' }} 天</span>
          </li>
        </ul>
      </section>

      <section v-if="digest.due.length">
        <strong>待复习 {{ plural(digest.counts.due) }}</strong>
        <ul class="edges">
          <li v-for="d in digest.due" :key="d.id">
            <a @click="emit('goto', d.id)">{{ d.name }}</a>
            <span class="fam">{{ d.overdue_days ? `逾期 ${d.overdue_days} 天` : '今天' }}</span>
            <button class="mini" title="记一次复习" @click="emit('review', d.id)">✓</button>
          </li>
        </ul>
      </section>

      <section v-if="digest.bridges.length">
        <strong>跨分组的桥 {{ plural(digest.counts.bridges) }}</strong>
        <p class="muted">连接两个领域的关系，通常是最值得整理的地方。</p>
        <ul class="edges">
          <li v-for="b in digest.bridges" :key="`${b.from}->${b.to}`">
            <span>{{ b.from_name }} → {{ b.to_name }}</span>
            <span class="fam">{{ b.count }} 条</span>
          </li>
        </ul>
      </section>

      <section v-if="digest.duplicates.length">
        <strong>重复候选 {{ plural(digest.counts.duplicates) }}</strong>
        <ul class="edges">
          <li v-for="x in digest.duplicates" :key="`${x.a}|${x.b}`">
            <a @click="emit('goto', x.a)">{{ x.a }}</a> / <a @click="emit('goto', x.b)">{{ x.b }}</a>
            <span class="fam">{{ x.reason }}</span>
          </li>
        </ul>
      </section>

      <section v-if="digest.stubs.length">
        <strong>待补的 stub {{ plural(digest.counts.stubs) }}</strong>
        <ul class="edges">
          <li v-for="id in digest.stubs" :key="id"><a @click="emit('goto', id)">{{ id }}</a></li>
        </ul>
      </section>

      <section v-if="digest.cycles.length">
        <strong class="warn">方向矛盾 {{ plural(digest.counts.cycles) }}</strong>
        <ul class="edges"><li v-for="(m, i) in digest.cycles" :key="i" class="warn">{{ m }}</li></ul>
      </section>
    </template>
  </aside>
</template>
