<script setup>
/**
 * 设置：居中弹窗，左边分类、右边开关。把散在顶栏菜单、画布浮层、活动栏底部的开关收到一处。
 *
 * **存储按语义分三处，界面上不分**：
 * - 「学习与复习」「写入审核」「模型」跟着人走（`~/.knowrary/`）——教练的系统提示词在服务端拼，
 *   只存浏览器的话，界面安静了、教练照样每轮开场播报欠账、结尾出检验题。
 * - 「知识库」里的原文目录跟着库走（`<库>/.knowrary/vault.json`，进 git）——那是库的结构，
 *   记在人身上的话，库推给别人之后原文链接全断。
 * - 「画布 / 外观」留 localStorage——那是"这台机器上怎么看图"，换台机器本来就该各看各的。
 * 分界写在每组标题下面给人看，免得以后问"为什么有的设置跟过来了、有的没有"。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  settings: { type: Object, default: () => ({}) },   // 跟着 vault 的那几个
  snap: { type: Boolean, default: true },
  avoidNodes: { type: Boolean, default: false },
  autoLod: { type: Boolean, default: true },
  aggregate: { type: Boolean, default: true },
  showMap: { type: Boolean, default: true },
  theme: { type: String, default: 'light' },
  llmConfig: { type: Object, default: null },
  llmSaving: { type: Boolean, default: false },
  // 名字只能这么拼：模板上写的是 `:save-llm`，Vue 把它 camelize 成 `saveLlm`。
  // 声明成 `saveLLM` 的话永远对不上，props 里拿到的是 default null——
  // 于是「测试」静悄悄 return、「保存配置」报「保存接口不可用」。
  saveLlm: { type: Function, default: null },
  testLlm: { type: Function, default: null },
  // 知识库：这几个读写的是用户级配置，不属于任何一个库。名字全用单个小写词，
  // 免得再撞上 `saveLLM` 那种 camelize 对不上的坑（见上面那条注释）。
  vault: { type: Object, default: null },
  browseVault: { type: Function, default: null },
  pickVault: { type: Function, default: null },
  startVault: { type: Function, default: null },
  forgetVault: { type: Function, default: null },
  // 当前库自己的配置（.knowrary/vault.json）：原文目录。和上面那几个「用哪个库」的用户级选择不是一层
  readVaultConfig: { type: Function, default: null },
  saveVaultConfig: { type: Function, default: null },
})
const emit = defineEmits(['close', 'set', 'toggle-snap', 'toggle-avoid', 'toggle-lod',
                          'toggle-aggregate', 'toggle-map', 'toggle-theme'])

const TABS = [
  { id: 'vault', name: '知识库', icon: 'folder', where: '用哪个库记在这台机器上；原文目录写在库里，跟着 git 走' },
  { id: 'review', name: '学习与复习', icon: 'rotate', where: '跟着人走（~/.knowrary），换个库也一样' },
  { id: 'audit', name: '写入审核', icon: 'checklist', where: '跟着人走（~/.knowrary），换个库也一样' },
  // 画布和外观合成一栏：两边都是 localStorage、都是"这台机器上怎么看图"，
  // 而「外观」里只有一个深色主题开关——一个开关不值得单开一栏，点进去只会觉得空。
  { id: 'canvas', name: '画布与外观', icon: 'map', where: '只存在这台机器上' },
  { id: 'models', name: '模型', icon: 'cube', where: '跟着人走（~/.knowrary/llm.local.json）' },
]
const tab = ref('review')
const draft = ref({ providers: [], roles: {} })
const error = ref('')
const editor = ref(null)
const editorIndex = ref(-1)
const testBusy = ref(false)
const testMessage = ref('')
const testError = ref('')
const listTesting = ref('')
const listTestMessage = ref('')
const listTestError = ref('')
const ROLE_NAMES = ['learn', 'review']

function copyConfig(config) {
  if (!config) return { providers: [], roles: {} }
  const providers = (config.providers || []).map((p) => ({ ...p, api_key: '', prev_name: undefined }))
  const sourceRoles = config.roles || {}
  const fallback = providers[0]?.name || ''
  return { providers, roles: Object.fromEntries(ROLE_NAMES.map((role) => [role, sourceRoles[role] || fallback])) }
}
watch(() => props.llmConfig, (config) => { draft.value = copyConfig(config) }, { immediate: true })

function addProvider() {
  const n = `provider-${draft.value.providers.length + 1}`
  editorIndex.value = -1
  editor.value = { name: n, type: 'openai', model: '', base_url: '', api_key: '', max_tokens: null, temperature: null }
  testMessage.value = ''
  testError.value = ''
  error.value = ''
}
function editProvider(index) {
  editorIndex.value = index
  editor.value = { ...draft.value.providers[index], api_key: '' }
  testMessage.value = ''
  testError.value = ''
  error.value = ''
}
function closeEditor() {
  if (!testBusy.value) editor.value = null
}
function validateProvider(provider) {
  if (!provider.name?.trim()) return '请填写 provider 名称'
  if (!provider.model?.trim() && provider.type !== 'claude-cli') return '请填写模型名称'
  if (provider.type === 'openai' && !provider.base_url?.trim()) return 'OpenAI 兼容类型需要 Base URL'
  return ''
}
async function testProvider(provider = null) {
  const target = provider || editor.value
  const listMode = Boolean(provider)
  const targetError = validateProvider(target)
  if (listMode) {
    listTestMessage.value = ''
    listTestError.value = targetError
    if (targetError || !props.testLlm) return
    listTesting.value = target.name
  } else {
    testMessage.value = ''
    testError.value = targetError
    if (targetError || !props.testLlm) return
    testBusy.value = true
  }
  try {
    const result = await props.testLlm({ ...target })
    if (listMode) listTestMessage.value = `连接成功：${result.message || '已收到响应'}`
    else testMessage.value = `连接成功：${result.message || '已收到响应'}`
  } catch (err) {
    if (listMode) listTestError.value = err.body?.detail || err.message
    else testError.value = err.body?.detail || err.message
  } finally {
    if (listMode) listTesting.value = ''
    else testBusy.value = false
  }
}
async function saveProvider() {
  const validationError = validateProvider(editor.value)
  error.value = validationError
  if (validationError) return
  const next = { ...editor.value }
  const providers = draft.value.providers.slice()
  const oldName = editorIndex.value < 0 ? '' : providers[editorIndex.value]?.name
  // 改名时把旧名字一起交上去：服务端保留密钥是按名字找旧记录的，不说清就找不到，
  // 密钥会静默消失（api_key 留空本意是"别动它"）。
  if (oldName && oldName !== next.name) next.prev_name = oldName
  if (editorIndex.value < 0) providers.push(next)
  else providers.splice(editorIndex.value, 1, next)
  const saved = await saveModels(providers, renameInRoles(draft.value.roles, oldName, next.name))
  if (!saved) return
  draft.value = copyConfig(saved)
  editor.value = null
}
function removeProvider(index) {
  const name = draft.value.providers[index]?.name
  draft.value.providers.splice(index, 1)
  const fallback = draft.value.providers[0]?.name || ''
  ROLE_NAMES.forEach((role) => {
    if (draft.value.roles[role] === name) draft.value.roles[role] = fallback
  })
}
/** 改名等于换了一个 provider：roles 还指着旧名字，服务端会以「角色 X 指向不存在的 provider」打回。 */
function renameInRoles(roles, oldName, newName) {
  if (!oldName || oldName === newName) return roles
  return Object.fromEntries(Object.entries(roles).map(([role, name]) =>
    [role, name === oldName ? newName : name]))
}
function ensureRoles(roles, providers) {
  const fallback = providers[0]?.name || ''
  return Object.fromEntries(ROLE_NAMES.map((role) => [role, roles[role] || fallback]))
}
async function saveModels(providers = draft.value.providers, roles = draft.value.roles) {
  error.value = ''
  if (!providers.length) { error.value = '至少保留一个 provider'; return null }
  const nextRoles = ensureRoles(roles, providers)
  if (Object.values(nextRoles).some((name) => !name)) { error.value = '每个 role 都要选择 provider'; return null }
  if (!props.saveLlm) { error.value = '保存接口不可用'; return null }
  try { return await props.saveLlm({ providers, roles: nextRoles }) }
  catch (err) { error.value = err.body?.detail || err.message; return null }
}

