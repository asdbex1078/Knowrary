<script setup>
/**
 * 碎片箱（2026-10-23）：接不同的活时学到的零散点子——先记下来，攒多了归拢成体系。
 *
 * 碎片不是节点（太碎进图就是毛线球），住在 vault 的 fragments/ 里按天一个 md，索引不扫。
 * 这个面板管三件事：随手记一条、看积压（几条没归拢、最老躺几天，到量会催）、
 * 一键归拢——一次 learn 调用分簇，每簇三选一：并进已有节点 / 长成新节点 / 继续躺。
 *
 * 落地不在这儿：并进走 App 的 writeChanges（append_body，写入审核照常把门），
 * 长成新节点把整理好的内容丢给导入面板；落完 App 调 /api/fragments/mark 标去向
 * （行尾 `→ 目标（日期）`，留档不删），再把 refresh 加一让这里重拉。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import { consolidateFragments, fetchFragments, postFragment } from '../api.js'

const props = defineProps({
  busy: { type: Boolean, default: false },         // App 正在落一簇（写 md / 跳导入）
  refresh: { type: Number, default: 0 },           // App 落完一簇 +1，这里重拉
  names: { type: Function, default: (x) => x },    // 节点 id → 显示名
})
const emit = defineEmits(['close', 'goto', 'enrich', 'import-cluster'])

const doc = ref(null)             // FragmentsRead
const draft = ref('')
const saving = ref(false)
const error = ref('')
const merging = ref(false)        // 正在等模型分簇
const elapsed = ref(0)            // 模型调用必须看得见：等了几秒
const result = ref(null)          // FragmentsConsolidated
const dismissed = ref(new Set())  // 这一轮里点了「放着」的簇（只记这次，不落盘）
let timer = null

const msgOf = (err) => err?.body?.detail?.message || err?.body?.detail || err?.message || '出错了'

async function load() {
  try { doc.value = await fetchFragments() } catch (err) { error.value = msgOf(err) }
}
load()
watch(() => props.refresh, load)
onBeforeUnmount(() => clearInterval(timer))

async function addOne() {
  const text = draft.value.trim()
  if (!text || saving.value) return
  saving.value = true
  error.value = ''
  try {
    await postFragment(text)
    draft.value = ''
    await load()
  } catch (err) {
    error.value = msgOf(err)
  } finally {
    saving.value = false
  }
}

async function consolidate() {
  if (merging.value) return
  merging.value = true
  error.value = ''
  elapsed.value = 0
  timer = setInterval(() => { elapsed.value += 1 }, 1000)
  try {
    result.value = await consolidateFragments()
    dismissed.value = new Set()
  } catch (err) {
    error.value = msgOf(err)
  } finally {
    clearInterval(timer)
    merging.value = false
  }
}

const byId = computed(() => new Map((doc.value?.entries || []).map((e) => [e.id, e])))
const openIds = computed(() => new Set((doc.value?.entries || []).filter((e) => !e.target).map((e) => e.id)))

/** 还摆得出来的簇：没点「放着」、且至少还剩一条没归拢的碎片（落完地自动消掉）。 */
const clusters = computed(() => (result.value?.clusters || [])
  .map((c, i) => ({ ...c, key: i }))
  .filter((c) => !dismissed.value.has(c.key) && c.ids.some((id) => openIds.value.has(id))))

/** 列表按天分组（entries 本来就新的在前）。 */
const days = computed(() => {
  const out = []
  for (const e of doc.value?.entries || []) {
    if (!out.length || out[out.length - 1].date !== e.date) out.push({ date: e.date, items: [] })
    out[out.length - 1].items.push(e)
  }
  return out
})

const ACTION_SAY = {
  enrich: (c) => `并进「${props.names(c.node)}」`,
  new: () => '长成新节点（去导入面板拆）',
  keep: () => '继续躺着',
}

/** 归拢去向里的 [[节点]]：点一下在图上定位。 */
const targetNode = (t) => (/^\[\[(.+)\]\]$/.exec(t || '') || [])[1] || ''
</script>

