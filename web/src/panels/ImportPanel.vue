<script setup>
// 导入：一篇笔记 → 拆成知识点 → 审核卡 → 写入。
//
// 三级匹配在服务端算完（/api/import/propose）：先认领当前项目里没建的点，再看连到全局哪些已有节点，
// 两边都沾不上的标"孤立"。这里只负责把结果摆成一张能改、能重算、能写的卡——
// 形状照着对话里的变更卡来（按文件出 diff、改摘要 / 正文、重算、写入），不另起一套审核 UI。
//
// 批量（第四步）：**严格串行**。队列里一次只拆一篇，上一篇点了「写入」或「跳过」之后才起下一篇——
// 后一篇拆解时服务端的索引已经有了前一篇刚建的节点，能连上、也能看出重复；并行跑或先全拆再统一审，
// 跨篇重复和漏连会大量出现。拆解失败就停在那一篇等人「重试 / 跳过」，不自动往下烧钱。
// 队列只活在内存里：刷新页面就没了，写进去的文件不受影响。
//
// 两个只在导入卡上有的动作都交给服务端改方案再翻译：
// · 「改用清单里的 id」：认领幽灵要连带改所有指向它的关系和正文链接，前端不自己拼；
// · 「这条待审直接写」：把那条边的置信度抬到 1。
import { computed, onMounted, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import { fetchImportSource, fetchImportSources, postImport, postImportPropose } from '../api.js'

const props = defineProps({
  fields: { type: Array, default: () => [] },       // 已有领域名，给下拉
  project: { type: String, default: '' },           // 当前项目 id；空串 = 全局
  projectName: { type: String, default: '' },
  projectField: { type: String, default: '' },
  revision: { type: Number, default: 0 },           // index revision，写入时做乐观锁
  busy: { type: Boolean, default: false },          // 上层正在落位 / 刷新
})
const emit = defineEmits(['applied', 'close', 'goto', 'error', 'copy'])

const TABS = [{ id: 'paste', label: '粘贴' }, { id: 'vault', label: '仓库里的文件' }, { id: 'file', label: '本地文件' }]
const tab = ref('paste')
const text = ref('')
const source = ref('')
const field = ref(props.projectField || props.fields[0] || '')
const folder = ref('')
const sources = ref([])
const picked = ref('')          // 仓库文件路径
const loadingSources = ref(false)
const working = ref(false)      // 正在拆解 / 重算 / 写入
const error = ref('')
const proposal = ref(null)      // ImportProposal
const renames = ref({})         // {模型给的 id: 清单 id}
const promote = ref(new Set())  // 要直接写的待审边 key
const openBodies = ref(new Set())
const stale = ref(false)        // 改过了、diff 还是旧的
const blocked = ref(null)       // 写入审核挡下来的那份结论（AuditReport）

// 批量队列：{ key, name, kind: 'vault' | 'file' | 'paste', path?, text?, status, error? }
// status: waiting → proposing → review → done | skipped | failed
const queue = ref([])
const checked = ref(new Set())  // 仓库文件多选
const current = computed(() => queue.value.find((q) => q.status === 'review' || q.status === 'proposing') || null)
const waiting = computed(() => queue.value.filter((q) => q.status === 'waiting'))
const queueDone = computed(() => queue.value.filter((q) => q.status === 'done').length)
const STATUS_LABEL = { waiting: '等着', proposing: '拆解中', review: '审核中', done: '已写入', skipped: '跳过', failed: '失败' }
let seq = 0

watch(() => props.projectField, (v) => { if (v && !field.value) field.value = v })

const plan = computed(() => proposal.value?.plan || null)
const preview = computed(() => proposal.value?.preview || null)
const nodes = computed(() => plan.value?.nodes || [])
const enrich = computed(() => plan.value?.enrich || plan.value?.merge_into || [])
const shownId = (id) => renames.value[id] || id
const isClaimed = (id) => (proposal.value?.claims || []).some((c) => c.node_id === id)
    || Object.values(renames.value).includes(id) || Object.keys(renames.value).includes(id)
const nearMissOf = (id) => (proposal.value?.near_misses || []).find((m) => m.node_id === id && !renames.value[id])
const edgeKey = (e) => `${e.source}->${e.target}#${e.relation}`
const edgeCount = (n) => (n.relations || []).length
const canRun = computed(() => !working.value && !props.busy)

onMounted(loadSources)

async function loadSources() {
  loadingSources.value = true
  try { sources.value = (await fetchImportSources()).files } catch (err) { error.value = msgOf(err) } finally { loadingSources.value = false }
}

async function pickSource(path) {
  picked.value = path
  try {
    const res = await fetchImportSource(path)
    text.value = res.text
    if (!source.value) source.value = path.split('/').pop().replace(/\.(md|markdown|txt)$/i, '')
  } catch (err) { error.value = msgOf(err) }
}

const stripExt = (name) => name.replace(/\.(md|markdown|txt)$/i, '')
const readText = (f) => new Promise((ok, no) => {
  const r = new FileReader(); r.onload = () => ok(String(r.result || '')); r.onerror = no; r.readAsText(f)
})

/** 本地文件：选一个就进编辑框；一次选了好几个就全部进队列。 */
async function onFile(ev) {
  const files = [...(ev.target.files || [])]
  ev.target.value = ''
  if (!files.length) return
  if (files.length === 1) {
    text.value = await readText(files[0])
    if (!source.value) source.value = stripExt(files[0].name)
    return
  }
  for (const f of files) enqueue({ name: stripExt(f.name), kind: 'file', text: await readText(f) })
}

function toggleCheck(path) {
  const next = new Set(checked.value); next.has(path) ? next.delete(path) : next.add(path); checked.value = next
}
function enqueueChecked() {
  for (const path of checked.value) enqueue({ name: stripExt(path.split('/').pop()), kind: 'vault', path })
  checked.value = new Set()
}
function enqueuePaste() {
  if (!text.value.trim()) return
  const name = source.value.trim() || text.value.trim().split('\n')[0].replace(/^#+\s*/, '').slice(0, 40)
  enqueue({ name, kind: 'paste', text: text.value })
  text.value = ''; source.value = ''
}
function enqueue(item) {
  const dup = queue.value.some((q) => q.kind === item.kind && q.status !== 'done' && q.status !== 'skipped'
    && (item.path ? q.path === item.path : q.name === item.name))
  if (dup) return
  queue.value = [...queue.value, { key: `q${++seq}`, status: 'waiting', ...item }]
}
function dequeue(key) { queue.value = queue.value.filter((q) => q.key !== key) }
function setStatus(key, patch) { queue.value = queue.value.map((q) => (q.key === key ? { ...q, ...patch } : q)) }

/** 起下一篇：一次只拆一篇。当前还有在拆 / 在审的就不动。 */
async function runNext() {
  if (current.value || working.value) return
  const item = waiting.value[0]
  if (!item) return
  if (!field.value.trim()) { error.value = '先选一个领域，队列里每篇都归它'; return }
  setStatus(item.key, { status: 'proposing', error: '' })
  let body = item.text || ''
  try {
    if (item.kind === 'vault' && !body) body = (await fetchImportSource(item.path)).text
    await proposeText(body, item.name)
    setStatus(item.key, { status: 'review' })
  } catch (err) {
    setStatus(item.key, { status: 'failed', error: msgOf(err) })
    error.value = `「${item.name}」拆解失败：${msgOf(err)}——修好后点「重试」，或「跳过」它继续`
  }
}
function retry(key) { setStatus(key, { status: 'waiting', error: '' }); error.value = ''; runNext() }
function skip(key) {
  const wasCurrent = current.value?.key === key
  setStatus(key, { status: 'skipped' })
  if (wasCurrent) { proposal.value = null; runNext() }
}

function msgOf(err) { return err?.body?.detail?.message || err?.body?.detail || err?.message || '未知错误' }

function reqBody(dryRun, force = false) {
  return { plan: plan.value, field: field.value.trim(), source: source.value.trim(), folder: folder.value.trim() || null,
           base_revision: props.revision, dry_run: dryRun, renames: renames.value, promote: [...promote.value],
           ...(force ? { force: true } : {}) }
}

/** 单篇：编辑框里的这一篇直接拆。队列里有东西时也能用，但拆完得先处理完它才会起队列。 */
async function propose() {
  error.value = ''
  if (!text.value.trim()) { error.value = '先把文章贴进来，或选一个文件'; return }
  if (!source.value.trim()) source.value = text.value.trim().split('\n')[0].replace(/^#+\s*/, '').slice(0, 40)
  if (!field.value.trim()) { error.value = '选一个领域（新节点统一归它）'; return }
  try { await proposeText(text.value, source.value.trim()) } catch (err) { error.value = `拆解失败：${msgOf(err)}` }
}

/** 真正的一次拆解：清掉上一张卡的状态，调 propose，把方案摆成卡。失败往上抛，由调用方决定怎么提示。 */
async function proposeText(body, name) {
  working.value = true
  source.value = name
  proposal.value = null; renames.value = {}; promote.value = new Set(); openBodies.value = new Set(); stale.value = false
  try {
    proposal.value = await postImportPropose({ text: body, source: name, field: field.value.trim(),
                                               folder: folder.value.trim() || null, project: props.project || null })
  } finally { working.value = false }
}

/** 改过摘要 / 正文 / 认领 / 提升之后重算 diff：服务端改方案再翻译，卡上看到的就是会落盘的。 */
async function recompute() {
  if (!plan.value) return
  working.value = true; error.value = ''
  try {
    proposal.value = { ...proposal.value, preview: await postImport(reqBody(true)) }
    stale.value = false
  } catch (err) { error.value = `重算失败：${msgOf(err)}` } finally { working.value = false }
}

function adopt(miss) {
  renames.value = { ...renames.value, [miss.node_id]: miss.point_id }
  stale.value = true
  recompute()
}
function undoAdopt(id) {
  const next = { ...renames.value }; delete next[id]; renames.value = next
  stale.value = true
  recompute()
}
function togglePromote(e) {
  const next = new Set(promote.value); const k = edgeKey(e)
  next.has(k) ? next.delete(k) : next.add(k)
  promote.value = next; stale.value = true
}
function toggleBody(id) {
  const next = new Set(openBodies.value); next.has(id) ? next.delete(id) : next.add(id); openBodies.value = next
}
function touch() { stale.value = true; blocked.value = null }

async function apply(force = false) {
  if (!plan.value) return
  working.value = true; error.value = ''
  blocked.value = null
  try {
    const res = await postImport(reqBody(false, force))
    // 审核挡下：**200 + applied:false**，不是错误码。卡上把结论摆出来，人改完再写，
    // 或者点「仍然写入」——一篇长文重拆一次要好几秒，不该逼人从头来过。
    if (res.applied === false && res.audit?.verdict === 'block') {
      blocked.value = res.audit
      proposal.value = { ...proposal.value, preview: res }
      return
    }
    const stubs = new Set((plan.value.stubs || []).map((s) => s.id))
    const born = nodes.value.map((n) => shownId(n.id)).filter((id) => !stubs.has(id))
    const claimed = born.filter((id) => (proposal.value.claims || []).some((c) => c.node_id === id)
        || Object.values(renames.value).includes(id))
    emit('applied', { result: res, born, claimed, enriched: enrich.value.map((e) => e.existing) })
    proposal.value = { ...proposal.value, preview: res, applied: true }
  } catch (err) { error.value = `写入失败：${msgOf(err)}`; return } finally { working.value = false }
  if (blocked.value) return         // 挡下了：停在这一篇，别推进队列
  if (current.value) {
    // 队列里的一篇写完了：记成 done，**这时**才起下一篇（严格串行）。
    // 放在 finally 之后：runNext 的守卫看 working，写入那一下还没收尾就起下一篇会被挡回去
    setStatus(current.value.key, { status: 'done' })
    proposal.value = null
    await runNext()
  }
}

function reset() {
  if (current.value && !proposal.value?.applied) { skip(current.value.key); return }
  proposal.value = null; text.value = ''; source.value = ''; picked.value = ''; error.value = ''
}
</script>

<template>
  <Drawer side="left" title="导入" icon="file" storage-key="import" :default-width="420" :max="720" expandable
          @close="emit('close')">
    <template #head-actions>
      <!-- 抄别人库里现成的点，和"把文章拆成点"是同一件事的两个来源，所以入口摆在一起 -->
      <button class="btn tiny" type="button" title="从别的知识库抄现成的知识点（带正文和关系）"
              @click="emit('copy')"><Icon name="copy" :size="13" />从别的库抄…</button>
      <span v-if="project" class="chip accent" :title="'在项目下导入：清单里没建的点会作为待认领给模型看'">{{ projectName || project }}</span>
      <span v-else class="chip">全局</span>
    </template>

    <template #default>
      <!-- 第一段：文章从哪来 -->
      <section v-if="!proposal || proposal.applied" class="imp-src">
        <div class="seg small">
          <button v-for="t in TABS" :key="t.id" :class="{ on: tab === t.id }" @click="tab = t.id">{{ t.label }}</button>
        </div>

        <div v-if="tab === 'vault'" class="imp-files scroll-thin">
          <div v-if="loadingSources" class="dim">读取中…</div>
          <div v-else-if="!sources.length" class="dim">仓库里没有可当素材的 md / txt（节点目录之外）。</div>
          <div v-for="f in sources" :key="f.path" class="imp-file" :class="{ on: picked === f.path }" :title="f.path">
            <input type="checkbox" :checked="checked.has(f.path)" title="勾上，一起加入队列" @click.stop="toggleCheck(f.path)">
            <button class="imp-file-pick" @click="pickSource(f.path)">
              <Icon name="file" :size="12" /><span class="nm">{{ f.name }}</span>
              <span class="dim">{{ f.path.split('/').slice(0, -1).join('/') }}</span>
            </button>
          </div>
        </div>
        <button v-if="tab === 'vault' && checked.size" class="btn tiny" data-act="enqueue" @click="enqueueChecked">
          <Icon name="plus" :size="12" />把勾上的 {{ checked.size }} 篇加入队列
        </button>
        <label v-else-if="tab === 'file'" class="imp-drop">
          <input type="file" accept=".md,.markdown,.txt" multiple @change="onFile">
          <Icon name="plus" :size="14" />选 .md / .txt（只在浏览器里读，不上传）。一次选好几个就进队列
        </label>

        <textarea v-model="text" class="imp-text scroll-thin" rows="10"
                  :placeholder="tab === 'paste' ? '把文章 / 笔记贴在这里' : '选中的文件内容会出现在这里，可以再改'"
                  @input="picked = ''"></textarea>

        <div class="imp-form">
          <label>来源<input v-model="source" type="text" placeholder="文章名（写进 frontmatter source）"></label>
          <label>领域
            <input v-model="field" type="text" list="imp-fields" placeholder="新节点统一归哪个领域">
            <datalist id="imp-fields"><option v-for="f in fields" :key="f" :value="f" /></datalist>
          </label>
          <label>目录<input v-model="folder" type="text" :placeholder="`nodes/ 下的子目录，默认同领域名`"></label>
        </div>

        <div class="imp-run">
          <button class="btn primary" style="flex: 1; justify-content: center" :disabled="!canRun || !!current" @click="propose">
            <Icon name="search" :size="14" />{{ working ? '拆解中…（一次模型调用）' : '拆成知识点' }}
          </button>
          <button v-if="text.trim()" class="btn tiny" title="先不拆，排进队列，和别的一起串行导" @click="enqueuePaste">
            <Icon name="plus" :size="12" />加入队列
          </button>
        </div>

      </section>

      <!-- 队列：严格串行，一篇写入 / 跳过之后才起下一篇 -->
      <div v-if="queue.length" class="imp-queue">
        <div class="imp-block-head">
          队列 {{ queueDone }}/{{ queue.length }}
          <button v-if="waiting.length && !current" class="btn primary tiny" style="margin-left: auto" data-act="run"
                  :disabled="!canRun" @click="runNext">
            <Icon name="play" :size="12" />{{ queueDone ? '继续' : '开始' }}（一篇一篇来）
          </button>
          <span v-else-if="current" class="dim" style="margin-left: auto; font-size: 11px">正在第 {{ queue.indexOf(current) + 1 }} 篇</span>
        </div>
        <ul class="imp-queue-list">
          <li v-for="q in queue" :key="q.key" class="imp-queue-item" :class="q.status">
            <span class="chip" :class="{ accent: q.status === 'review' || q.status === 'proposing', 'm-mastered': q.status === 'done' }">
              {{ STATUS_LABEL[q.status] }}
            </span>
            <span class="nm" :title="q.path || q.name">{{ q.name }}</span>
            <span v-if="q.error" class="dim imp-queue-err" :title="q.error">{{ q.error }}</span>
            <button v-if="q.status === 'failed'" class="btn tiny" @click="retry(q.key)">重试</button>
            <button v-if="q.status === 'failed' || q.status === 'waiting'" class="icon-btn" title="跳过 / 移出队列"
                    @click="q.status === 'failed' ? skip(q.key) : dequeue(q.key)"><Icon name="x" :size="12" /></button>
          </li>
        </ul>
      </div>
      <p class="dim" style="font-size: 11.5px; line-height: 1.6; margin: 8px 0 0">
        {{ project ? `在「${projectName || project}」下导入：清单里还没建的点会先给模型认领，新点落项目画布。` : '全局导入：新点按邻居投票放上全局图，判不出的进 Inbox。' }}
        {{ text.length ? `${text.length} 字。` : '' }}
      </p>

      <p v-if="error" class="imp-error">{{ error }}</p>

      <!-- 第二段：审核卡 -->
      <section v-if="proposal" class="change-card imp-card">
        <div class="cc-head">
          <Icon name="file" :size="13" />
          <span v-if="current" class="chip accent">第 {{ queue.indexOf(current) + 1 }}/{{ queue.length }} 篇 · {{ current.name }}</span>
          {{ proposal.applied ? '已写入' : '提议写入' }} {{ preview.files.length }} 个文件
          <span v-if="proposal.applied" class="chip m-mastered">已写入</span>
          <span v-else-if="stale" class="dim" style="font-size: 11px">改过了，diff 是旧的</span>
          <button class="btn subtle tiny" style="margin-left: auto" @click="reset">
            <Icon name="x" :size="12" />{{ proposal.applied ? '再导一篇' : current ? '跳过这篇' : '放弃' }}
          </button>
        </div>
        <p v-if="plan.summary" class="imp-summary">{{ plan.summary }}</p>
        <div class="imp-counts dim">
          新建 {{ preview.counts.nodes }} · stub {{ preview.counts.stubs }} · 补充老节点 {{ preview.counts.enrich }}
          · 直接写入 {{ preview.counts.edges }} 条边 · 待审 {{ preview.counts.pending }} 条
          <span v-if="proposal.project_points">· 给模型看了 {{ proposal.project_points }} 个待认领的点</span>
        </div>

        <!-- 孤立提醒：整块和体系断开的，只会掉进 Inbox -->
        <div v-if="proposal.isolated.length" class="imp-note warn">
          <Icon name="warn" :size="13" />
          <div>
            <b>{{ proposal.isolated.join('、') }}</b> 一条边都没连到已有节点{{ project ? '、也没认领清单里的点' : '' }}，写入后只会进 Inbox。
            <div v-if="proposal.suggest_home" class="dim">
              模型建议：{{ proposal.suggest_home.kind === 'project' ? '归到项目' : proposal.suggest_home.kind === 'field' ? '归到领域' : '' }}
              「{{ proposal.suggest_home.name }}」{{ proposal.suggest_home.why ? `——${proposal.suggest_home.why}` : '' }}（只是建议，不会自动建）
            </div>
          </div>
        </div>

        <!-- 节点行 -->
        <ul class="imp-nodes">
          <li v-for="n in nodes" :key="n.id" class="imp-node">
            <div class="imp-node-head">
              <b :title="n.id !== shownId(n.id) ? `模型给的 id：${n.id}` : ''">{{ shownId(n.id) }}</b>
              <span v-if="isClaimed(n.id)" class="chip accent" title="认领了清单里的这个点：写入后落在幽灵原位">认领</span>
              <span class="dim">{{ edgeCount(n) }} 条关系</span>
              <button v-if="!proposal.applied" class="icon-btn" :title="openBodies.has(n.id) ? '收起正文' : '看 / 改正文'"
                      @click="toggleBody(n.id)"><Icon :name="openBodies.has(n.id) ? 'fold' : 'pencil'" :size="12" /></button>
              <button v-if="renames[n.id] && !proposal.applied" class="btn subtle tiny" @click="undoAdopt(n.id)">还用模型的 id</button>
            </div>
            <div v-if="nearMissOf(n.id) && !proposal.applied" class="imp-note">
              <Icon name="target" :size="12" />
              和清单里的「<b>{{ nearMissOf(n.id).point_name }}</b>」很像（{{ Math.round(nearMissOf(n.id).ratio * 100) }}%）
              <button class="btn tiny" :disabled="!canRun" @click="adopt(nearMissOf(n.id))">改用清单里的 id</button>
            </div>
            <input v-if="!proposal.applied" v-model="n.desc" type="text" class="imp-desc" placeholder="一句话摘要（画布上就这一句）" @input="touch">
            <div v-else class="dim" style="font-size: 12px">{{ n.desc }}</div>
            <textarea v-if="openBodies.has(n.id) && !proposal.applied" v-model="n.body" class="scroll-thin" rows="10"
                      placeholder="正文（整篇落盘；## 关系 由系统管，别写）" @input="touch"></textarea>
          </li>
        </ul>

        <div v-if="enrich.length" class="imp-block">
          <div class="imp-block-head">补充老节点（只追加，带来源引言）</div>
          <div v-for="e in enrich" :key="e.existing" class="edge-row">
            <button class="link" @click="emit('goto', e.existing)">{{ e.existing }}</button>
            <span class="to dim">{{ e.why || '' }}</span>
          </div>
        </div>

        <div v-if="preview.pending.length" class="imp-block">
          <div class="imp-block-head">待审边（模型没把握，勾上就直接写进 md）</div>
          <label v-for="e in preview.pending" :key="edgeKey(e)" class="edge-row imp-pending">
            <input type="checkbox" :checked="promote.has(edgeKey(e))" :disabled="proposal.applied" @change="togglePromote(e)">
            <span class="to">{{ e.source }} <i>{{ e.relation }}</i> → {{ e.target }}</span>
            <span class="yr">{{ Math.round(e.confidence * 100) }}%</span>
          </label>
        </div>

        <!-- 写入审核挡下：这里不是错误，是"先改再写"。每条都带依据和改法 -->
        <div v-if="blocked" class="imp-block imp-audit">
          <div class="imp-block-head"><Icon name="checklist" :size="13" />审核没通过{{ blocked.summary ? `：${blocked.summary}` : '' }}</div>
          <div v-for="(it, i) in blocked.issues.filter((x) => x.level === 'block')" :key="i"
               class="dim" style="font-size: 12px; line-height: 1.7">
            · <b>{{ it.message }}</b>
            <span v-if="it.why">（依据：{{ it.why }}）</span>
            <span v-if="it.fix" style="color: var(--ok, #2f7d52)">→ {{ it.fix }}</span>
          </div>
        </div>

        <details v-if="preview.warnings.length" class="imp-block">
          <summary class="imp-block-head">{{ preview.warnings.length }} 条提醒</summary>
          <div v-for="(w, i) in preview.warnings" :key="i" class="dim" style="font-size: 12px">· {{ w }}</div>
        </details>

        <details v-if="(preview.audit?.issues || []).length && !blocked" class="imp-block">
          <summary class="imp-block-head">{{ preview.audit.issues.length }} 条写入检查</summary>
          <div v-for="(it, i) in preview.audit.issues" :key="i" class="dim" style="font-size: 12px; line-height: 1.7">
            · {{ it.message }}<span v-if="it.fix"> —— {{ it.fix }}</span>
          </div>
        </details>

        <pre v-for="f in preview.files" :key="f.path" class="cc-diff"><b>{{ f.path }}</b>
{{ f.diff || '（新文件）' }}</pre>

        <div v-if="!proposal.applied" class="cc-acts">
          <button class="btn subtle tiny" :disabled="!canRun" @click="recompute"><Icon name="refresh" :size="12" />重算 diff</button>
          <button class="btn primary tiny" :disabled="!canRun" @click="apply()"><Icon name="check" :size="13" />写入</button>
          <button v-if="blocked" class="btn subtle tiny" :disabled="!canRun" @click="apply(true)"
                  title="审核看走眼了：跳过模型那一段直接写（会记进 issues.jsonl）">仍然写入</button>
          <span class="dim" style="font-size: 11px">写前自动备份；待审边记进 pending.json</span>
        </div>
        <p v-else class="dim" style="font-size: 11.5px">
          已写入 {{ preview.files.length }} 个文件{{ preview.backup ? `，备份 ${preview.backup}` : '' }}{{ preview.log ? `，方案存档 ${preview.log}` : '' }}
        </p>
      </section>
    </template>
  </Drawer>
</template>