// ---------------------------------------------------------------- 知识库
const picker = ref(null)          // 目录选择器的当前一页；null = 没打开
const withSample = ref(false)     // 新建库时顺带铺一份示例内容
const vaultBusy = ref(false)
const vaultError = ref('')
const STATUS_TEXT = {
  vault: '已经是知识库',
  empty: '空目录，可以初始化成新库',
  occupied: '目录里有别的东西——换一个空目录，免得往别人的工程里撒文件',
  program: '这是 Knowrary 程序自己的目录，不能当知识库——知识库要另挑一个地方',
  missing: '目录不存在',
}

async function openPicker(path = null) {
  vaultError.value = ''
  if (!props.browseVault) { vaultError.value = '浏览接口不可用'; return }
  vaultBusy.value = true
  try { picker.value = await props.browseVault(path) }
  catch (err) { vaultError.value = err.body?.detail || err.message }
  finally { vaultBusy.value = false }
}

/** 选定当前这一层目录：已经是库就直接切过去，空目录先初始化再切。 */
async function useHere() {
  const here = picker.value
  if (!here || (here.status !== 'vault' && here.status !== 'empty')) return
  if (here.status === 'vault') await run(props.pickVault, here.path)
  else await run(props.startVault, here.path, withSample.value)
}

async function switchTo(path) {
  await run(props.pickVault, path)
}

