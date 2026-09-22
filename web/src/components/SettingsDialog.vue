<script setup>
/**
 * 设置：居中弹窗，左边分类、右边开关。把散在顶栏菜单、画布浮层、活动栏底部的开关收到一处。
 *
 * **存储按语义分两边，界面上不分**：
 * - 「学习与复习」跟着 vault 走（`.knowrary/settings.json`）——教练的系统提示词在服务端拼，
 *   只存浏览器的话，界面安静了、教练照样每轮开场播报欠账、结尾出检验题。
 * - 「画布 / 外观」留 localStorage——那是"这台机器上怎么看图"，换台机器本来就该各看各的。
 * 分界写在每组标题下面给人看，免得以后问"为什么有的设置跟过来了、有的没有"。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
})
const emit = defineEmits(['close', 'set', 'toggle-snap', 'toggle-avoid', 'toggle-lod',
                          'toggle-aggregate', 'toggle-map', 'toggle-theme', 'save-llm'])

const TABS = [
  { id: 'review', name: '学习与复习', icon: 'rotate', where: '跟着 vault 走，换台机器也一样' },
  { id: 'canvas', name: '画布', icon: 'map', where: '只存在这台机器上' },
  { id: 'look', name: '外观', icon: 'sun', where: '只存在这台机器上' },
  { id: 'models', name: '模型', icon: 'cube', where: '跟着 vault 走' },
]
const tab = ref('review')
const draft = ref({ providers: [], roles: {} })
const error = ref('')

function copyConfig(config) {
  if (!config) return { providers: [], roles: {} }
  return { providers: (config.providers || []).map((p) => ({ ...p, api_key: '' })),
           roles: { ...(config.roles || {}) } }
}
watch(() => props.llmConfig, (config) => { draft.value = copyConfig(config) }, { immediate: true })

function addProvider() {
  const n = `provider-${draft.value.providers.length + 1}`
  draft.value.providers.push({ name: n, type: 'openai', model: '', base_url: '', api_key: '' })
}
function removeProvider(index) {
  const name = draft.value.providers[index]?.name
  draft.value.providers.splice(index, 1)
  Object.keys(draft.value.roles).forEach((role) => {
    if (draft.value.roles[role] === name) delete draft.value.roles[role]
  })
}
function addRole() {
  const base = 'custom'
  let name = base
  let i = 2
  while (draft.value.roles[name]) name = `${base}-${i++}`
  draft.value.roles[name] = draft.value.providers[0]?.name || ''
}
function removeRole(name) {
  if (['learn', 'review'].includes(name)) return
  delete draft.value.roles[name]
}
function renameRole(oldName, event) {
  const name = event.target.value.trim()
  if (!name || name === oldName || draft.value.roles[name]) {
    event.target.value = oldName
    return
  }
  draft.value.roles[name] = draft.value.roles[oldName]
  delete draft.value.roles[oldName]
}
function saveModels() {
  error.value = ''
  if (!draft.value.providers.length) { error.value = '至少保留一个 provider'; return }
  if (Object.values(draft.value.roles).some((name) => !name)) { error.value = '每个 role 都要选择 provider'; return }
  emit('save-llm', { providers: draft.value.providers, roles: draft.value.roles })
}

function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); emit('close') } }
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
          <template v-if="tab === 'review'">
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
          </template>

          <template v-else-if="tab === 'look'">
            <label class="switch-row">
              <input type="checkbox" :checked="theme === 'dark'" @change="emit('toggle-theme')" />
              <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
              <span class="label">深色主题<span class="sub">跟着这台机器，不进 vault</span></span>
            </label>
          </template>

          <template v-else>
            <div class="model-head">
              <div><b>Providers</b><span class="sub">角色会从这里选择模型。API Key 只写入本地配置，不会回显。</span></div>
              <button class="btn tiny" type="button" @click="addProvider"><Icon name="plus" :size="13" />新增</button>
            </div>
            <div v-if="!draft.providers.length" class="empty-hint">还没有 provider，先新增一个。</div>
            <div v-for="(provider, i) in draft.providers" :key="i" class="model-row">
              <div class="model-grid">
                <label>名称<input v-model.trim="provider.name" placeholder="例如 openai" /></label>
                <label>类型<select v-model="provider.type"><option v-for="type in (llmConfig?.provider_types || ['claude-cli', 'anthropic', 'openai'])" :key="type" :value="type">{{ type }}</option></select></label>
                <label>模型<input v-model.trim="provider.model" placeholder="可留空使用默认" /></label>
                <label v-if="provider.type === 'openai'">Base URL<input v-model.trim="provider.base_url" placeholder="https://.../v1" /></label>
                <label>API Key<input v-model="provider.api_key" type="password" :placeholder="provider.api_key_set ? '已设置，留空保持不变' : 'env:变量名 或明文'" /></label>
              </div>
              <button class="icon-btn ghost danger" type="button" title="删除 provider" @click="removeProvider(i)"><Icon name="trash" :size="14" /></button>
            </div>
            <div class="model-head roles-head"><div><b>Roles</b><span class="sub">固定角色用于聊天与复习，也可以增加自定义角色。</span></div><button class="btn tiny" type="button" @click="addRole"><Icon name="plus" :size="13" />新增</button></div>
            <div v-for="(providerName, role) in draft.roles" :key="role" class="role-row">
              <input :value="role" :readonly="['learn', 'review'].includes(role)" aria-label="role 名称" @change="renameRole(role, $event)" />
              <select v-model="draft.roles[role]" aria-label="role provider"><option v-for="provider in draft.providers" :key="provider.name" :value="provider.name">{{ provider.name || '未命名' }}</option></select>
              <button class="icon-btn ghost danger" type="button" title="删除 role" :disabled="['learn', 'review'].includes(role)" @click="removeRole(role)"><Icon name="trash" :size="14" /></button>
            </div>
            <p v-if="error" class="model-error">{{ error }}</p>
            <div class="model-actions"><button class="btn primary" type="button" :disabled="llmSaving" @click="saveModels"><Icon name="save" :size="14" />{{ llmSaving ? '保存中…' : '保存模型配置' }}</button></div>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>
