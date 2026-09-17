<script setup>
// 素材：贴 vault 里 assets/ 的图，或现传一张；也能直接加便签。
// 图片只记在 layout.images，md 不受影响。
import { onMounted, ref } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import { fetchAssets, uploadAsset } from '../api.js'

const emit = defineEmits(['pick', 'close', 'error', 'add-note'])
const items = ref([])
const busy = ref(false)
const fileEl = ref(null)

async function refresh() {
  try {
    items.value = (await fetchAssets()).items
  } catch (err) {
    emit('error', `读不到 assets/：${err.message}`)
  }
}
onMounted(refresh)

async function onFile(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  busy.value = true
  try {
    const saved = await uploadAsset(file.name, file)
    await refresh()
    emit('pick', saved.file)
  } catch (err) {
    emit('error', `上传失败：${err.body?.detail || err.message}`)
  } finally {
    busy.value = false
    if (fileEl.value) fileEl.value.value = ''
  }
}
</script>

<template>
  <Drawer side="left" title="素材" icon="image" storage-key="assets" :default-width="292" @close="emit('close')">
    <template #head-actions>
      <button class="btn ghost tiny" title="在视口中心加一张便签" @click="emit('add-note')">
        <Icon name="note" :size="14" />便签
      </button>
    </template>

    <template #default>
      <label class="dropzone">
        <Icon name="image" :size="22" :width="1.4" />
        <span>选一张图上传到 assets/</span>
        <input ref="fileEl" type="file" accept="image/*" :disabled="busy" @change="onFile" />
      </label>

      <p class="dim" style="font-size: 11.5px; line-height: 1.6; margin-bottom: 12px">
        图片存在 vault 的 <code>assets/</code> 里，画布只记它的位置和大小。
      </p>

      <ul v-if="items.length" class="thumbs">
        <li v-for="it in items" :key="it.file" :title="`${it.file}（${Math.round(it.size / 1024)}KB）`"
            @click="emit('pick', it.file)">
          <img :src="it.url" :alt="it.file" loading="lazy" />
          <span class="cap">{{ it.file }}</span>
        </li>
      </ul>
      <div v-else class="empty">
        <Icon name="image" :size="28" :width="1.3" />
        <span class="t">assets/ 里还没有图片</span>
      </div>
    </template>
  </Drawer>
</template>
