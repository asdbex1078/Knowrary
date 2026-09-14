<script setup>
// Inbox：索引里有、画布上还没有的节点。两种上画布的方式——
// 「放进建议分组」交给服务端按邻居投票找空位；拖到画布上则是人工指定落点。
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

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
  <Drawer side="left" title="Inbox" icon="inbox" storage-key="inbox" :default-width="300" @close="emit('close')">
    <template #head-actions>
      <span class="head-count">{{ items.length }}</span>
    </template>

    <template #default>
      <template v-if="items.length">
        <p class="dim" style="font-size: 12.5px; line-height: 1.6">
          写好了但还没放上画布的知识点。放上去是<b>草稿</b>（金色虚线框），确认位置后再定稿。
        </p>
        <button class="btn primary" :disabled="busy" style="width: 100%; justify-content: center; margin: 12px 0"
                @click="emit('placeAll')">
          <Icon name="target" :size="14" />全部按建议放置
        </button>

        <ul>
          <li v-for="it in items" :key="it.id" class="card inbox-item" draggable="true"
              @dragstart="onDragStart($event, it)">
            <div class="nm">
              {{ it.name }}
              <span v-if="it.stub" class="chip" style="font-size: 11px; padding: 1px 7px">stub</span>
            </div>
            <div class="ds">{{ it.desc || '（无摘要）' }}</div>
            <div class="ft">
              <span>{{ it.field || '未指定领域' }}</span>
              <span class="dim">·</span>
              <span>{{ it.degree }} 关系</span>
              <button class="btn tiny" data-act="place" :disabled="busy || !it.suggested_group"
                      :title="it.suggested_group_name ? `放进「${it.suggested_group_name}」` : '判不出分组，需要手动拖'"
                      @click="emit('place', it)">
                {{ it.suggested_group_name || '需手动拖' }}
              </button>
            </div>
          </li>
        </ul>

        <p class="dim" style="font-size: 11.5px; margin-top: 12px; line-height: 1.6">
          也可以直接把条目拖到画布上的分组框里，就放在你松手的位置。
        </p>
      </template>

      <div v-else class="empty">
        <Icon name="inbox" :size="30" :width="1.3" />
        <span class="t">Inbox 是空的</span>
        <span class="s">索引里的节点都已经在画布上了。</span>
      </div>
    </template>
  </Drawer>
</template>
