<script setup>
/**
 * 抽成模型谱系（2026-09-30，模型谱系第 2 步）：原文面板里就地走完「指定目录 → 抽 → 预览 → 核对 → 写入」。
 *
 * - 一篇一篇**串行**地抽（learn），每篇都看得见：第几篇、哪一篇、等了几秒、谁在抽；能停（这篇抽完就停）。
 * - 几篇的计划并成一份（`mergeProposals`），按家族列出新增 / 补格 / 冲突 / 可疑，每个型号能勾掉。
 * - 有可疑的格子（数字在原文里没原样出现）才调 review，按篇逐次核对，结论挂在格子上；全对得上不调模型。
 * - 写入只补不覆盖，写前快照；预览之后家族文件被改过就拒，让人重新抽。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchModels, postModelsApply, postModelsExtract, postModelsVerify } from '../api.js'
import { applyBody, mergeProposals, pickedCount, suspectsByArticle } from '../modelsPlan.js'
import Icon from '../ui/Icon.vue'
import ModelPlanRow from './ModelPlanRow.vue'
import ModelsDirPick from './ModelsDirPick.vue'

const props = defineProps({
  articles: { type: Array, required: true },       // [{ path, title }]
})
const emit = defineEmits(['close'])

const stage = ref('loading')          // loading / dir / extract / preview / verify / write / done
const catalog = ref(null)
const run = ref(null)                 // { i, n, title, started, stop, what }
const proposals = ref([])
const failed = ref([])                // [{ title, error }]
const families = ref([])
const checks = ref({})                // `${型号}|${键}` → review 的结论
const result = ref(null)
const error = ref('')
const now = ref(Date.now())
const timer = setInterval(() => { if (run.value) now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(timer))

const waited = computed(() => run.value ? Math.max(0, Math.round((now.value - run.value.started) / 1000)) : 0)
const picked = computed(() => pickedCount(families.value))
const suspects = computed(() => Object.values(suspectsByArticle(families.value)).flat().length)

function msgOf(err) { return err?.body?.detail?.message || err?.body?.detail || err?.message || '失败了' }

onMounted(async () => {
  try { catalog.value = await fetchModels() } catch (err) { error.value = msgOf(err) }
  if (catalog.value?.configured) extractAll()
  else stage.value = 'dir'
})

function onDir(read) { catalog.value = read; extractAll() }

async function extractAll() {
  stage.value = 'extract'
  const n = props.articles.length
  for (let i = 0; i < n; i++) {
    if (run.value?.stop) break
    const a = props.articles[i]
    run.value = { i: i + 1, n, title: a.title, started: Date.now(), stop: run.value?.stop || false, what: 'learn 在抽' }
    try { proposals.value.push(await postModelsExtract(a.path)) } catch (err) { failed.value.push({ title: a.title, error: msgOf(err) }) }
  }
  run.value = null
  families.value = mergeProposals(proposals.value)
  stage.value = 'preview'
  if (suspects.value) verifyAll()
}

async function verifyAll() {
  stage.value = 'verify'
  const groups = Object.entries(suspectsByArticle(families.value))
  for (let i = 0; i < groups.length; i++) {
    const [path, items] = groups[i]
    const title = props.articles.find((a) => a.path === path)?.title || path
    run.value = { i: i + 1, n: groups.length, title, started: Date.now(), what: `review 在核对 ${items.length} 格：` }
    try {
      const got = await postModelsVerify(path, items)
      for (const c of got.checks) checks.value[`${c.model}|${c.key}`] = { ...c, by: got.model }
    } catch (err) { error.value = `核对《${title}》失败：${msgOf(err)}（可以照样写入，可疑的格子自己看一眼）` }
  }
  run.value = null
  stage.value = 'preview'
}

function checksOf(model) { return Object.values(checks.value).filter((c) => c.model === model.name) }
function toggle(model) { model.pick = !model.pick }
function fix(model, fx) { model.fields[fx.key] = fx.value }

async function write() {
  stage.value = 'write'; error.value = ''
  try {
    result.value = await postModelsApply(applyBody(families.value, proposals.value))
    stage.value = 'done'
  } catch (err) {
    error.value = err.status === 409 ? `${msgOf(err)}` : `写入失败：${msgOf(err)}`
    stage.value = 'preview'
  }
}
</script>

<template>
  <div class="models-extract" data-models-extract>
    <header class="models-extract-head">
      <b>抽成模型谱系</b>
      <span class="dim">{{ articles.length }} 篇原文<template v-if="catalog?.dir"> → <code>{{ catalog.dir }}/</code></template></span>
      <button class="icon-btn ghost tiny" style="margin-left: auto" title="不抽了，回到原文列表" data-act="close-extract"
              :disabled="stage === 'write'" @click="emit('close')"><Icon name="x" :size="13" /></button>
    </header>

    <ModelsDirPick v-if="stage === 'dir'" :candidates="catalog?.candidates || []" :sample="catalog?.sample || ''"
                   @saved="onDir" />

    <div v-if="run" class="cc-audit busy" data-models-run>
      <Icon name="clock" :size="13" />
      <span>{{ run.what }}《{{ run.title }}》· 第 {{ run.i }}/{{ run.n }} 篇 · 已等 {{ waited }} 秒</span>
      <button v-if="stage === 'extract'" class="btn subtle tiny" style="margin-left: auto" :disabled="run.stop"
              data-act="stop-extract" @click="run.stop = true">{{ run.stop ? '这篇抽完就停' : '停' }}</button>
    </div>

    <p v-for="f in failed" :key="f.title" class="warn-text">《{{ f.title }}》没抽成：{{ f.error }}</p>
    <p v-if="error" class="warn-text" data-models-error>{{ error }}</p>

    <template v-if="families.length && stage !== 'extract'">
      <section v-for="fam in families" :key="fam.target" class="models-family" :data-family="fam.name">
        <h4>{{ fam.name }} <span class="dim">{{ fam.exists ? '已有' : '新建' }} <code>{{ fam.target }}</code></span></h4>
        <ul>
          <ModelPlanRow v-for="m in fam.models" :key="m.name" :model="m" :checks="checksOf(m)"
                        @toggle="toggle(m)" @fix="fix(m, $event)" />
        </ul>
      </section>
      <div v-if="stage !== 'done'" class="models-extract-foot">
        <span v-if="suspects && stage === 'preview'" class="dim">{{ suspects }} 格数字在原文里没原样出现，review 核对过的结论挂在格子上</span>
        <button class="btn primary" :disabled="!picked || stage !== 'preview'" data-act="write-models" @click="write">
          <Icon name="check" :size="13" />写入 {{ picked }} 个型号</button>
      </div>
    </template>
    <p v-else-if="stage === 'preview'" class="dim">没有要写的：这几篇里的型号目录里都有了，或者没抽出型号。</p>

    <div v-if="stage === 'done'" class="art-note" data-models-done>
      写好了：{{ result.models }} 个型号，写进 {{ result.files.map((f) => f).join('、') }}；写前的快照在
      <code>{{ result.backup }}</code>。
    </div>
  </div>
</template>