<template>
  <Drawer side="left" title="碎片" icon="note" storage-key="fragments" :default-width="440" :max="880" expandable
          @close="emit('close')">
    <div class="frag scroll-thin">
      <!-- 随手记：回车就进 fragments/今天.md，顺手劲不被打断 -->
      <div class="frag-add">
        <input v-model="draft" type="text" spellcheck="false" :disabled="saving"
               placeholder="随手记一条（回车保存）：今天踩的坑、冒出来的点子……"
               @keydown.enter.prevent="addOne" />
        <button class="btn tiny primary" :disabled="saving || !draft.trim()" @click="addOne">记下</button>
      </div>

      <!-- 积压常显 + 到量催归：碎片系统的死法从来都是只进不出 -->
      <div v-if="doc" class="frag-stats" :class="{ nag: doc.nag }">
        <span v-if="doc.open">
          <b>{{ doc.open }}</b> 条没归拢<template v-if="doc.oldest_days">，最老的躺了 <b>{{ doc.oldest_days }}</b> 天</template>
          <template v-if="doc.nag">——该归拢了</template>
        </span>
        <span v-else class="dim">没有积压：碎片都归拢过了</span>
        <button class="btn tiny" :class="{ primary: doc.nag }" :disabled="merging || !doc.open"
                data-act="consolidate" :title="`让模型（learn 角色）把 ${doc.open} 条碎片分簇，每簇给处置建议；只出建议，写不写由你`"
                @click="consolidate">
          <Icon name="layers" :size="12" />{{ merging ? `归拢中… ${elapsed}s` : '归拢' }}</button>
      </div>
      <p v-if="error" class="imp-error">{{ error }}</p>

      <!-- 归拢建议：每簇一张卡。落完地（碎片全标了去向）这张卡自己消失 -->
      <template v-if="clusters.length">
        <p class="dim frag-meta">{{ result.model }} · {{ (result.ms / 1000).toFixed(1) }}s
          <template v-if="result.issues.length">·
            <span :title="result.issues.join('\n')">{{ result.issues.length }} 条建议被降级 / 丢掉</span></template></p>
        <div v-for="c in clusters" :key="c.key" class="frag-cluster" :data-cluster="c.title">
          <div class="frag-cluster-head">
            <b>{{ c.title }}</b><span class="chip" :class="c.action === 'keep' ? 'm-unbuilt' : 'm-mastered'">
              {{ ACTION_SAY[c.action](c) }}</span>
          </div>
          <p v-if="c.summary" class="dim">{{ c.summary }}</p>
          <ul class="frag-cluster-items">
            <li v-for="id in c.ids" :key="id" :class="{ done: !openIds.has(id) }">{{ byId.get(id)?.text || id }}</li>
          </ul>
          <pre v-if="c.text" class="frag-cluster-text">{{ c.text }}</pre>
          <div class="frag-cluster-ops">
            <button v-if="c.action === 'enrich'" class="btn tiny primary" :disabled="busy" data-act="enrich"
                    :title="`追加进「${names(c.node)}」的正文末尾（带「补充自碎片」引言，写入审核照常把门）`"
                    @click="emit('enrich', c)">并进 {{ names(c.node) }}</button>
            <button v-if="c.action === 'new'" class="btn tiny primary" :disabled="busy" data-act="to-import"
                    title="把整理好的内容带到导入面板，照常拆成知识点（碎片随即标为已归拢）"
                    @click="emit('import-cluster', c)">去导入面板拆</button>
            <button v-if="c.action === 'enrich' && c.node" class="btn subtle tiny" @click="emit('goto', c.node)">在图上看它</button>
            <button class="btn subtle tiny" data-act="dismiss" title="这轮先不动它（碎片照旧躺着，下次归拢还会出现）"
                    @click="dismissed = new Set([...dismissed, c.key])">放着</button>
          </div>
        </div>
      </template>

      <!-- 全部碎片：按天分组，归拢过的变暗、标去向 -->
      <p v-if="doc && !doc.entries.length" class="dim">
        还没有碎片。学到零散的一句话就记在这儿——攒多了点「归拢」，它们就长进体系里。
        文件在 vault 的 <code>{{ doc.dir }}/</code>，按天一个 md，Obsidian 里也能直接写。</p>
      <section v-for="d in days" :key="d.date" class="frag-day">
        <div class="frag-day-head">{{ d.date }}</div>
        <div v-for="e in d.items" :key="e.id" class="frag-row" :class="{ done: !!e.target }">
          <span class="frag-time mono">{{ e.time }}</span>
          <span class="frag-text">{{ e.text }}</span>
          <button v-if="targetNode(e.target)" class="art-chip" :title="`已并入，点一下在图上定位`"
                  @click="emit('goto', targetNode(e.target))">→ {{ names(targetNode(e.target)) }}</button>
          <span v-else-if="e.target" class="dim frag-target">→ {{ e.target }}</span>
        </div>
      </section>
    </div>
  </Drawer>
</template>