/** 切库会换掉整张图、项目、对话，前端没有一处状态还作数——所以成功后整页重载。 */
async function run(action, path, sample = false) {
  vaultError.value = ''
  if (!action) { vaultError.value = '切换接口不可用'; return }
  vaultBusy.value = true
  try {
    await action(path, sample)     // 成功即整页重载（App 那侧做），下面这行基本走不到
    picker.value = null
  } catch (err) {
    vaultError.value = err.body?.detail || err.message
  } finally {
    vaultBusy.value = false
  }
}

// —— 原文目录：这个库的长文放在哪（库内相对路径，写在库的 .knowrary/vault.json 里）——
const artCfg = ref(null)          // 服务端读回来的：{ articles_dir, articles, candidates }
const artDraft = ref('')
const artCheck = ref({ ok: true, message: '', needMove: 0 })   // 边打边问服务端（dry_run）的结果
const artBusy = ref(false)
const artError = ref('')
const artDone = ref('')
let artTimer = null

async function loadArticlesDir() {
  if (!props.readVaultConfig || !props.vault?.current) return
  try {
    artCfg.value = await props.readVaultConfig()
    artDraft.value = artCfg.value.articles_dir
  } catch (err) {
    artError.value = `读不到原文目录：${err.body?.detail?.message || err.message}`
  }
}
watch(() => props.vault?.current?.path, loadArticlesDir, { immediate: true })
onBeforeUnmount(() => clearTimeout(artTimer))

const artChanged = computed(() => !!artCfg.value && artDraft.value.trim() !== artCfg.value.articles_dir)

/** 规则只在服务端写一份：这里防抖去问一句 dry_run，不在前端再抄一遍校验。 */
watch(artDraft, (text) => {
  clearTimeout(artTimer)
  artDone.value = ''
  if (!artChanged.value) { artCheck.value = { ok: true, message: '', needMove: 0 }; return }
  artTimer = setTimeout(async () => {
    try {
      await props.saveVaultConfig({ articles_dir: text, dry_run: true })
      artCheck.value = { ok: true, message: '', needMove: 0 }
    } catch (err) {
      const d = err.body?.detail || {}
      artCheck.value = err.status === 409 ? { ok: true, message: '', needMove: d.articles || 0 }
        : { ok: false, message: d.message || err.message, needMove: 0 }
    }
  }, 300)
})

async function saveArticlesDir(move = false) {
  if (!props.saveVaultConfig || !artChanged.value || !artCheck.value.ok) return
  artBusy.value = true
  artError.value = ''
  try {
    const got = await props.saveVaultConfig({ articles_dir: artDraft.value, move })
    const from = artCfg.value.articles_dir
    artCfg.value = got
    artDraft.value = got.articles_dir
    artCheck.value = { ok: true, message: '', needMove: 0 }
    artDone.value = got.moved?.length
      ? `已把 ${got.moved.length} 篇从 ${from}/ 搬到 ${got.articles_dir}/，搬之前的快照在 ${got.backup}`
      : `原文目录改成了 ${got.articles_dir}/`
  } catch (err) {
    const d = err.body?.detail || {}
    if (err.status === 409) artCheck.value = { ok: true, message: '', needMove: d.articles || 0 }
    else artError.value = d.message || err.message
  } finally {
    artBusy.value = false
  }
}

async function forget(path) {
  vaultError.value = ''
  if (!props.forgetVault) return
  try { await props.forgetVault(path) }
  catch (err) { vaultError.value = err.body?.detail || err.message }
}

