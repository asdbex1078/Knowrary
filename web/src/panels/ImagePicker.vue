<script setup>
// 贴图：选 vault 里 assets/ 已有的图，或现传一张。图片只记在 layout.images，md 不受影响。
import { onMounted, ref } from 'vue'
import { fetchAssets, uploadAsset } from '../api'

const emit = defineEmits(['pick', 'close', 'error'])
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
  <aside class="picker">
    <h3>贴一张图<button class="mini" title="收起" @click="emit('close')">✕</button></h3>
    <p class="muted">图片放在 vault 的 <code>assets/</code> 里，画布只记它的位置和大小。</p>
    <div class="row">
      <input ref="fileEl" type="file" accept="image/*" :disabled="busy" @change="onFile" />
    </div>
    <p v-if="!items.length" class="muted">assets/ 里还没有图片。</p>
    <ul class="thumbs">
      <li v-for="it in items" :key="it.file" @click="emit('pick', it.file)">
        <img :src="it.url" :alt="it.file" loading="lazy" />
        <span class="fam">{{ it.file }}（{{ Math.round(it.size / 1024) }}KB）</span>
      </li>
    </ul>
  </aside>
</template>
