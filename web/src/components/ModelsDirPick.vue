<script setup>
/**
 * 指定模型目录（2026-09-30）：家族文件（kind: family）放哪儿。抽取入口和模型谱系的空状态共用。
 *
 * 谱系是通用能力，不默认绑在哪个路径上——没设时让人自己挑：库里现有的目录当候选，也能直接填新目录。
 * 格式样例折在下面，想手写家族文件的照着抄。规则（库内相对、不许进 nodes/）只在服务端写一份，这里只转述报错。
 */
import { ref } from 'vue'
import { putModelsDir } from '../api.js'

const props = defineProps({
  candidates: { type: Array, default: () => [] },
  sample: { type: String, default: '' },
  sampleLineage: { type: String, default: '' },   // 谱系.md 的样例（2026-10-12：一级子目录一张图）
  current: { type: String, default: '' },
})
const emit = defineEmits(['saved'])

const dir = ref(props.current || 'models')
const busy = ref(false)
const error = ref('')

async function save() {
  busy.value = true; error.value = ''
  try {
    emit('saved', await putModelsDir(dir.value.trim()))
  } catch (err) {
    error.value = err?.body?.detail?.message || err?.body?.detail || err.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="models-dir" data-models-dir>
    <p class="dim">家族文件放在哪个目录？一个家族一个 md（frontmatter 写 <code>kind: family</code>），和原文放一起也行。
      换目录只换读取位置，不搬文件。</p>
    <div class="models-dir-row">
      <input v-model="dir" list="models-dir-candidates" placeholder="models" data-input="models-dir"
             @keydown.enter="save">
      <datalist id="models-dir-candidates">
        <option v-for="c in candidates" :key="c" :value="c" />
      </datalist>
      <button class="btn primary tiny" :disabled="busy || !dir.trim()" data-act="models-dir-save" @click="save">
        用这个目录</button>
    </div>
    <p v-if="error" class="warn-text">{{ error }}</p>
    <details v-if="sample" class="models-sample">
      <summary>家族文件长什么样</summary>
      <pre>{{ sample }}</pre>
    </details>
    <details v-if="sampleLineage" class="models-sample">
      <summary>一张谱系图怎么声明（子目录里的 谱系.md）</summary>
      <p class="dim">一级子目录就是一张图：<code>大模型/</code>、<code>CPU/</code>、<code>操作系统/</code>。
        <code>谱系.md</code> 写这张图除了公共字段还登记哪些字段，没有它也照样是一张图。</p>
      <pre>{{ sampleLineage }}</pre>
    </details>
  </div>
</template>
