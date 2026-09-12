<script setup>
// Inbox：索引里有、画布上还没有的节点。两种上画布的方式——
// 「放进建议分组」交给服务端按邻居投票找空位；拖到画布上则是人工指定落点。
defineProps({
  items: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['place', 'placeAll', 'close', 'dragStart'])

function onDragStart(ev, item) {
  ev.dataTransfer.effectAllowed = 'copy'
  ev.dataTransfer.setData('text/knowrary-node', item.id)
  emit('dragStart', item)
}
</script>

<template>
  <aside class="inbox">
    <h3>Inbox {{ items.length }}<button class="mini" title="收起" @click="emit('close')">✕</button></h3>
    <p class="muted">写好了但还没放上画布的知识点。放上去是<b>草稿</b>（金色虚线框），确认位置后再定稿。</p>
    <div class="row" v-if="items.length">
      <button class="primary" :disabled="busy" @click="emit('placeAll')">全部按建议放置</button>
    </div>
    <p v-else class="muted">空的 —— 索引里的节点都已经在画布上了。</p>
    <ul class="inbox-list">
      <li v-for="it in items" :key="it.id" draggable="true" @dragstart="onDragStart($event, it)">
        <div class="name">{{ it.name }}<span v-if="it.stub" class="fam"> · stub</span></div>
        <div class="muted">{{ it.desc || '（无摘要）' }}</div>
        <div class="fam">
          {{ it.field || '未指定领域' }} · {{ it.degree }} 条关系 ·
          建议：{{ it.suggested_group_name || '判不出分组，需要手动拖' }}
        </div>
        <button class="mini" :disabled="busy || !it.suggested_group" @click="emit('place', it)">放进去</button>
      </li>
    </ul>
    <p class="muted">提示：直接把条目拖到画布上的某个分组框里，就放在你松手的位置。</p>
  </aside>
</template>
