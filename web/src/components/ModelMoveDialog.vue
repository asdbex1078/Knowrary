<script setup>
/**
 * 「这是一个型号：挪进模型目录」（2026-09-30）：型号不是知识节点，对话 / 导入又把它建成了点时从这儿挪走。
 *
 * 打开就先算一遍影响面（dry_run）：进哪个家族、补哪几格、正文多少字当笔记、它在哪几张画布 / 哪几个项目里；
 * 家族名能改（默认按名字猜：DeepSeek-V3 → DeepSeek），改了重算。确认才写：写前整份快照，节点删掉。
 * 有别的点链着它时服务端会拒，原话照转——那是要人先去改的地方。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { postModelFromNode } from '../api.js'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  node: { type: Object, required: true },        // { id, name }
})
const emit = defineEmits(['done', 'close'])

const family = ref('')
const plan = ref(null)
const busy = ref(false)
const error = ref('')

function msgOf(err) { return err?.body?.detail?.message || err?.body?.detail || err?.message || '失败了' }

async function preview() {
  busy.value = true; error.value = ''
  try {
    plan.value = await postModelFromNode({ node: props.node.id, family: family.value.trim(), dry_run: true })
    family.value = plan.value.family
  } catch (err) {
    plan.value = null; error.value = msgOf(err)
  } finally {
    busy.value = false
  }
}

async function apply() {
  busy.value = true; error.value = ''
  try {
    emit('done', await postModelFromNode({ node: props.node.id, family: family.value.trim(), dry_run: false }))
  } catch (err) {
    error.value = msgOf(err)
  } finally {
    busy.value = false
  }
}

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
onMounted(() => { document.addEventListener('keydown', onKey, true); preview() })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="rel-dialog node-dialog" data-model-move>
    <header>
      <Icon name="timeline" :size="15" />
      <span class="who">挪进模型目录</span>
      <span class="dim">{{ node.id }}</span>
      <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')"><Icon name="x" :size="14" /></button>
    </header>
    <div class="rel-body">
      <p class="dim" style="font-size: 12px; line-height: 1.6">
        型号不是知识节点：挪过去之后它出现在「谱系 → 模型谱系」里，图上这个点删掉。字段搬过去（目录里已有的格子不动），
        正文当这一节的笔记，一个字不丢。</p>
      <label class="fld">
        <span class="lb">进哪个家族</span>
        <input v-model="family" data-input="model-family" @keydown.enter.prevent="preview" />
        <span class="hint">目录里有同名家族就并进去，没有就新建一个家族文件。</span>
      </label>
      <p v-if="error" class="warn-text" data-model-move-error>{{ error }}</p>
      <template v-if="plan">
        <div class="quiz-label">这次会动到</div>
        <div class="card" style="padding: 10px 12px; font-size: 12px; line-height: 1.7" data-model-move-plan>
          <div><code>{{ plan.target }}</code>（{{ plan.exists ? '已有家族' : '新建家族文件' }}，
            {{ plan.model_exists ? '已有这个型号：只补空格' : '新增这个型号' }}）</div>
          <div v-for="(v, k) in plan.fields" :key="k">+ {{ k }}：{{ v }}</div>
          <div v-if="plan.notes_chars">+ 笔记 {{ plan.notes_chars }} 字（原来的正文，标题降一级）</div>
          <div v-if="plan.layouts.length">− 从 {{ plan.layouts.length }} 张画布上拿掉</div>
          <div v-if="plan.projects.length">− 从项目清单里拿掉：{{ plan.projects.join('、') }}</div>
          <div v-if="plan.reviews">− {{ plan.reviews }} 条复习记录（型号不进复习）</div>
          <div>− 删掉节点文件（写前整份快照到 <code>.knowrary/backup/</code>）</div>
        </div>
      </template>
      <footer>
        <button class="btn subtle" :disabled="busy" data-act="model-move-preview" @click="preview">
          <Icon name="refresh" :size="13" />重算</button>
        <button class="btn primary" :disabled="busy || !plan" data-act="model-move-apply" @click="apply">
          <Icon name="check" :size="14" />{{ busy ? '处理中…' : '确认挪过去' }}</button>
      </footer>
    </div>
  </div>
</template>
