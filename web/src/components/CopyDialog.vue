<script setup>
/**
 * 跨库抄知识点：从一个库挑几个点，抄进另一个库。
 *
 * **两个方向同一个对话框**：上面就是「从 A → 到 B」两个下拉。
 * 画布右键进来时 A 预设成当前库、点也预选好了（往外推）；
 * 「导入」面板进来时 B 预设成当前库、A 默认选另一个（从别处拉）。
 * 做成两套界面的话，两边都要维护一份选点、预览、写入的逻辑，而它们本来就是同一件事。
 *
 * 选完点先 dry-run 预览：要写几个文件、补几个壳、哪几条边要等你确认——
 * 抄东西是**写别人／自己库里的 md**，不该点一下就落盘。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sources: { type: Array, default: () => [] },     // [{path,name,nodes,current}]
  from: { type: String, default: '' },             // 预设源库
  to: { type: String, default: '' },               // 预设目标库
  preset: { type: Array, default: () => [] },      // 预选的节点 id（画布上选中的那些）
  loadCatalog: { type: Function, default: null },
  submit: { type: Function, default: null },
})
const emit = defineEmits(['close', 'done'])

const src = ref(props.from)
const dst = ref(props.to)
const rows = ref([])
const picked = ref(new Set(props.preset))
const query = ref('')
const neighbors = ref(false)
const preview = ref(null)
const busy = ref(false)
const writing = ref(false)
const error = ref('')

const sameVault = computed(() => !!src.value && src.value === dst.value)
const ready = computed(() => !!src.value && !!dst.value && !sameVault.value && picked.value.size > 0)
const nameOf = (path) => props.sources.find((s) => s.path === path)?.name || path

async function loadRows() {
  error.value = ''
  if (!src.value || !props.loadCatalog) return
  busy.value = true
  try {
    rows.value = (await props.loadCatalog(src.value, query.value)).nodes || []
  } catch (err) {
    error.value = err.body?.detail || err.message
  } finally {
    busy.value = false
  }
}

function toggle(id) {
  const next = new Set(picked.value)
  next.has(id) ? next.delete(id) : next.add(id)
  picked.value = next
}

/** 换源库等于换了一批点：之前勾的 id 在新库里多半不存在，留着只会误导。 */
watch(src, () => { picked.value = new Set(); preview.value = null; loadRows() })
watch([picked, neighbors, dst], () => { preview.value = null })

async function run(dryRun) {
  error.value = ''
  if (!ready.value || !props.submit) return
  const flag = dryRun ? busy : writing
  flag.value = true
  try {
    const result = await props.submit({
      source: src.value, target: dst.value, ids: [...picked.value],
      with_neighbors: neighbors.value, dry_run: dryRun,
    })
    preview.value = result
    if (!dryRun) emit('done', result)
  } catch (err) {
    error.value = err.body?.detail || err.message
  } finally {
    flag.value = false
  }
}

function onKey(ev) {
  if (ev.key !== 'Escape') return
  ev.stopPropagation()
  emit('close')
}
onMounted(() => { document.addEventListener('keydown', onKey, true); loadRows() })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div class="set-mask" @click.self="emit('close')">
    <div class="copy-dialog" role="dialog" aria-label="抄知识点">
      <header class="model-editor-head">
        <div><b>抄知识点</b><span class="dim">从一个库挑几个点，抄进另一个库。正文、关系、年份一起带走</span></div>
        <button class="icon-btn ghost tiny" title="关闭（Esc）" @click="emit('close')"><Icon name="x" :size="14" /></button>
      </header>

      <div class="copy-route">
        <label>从
          <select v-model="src" aria-label="源库">
            <option v-for="s in sources" :key="s.path" :value="s.path">{{ s.name }}（{{ s.nodes }} 个点）</option>
          </select>
        </label>
        <Icon name="arrowRight" :size="15" />
        <label>到
          <select v-model="dst" aria-label="目标库">
            <option v-for="s in sources" :key="s.path" :value="s.path">{{ s.name }}{{ s.current ? '（当前）' : '' }}</option>
          </select>
        </label>
      </div>
      <p v-if="sameVault" class="model-error">源库和目标库是同一个，选两个不同的库。</p>

      <div class="copy-pick">
        <input v-model.trim="query" class="copy-search" placeholder="搜节点名 / id / 摘要"
               @keyup.enter="loadRows" />
        <button class="btn tiny" type="button" :disabled="busy" @click="loadRows">
          <Icon name="search" :size="13" />找
        </button>
      </div>

      <div class="copy-list">
        <div v-if="!rows.length" class="empty-hint">{{ busy ? '读取中…' : '这个库里没有匹配的点。' }}</div>
        <label v-for="n in rows" :key="n.id" class="copy-row" :class="{ on: picked.has(n.id) }">
          <input type="checkbox" :checked="picked.has(n.id)" @change="toggle(n.id)" />
          <span class="copy-name">
            <b>{{ n.name }}</b>
            <span class="dim">{{ n.field }}{{ n.status === 'stub' ? ' · 只有壳' : '' }} · {{ n.desc }}</span>
          </span>
        </label>
      </div>

      <label class="switch-row copy-neighbors">
        <input v-model="neighbors" type="checkbox" />
        <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
        <span class="label">把直接相连的邻居也带上
          <span class="sub">不勾的话，指向没选中的点的边不会丢——目标库里会补一个空壳，
            正文留在源库。勾上则把那些邻居也抄成完整节点</span></span>
      </label>

      <div v-if="preview" class="copy-preview">
        <p class="copy-sum">
          抄 <b>{{ preview.picked }}</b> 个点、补 <b>{{ preview.stubs }}</b> 个壳；
          <template v-if="preview.pending?.length">
            <b>{{ preview.pending.length }}</b> 条边指向目标库已有的同名点，先进待审——
            <span class="dim">同 id 不等于同概念，跨库尤其</span>
          </template>
          <span v-else class="dim">没有需要确认的边</span>
        </p>
        <div class="copy-files">
          <div v-for="f in preview.files" :key="f.path" class="copy-file">{{ f.path }}</div>
        </div>
        <p v-for="w in preview.warnings" :key="w" class="field-hint">⚠ {{ w }}</p>
        <p v-if="preview.applied" class="model-success">已写入 {{ nameOf(preview.target_vault) }}。</p>
      </div>
      <p v-if="error" class="model-error">{{ error }}</p>

      <footer class="model-editor-foot">
        <span class="dim">选中 {{ picked.size }} 个</span>
        <button class="btn" type="button" :disabled="!ready || busy || writing" @click="run(true)">
          <Icon name="eye" :size="14" />{{ busy ? '算着…' : '预览' }}
        </button>
        <button class="btn primary" type="button" :disabled="!ready || writing || busy" @click="run(false)">
          <Icon name="copy" :size="14" />{{ writing ? '抄着…' : '抄过去' }}
        </button>
      </footer>
    </div>
  </div>
</template>