function onKey(ev) {
  if (ev.key !== 'Escape') return
  ev.stopPropagation()
  if (editor.value) closeEditor()
  else if (picker.value) picker.value = null
  else emit('close')
}
onMounted(() => document.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <!-- 蒙层：点空白处关掉。设置是"看一眼改一下就走"的东西，不该要求人去找那个 ✕ -->
  <div class="set-mask" @click.self="emit('close')">
    <div class="set-dialog" role="dialog" aria-label="设置">
      <nav class="set-nav">
        <div class="set-brand"><Icon name="grid" :size="14" />设置</div>
        <button v-for="t in TABS" :key="t.id" class="set-tab" :class="{ on: tab === t.id }"
                @click="tab = t.id">
          <Icon :name="t.icon" :size="15" />{{ t.name }}
        </button>
      </nav>

      <section class="set-main">
        <header>
          <span class="who">{{ TABS.find((t) => t.id === tab).name }}</span>
          <span class="dim">{{ TABS.find((t) => t.id === tab).where }}</span>
          <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')">
            <Icon name="x" :size="14" />
          </button>
        </header>

        <div class="set-body">
          <template v-if="tab === 'vault'">
            <div class="vault-now">
              <span class="sub">当前知识库</span>
              <b v-if="vault?.current">{{ vault.current.path }}</b>
              <b v-else class="vault-none">还没有选择——挑一个目录开始</b>
            </div>
            <template v-if="vault?.current && artCfg">
              <div class="model-head">
                <div><b>原文目录</b><span class="sub">长文原样存在这儿：不上图、不进复习，节点用 sources 链回来。
                  写在这个库的 <code>.knowrary/vault.json</code> 里，跟着 git 走。</span></div>
              </div>
              <div class="art-row">
                <input v-model="artDraft" class="art-input" list="art-dir-options" spellcheck="false"
                       placeholder="articles" :class="{ bad: !artCheck.ok }" aria-label="原文目录"
                       @keydown.enter.prevent="artCheck.needMove ? null : saveArticlesDir()" />
                <datalist id="art-dir-options">
                  <option v-for="d in artCfg.candidates" :key="d" :value="d" />
                </datalist>
                <button class="btn tiny primary" type="button"
                        :disabled="artBusy || !artChanged || !artCheck.ok || !!artCheck.needMove"
                        @click="saveArticlesDir()">保存</button>
              </div>
              <p v-if="!artCheck.ok" class="model-error">{{ artCheck.message }}</p>
              <div v-else-if="artCheck.needMove" class="art-move">
                <span>旧目录 <code>{{ artCfg.articles_dir }}/</code> 里还有 <b>{{ artCheck.needMove }}</b> 篇原文，
                  要一起搬到 <code>{{ artDraft.trim() }}/</code> 吗？搬之前会先整份备份。</span>
                <button class="btn tiny primary" type="button" :disabled="artBusy"
                        @click="saveArticlesDir(true)">{{ artBusy ? '搬家中…' : '一起搬过去' }}</button>
                <button class="btn tiny subtle" type="button" :disabled="artBusy"
                        @click="artDraft = artCfg.articles_dir">取消</button>
              </div>
              <p v-else class="sub art-count">
                {{ artChanged ? `保存后原文放进 ${artDraft.trim()}/（不存在会自动建）`
                  : `现在 ${artCfg.articles_dir}/ 里有 ${artCfg.articles} 篇原文` }}</p>
              <p v-if="artDone" class="art-done">{{ artDone }}</p>
              <p v-if="artError" class="model-error">{{ artError }}</p>
            </template>

            <p v-if="vault?.pinned" class="model-error">
              当前库被环境变量 <code>KNOWRARY_VAULT</code> 钉住了，<b>在这里切换不会生效</b>——
              它的优先级高于这份选择。去掉这个变量（或用 <code>./server/dev.sh</code> 重起服务）再来。
            </p>

            <div class="model-head">
              <div><b>最近使用</b><span class="sub">切过去之后整页会重新加载——图、项目、对话都跟着库走。</span></div>
              <button class="btn tiny" type="button" :disabled="vaultBusy || vault?.pinned"
                      :title="vault?.pinned ? '被 KNOWRARY_VAULT 钉住了，切了也不生效' : '选一个目录当知识库'"
                      @click="openPicker()">
                <Icon name="folder" :size="13" />选择目录…
              </button>
            </div>
            <div v-if="!vault?.recent?.length" class="empty-hint">还没有用过别的库。</div>
            <div v-for="item in vault?.recent || []" :key="item.path" class="model-list-row">
              <div class="model-list-name"><b>{{ item.name }}</b><span>{{ item.path }}</span></div>
              <div class="model-list-actions">
                <button class="btn tiny" type="button"
                        :disabled="vaultBusy || vault?.pinned || item.status !== 'vault'"
                        :title="vault?.pinned ? '被 KNOWRARY_VAULT 钉住了，切了也不生效'
                          : item.status === 'vault' ? '切换到这个库' : STATUS_TEXT[item.status]"
                        @click="switchTo(item.path)">切换</button>
                <button class="icon-btn ghost danger" type="button" title="从列表里移除（不会删目录）"
                        @click="forget(item.path)"><Icon name="x" :size="14" /></button>
              </div>
            </div>
            <p v-if="vaultError" class="model-error">{{ vaultError }}</p>
          </template>

          <template v-else-if="tab === 'review'">
            <label class="switch-row">
              <input type="checkbox" :checked="settings.review_enabled"
                     @change="emit('set', { review_enabled: !settings.review_enabled })" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">复习与出题
                <span class="sub">总闸。关掉后「今日」分栏里的到期与错题一并收起，出题范围也跟着空；
                  复习记录照常积累，只是整套不再出现</span></span>
            </label>
            <label class="switch-row" :class="{ muted: !settings.review_enabled }">
              <input type="checkbox" :disabled="!settings.review_enabled" :checked="settings.review_in_chat"
                     @change="emit('set', { review_in_chat: !settings.review_in_chat })" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">教练会考我
                <span class="sub">关掉后教练不再开场播报欠账、不再在结尾出检验题，出题和记复习的工具直接收走。
                  <b>「今日」分栏里照常能复习</b>——那是专心复习的地方，不受这一档影响</span></span>
            </label>
            <label class="switch-row" :class="{ muted: !settings.review_enabled }">
              <input type="checkbox" :disabled="!settings.review_enabled" :checked="settings.review_brief"
                     @change="emit('set', { review_brief: !settings.review_brief })" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">晨间简报<span class="sub">每天第一次打开时弹出今天该干什么</span></span>
            </label>
            <label class="switch-row" :class="{ muted: !settings.review_enabled }">
              <input type="checkbox" :disabled="!settings.review_enabled" :checked="settings.review_marks"
                     @change="emit('set', { review_marks: !settings.review_marks })" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">到期标记<span class="sub">画布上的金色小圆点、活动栏「今日」的角标</span></span>
            </label>
          </template>

          <template v-else-if="tab === 'audit'">
            <p class="dim" style="font-size: 12.5px; line-height: 1.7; margin: 0 0 12px">
              往 md 里写内容之前，先让 <b>review 角色</b>看一眼对不对。
              <br>注意这里的 review 是<b>审校</b>，和上一栏的「复习」不是一回事。
            </p>
            <label class="switch-row">
              <input type="checkbox" :checked="settings.audit_enabled"
                     @change="emit('set', { audit_enabled: !settings.audit_enabled })" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">写入前过一遍审核
                <span class="sub">只审<b>正文类</b>写入（新建节点、补正文、重写正文）——加一条边、改一个年份不审，
                  否则每点一下都卡几秒。每次审核 = 一次 review 角色调用</span></span>
            </label>
            <label class="switch-row" :class="{ muted: !settings.audit_enabled }">
              <input type="checkbox" :disabled="!settings.audit_enabled"
                     :checked="settings.audit_force_allowed"
                     @change="emit('set', { audit_force_allowed: !settings.audit_force_allowed })" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">允许「仍然写入」
                <span class="sub">模型也会看走眼。不留这个后门，你迟早把整套审核关掉——那就一条都不查了。
                  每次强制写入都会记进 <code>.knowrary/issues.jsonl</code></span></span>
            </label>
            <p class="dim" style="font-size: 11.5px; line-height: 1.7; margin-top: 14px">
              <b>关掉它不等于什么都不查。</b>未登记的关系类型、正文里指不到的
              <code>[[链接]]</code>、正文太薄、新点一条边都没有、名字和已有节点撞车——
              这些是算出来的，不花钱也不用等，<b>照常查、照常显示在卡片上，只是不拦路</b>。
            </p>
          </template>

          <template v-else-if="tab === 'canvas'">
            <label class="switch-row">
              <input type="checkbox" :checked="snap" @change="emit('toggle-snap')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">对齐吸附<span class="sub">拖动出参考线，松手贴 8px 网格</span></span>
            </label>
            <label class="switch-row">
              <input type="checkbox" :checked="avoidNodes" @change="emit('toggle-avoid')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">连线绕开卡片<span class="sub">直角走线；手工拐过的边不受影响</span></span>
            </label>
            <label class="switch-row">
              <input type="checkbox" :checked="autoLod" @change="emit('toggle-lod')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">自动折叠<span class="sub">缩小时分组收成簇卡片</span></span>
            </label>
            <label class="switch-row">
              <input type="checkbox" :checked="aggregate" @change="emit('toggle-aggregate')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">跨组边聚合<span class="sub">组与组之间的边并成一条粗线</span></span>
            </label>
            <label class="switch-row">
              <input type="checkbox" :checked="showMap" @change="emit('toggle-map')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">小地图<span class="sub">右下角那块缩略图</span></span>
            </label>
            <label class="switch-row">
              <input type="checkbox" :checked="theme === 'dark'" @change="emit('toggle-theme')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">深色主题<span class="sub">和上面几个一样，只跟着这台机器，不进 vault</span></span>
            </label>
          </template>

          <template v-else>
            <div class="model-head">
              <div><b>模型</b><span class="sub">列表只显示模型名；密钥只在编辑时写入，不会回显。</span></div>
              <button class="btn tiny" type="button" @click="addProvider"><Icon name="plus" :size="13" />添加模型</button>
            </div>
            <div v-if="!draft.providers.length" class="empty-hint">还没有 provider，先新增一个。</div>
            <div v-for="(provider, i) in draft.providers" :key="i" class="model-list-row">
              <div class="model-list-name"><b>{{ provider.model || provider.name }}</b><span>{{ provider.type }}</span></div>
              <div class="model-list-actions">
                <button class="icon-btn ghost" type="button" :title="listTesting === provider.name ? '测试中…' : '测试模型'" :disabled="Boolean(listTesting)" @click="testProvider(provider)"><Icon name="play" :size="14" /></button>
                <button class="icon-btn ghost" type="button" title="编辑模型" @click="editProvider(i)"><Icon name="pencil" :size="14" /></button>
                <button class="icon-btn ghost danger" type="button" title="删除模型" @click="removeProvider(i)"><Icon name="trash" :size="14" /></button>
              </div>
            </div>
            <p v-if="listTestError" class="model-error">{{ listTestError }}</p><p v-if="listTestMessage" class="model-success">{{ listTestMessage }}</p>
            <div class="model-head roles-head"><div><b>Roles</b><span class="sub">固定角色用于聊天与复习。</span></div></div>
            <div v-for="role in ROLE_NAMES" :key="role" class="role-row">
              <span class="role-name">{{ role }}</span>
              <select v-model="draft.roles[role]" aria-label="role provider"><option v-for="provider in draft.providers" :key="provider.name" :value="provider.name">{{ provider.name || '未命名' }}</option></select>
            </div>
            <p v-if="error" class="model-error">{{ error }}</p>
            <p class="field-hint model-scope">
              配置存在 <code>{{ llmConfig?.path || '~/.knowrary/llm.local.json' }}</code>，
              <b>所有知识库共用</b>——换一个库不用重配。密钥只写进这个文件，不会进任何知识库。
            </p>
            <div class="model-actions"><button class="btn primary" type="button" :disabled="llmSaving" @click="saveModels()"><Icon name="save" :size="14" />{{ llmSaving ? '保存中…' : '保存配置' }}</button></div>
          </template>
        </div>
      </section>
    </div>

    <div v-if="picker" class="model-editor-mask" @click.self="picker = null">
      <div class="model-editor vault-picker" role="dialog" aria-label="选择知识库目录">
        <header class="model-editor-head">
          <div><b>选择目录</b><span class="dim">{{ picker.path }}</span></div>
          <button class="icon-btn ghost tiny" title="关闭" @click="picker = null"><Icon name="x" :size="14" /></button>
        </header>
        <div class="vault-roots">
          <button v-for="root in picker.roots || []" :key="root.path" class="btn tiny" type="button"
                  :disabled="vaultBusy || picker.path === root.path" :title="root.path"
                  @click="openPicker(root.path)">
            <Icon :name="root.name === '家目录' ? 'home' : 'folder'" :size="12" />{{ root.name }}
          </button>
        </div>
        <div class="model-editor-body vault-list">
          <button class="vault-row up" type="button" :disabled="!picker.parent || vaultBusy"
                  @click="openPicker(picker.parent)">
            <Icon name="arrowLeft" :size="14" /><span class="vault-name">上一级</span>
          </button>
          <div v-if="!picker.entries.length" class="empty-hint">这层没有子目录。</div>
          <button v-for="entry in picker.entries" :key="entry.path" class="vault-row" type="button"
                  :disabled="vaultBusy" @click="openPicker(entry.path)">
            <Icon name="folder" :size="14" />
            <span class="vault-name">{{ entry.name }}</span>
            <span class="vault-flag" :class="entry.status">{{ entry.status === 'vault' ? '已是库'
              : entry.status === 'empty' ? '空' : entry.status === 'program' ? '程序本身' : '有内容' }}</span>
          </button>
        </div>
        <label v-if="picker.status === 'empty'" class="switch-row vault-sample">
          <input v-model="withSample" type="checkbox" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">放一份示例内容
            <span class="sub">9 个节点讲 HTTP 三代演进，覆盖全部 5 类关系，带一张摆好的图和一个学习项目。
              空库里没东西可点，看不出这程序在干什么；不想要随时整片删掉</span></span>
        </label>
        <footer class="model-editor-foot vault-foot">
          <span class="dim">{{ STATUS_TEXT[picker.status] }}</span>
          <button class="btn primary" type="button"
                  :disabled="vaultBusy || (picker.status !== 'vault' && picker.status !== 'empty')"
                  @click="useHere">
            <Icon name="check" :size="14" />{{ picker.status === 'vault' ? '使用这个库' : '选定并初始化' }}
          </button>
        </footer>
      </div>
    </div>

    <div v-if="editor" class="model-editor-mask" @click.self="closeEditor">
      <div class="model-editor" role="dialog" aria-label="模型详情">
        <header class="model-editor-head"><div><b>{{ editorIndex < 0 ? '添加模型' : '编辑模型' }}</b><span class="dim">连接测试不会保存配置</span></div><button class="icon-btn ghost tiny" title="关闭" @click="closeEditor"><Icon name="x" :size="14" /></button></header>
        <div class="model-editor-body">
          <label>Provider 名称<input v-model.trim="editor.name" placeholder="例如 openai" /></label>
          <label>类型<select v-model="editor.type"><option v-for="type in (llmConfig?.provider_types || ['claude-cli', 'anthropic', 'openai'])" :key="type" :value="type">{{ type }}</option></select></label>
          <label>模型名称<input v-model.trim="editor.model" placeholder="例如 gpt-4o / qwen-plus" /></label>
          <label v-if="editor.type === 'openai'">Base URL<input v-model.trim="editor.base_url" placeholder="https://api.openai.com/v1" /></label>
          <label>API Key<input v-model="editor.api_key" type="password" :placeholder="editor.api_key_set ? '已设置，留空保持不变' : 'env:变量名 或明文'" /><span class="field-hint">已有密钥不会回显；留空会保留原值。</span></label>
          <div class="model-advanced"><label>最大 tokens<input v-model.number="editor.max_tokens" type="number" min="1" placeholder="默认" /></label><label>Temperature<input v-model.number="editor.temperature" type="number" min="0" max="2" step="0.1" placeholder="默认" /></label></div>
          <p v-if="testError" class="model-error">{{ testError }}</p><p v-if="testMessage" class="model-success">{{ testMessage }}</p>
          <!-- 保存被服务端打回时，报错得落在人正看着的这一层：外面那条压在蒙层底下，等于没说 -->
          <p v-if="error" class="model-error">{{ error }}</p>
        </div>
        <footer class="model-editor-foot"><button class="btn subtle" type="button" :disabled="testBusy" @click="testProvider()"><Icon name="play" :size="14" />{{ testBusy ? '测试中…' : '测试连接' }}</button><span></span><button class="btn" type="button" :disabled="testBusy || llmSaving" @click="closeEditor">取消</button><button class="btn primary" type="button" :disabled="testBusy || llmSaving" @click="saveProvider"><Icon name="save" :size="14" />保存</button></footer>
      </div>
    </div>
  </div>
</template>
