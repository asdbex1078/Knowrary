<script setup>
/**
 * 右侧检查器：选中节点的一切都在这里，按「详情 / 关系 / 变更」分页。
 *
 * 原来是并排两块 aside（节点详情 + 待写回变更），一起弹出来时画布只剩一半；
 * 而且详情里一行六个按钮不分主次。现在合成一个抽屉、按任务分页。
 */
import { computed, reactive, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import { parseDiff } from '../ui/diff.js'
import PendingEdgeRow from './PendingEdgeRow.vue'
import EdgeCheckRow from './EdgeCheckRow.vue'
import EdgeRunBar from './EdgeRunBar.vue'

const props = defineProps({
  selected: { type: Object, default: null },
  detail: { type: Object, default: null },
  relationTypes: { type: Array, default: () => [] },
  allNodeIds: { type: Array, default: () => [] },
  pending: { type: Array, default: () => [] },
  changePreview: { type: Object, default: null },
  isDue: { type: Boolean, default: false },
  writeNonce: { type: Number, default: 0 },   // 外面 ++ 一次就把正文编辑框打开
  suggestions: { type: Object, default: null },   // SuggestResult from /api/suggest
  suggesting: { type: Boolean, default: false },   // LLM 正在生成建议
  pendingEdges: { type: Array, default: () => [] },   // 和这个点有关的待审边（导入时模型没把握的）
  edgeChecks: { type: Array, default: () => [] },     // 和这个点有关的已有边的校验结论（review 角色审的）
  edgeRun: { type: Object, default: null },           // 正在跑的那一轮校验（任何范围的）
  models: { type: Array, default: () => [] },         // 技术点链着这个点的型号（模型目录反查）
  lineOptions: { type: Object, default: () => ({ 流派: [], 领域线: [] }) },   // 库里有哪些流派 / 领域线
})
/** 这个点的几层：第一个是主层。索引里 `layer` 永远是主层、`layers` 是几层时的全量。 */
const myLayers = computed(() => props.selected?.layers || (props.selected?.layer ? [props.selected.layer] : []))
function setPrimaryLayer(value) {
  // 换主层时原来的其它几层留着；选「未分层」就整个清空（没有主层，副层也没地方挂）
  const rest = myLayers.value.filter((x) => x !== value && x !== myLayers.value[0])
  emit('set-layer', { id: props.selected.id, layer: value ? [value, ...rest] : [] })
}
function toggleAlsoLayer(l) {
  const have = myLayers.value
  emit('set-layer', { id: props.selected.id, layer: have.includes(l) ? have.filter((x) => x !== l) : [...have, l] })
}

/** 加 / 去掉一条线：已有的顺序不动（第一个是主道），新加的接在最后。 */
function toggleLine(key, field, line) {
  const have = props.selected?.[field] || []
  const on = have.includes(line.name) || have.includes(line.id)
  const names = on ? have.filter((x) => x !== line.name && x !== line.id) : [...have, line.id]
  emit('set-lines', { id: props.selected.id, key, names })
}
// 抽象层的七档，和 core.parser.LAYERS 同一张表
const LAYERS = ['理论', '硬件', '体系结构', '汇编接口', '系统软件', '高级语言', 'AI应用']
const emit = defineEmits([
  'close', 'goto', 'open-article', 'decide-pending', 'check-edges', 'stop-edge-check', 'fix-edge', 'edit-desc', 'set-layer', 'set-lines', 'set-year', 'add-ref', 'review', 'quiz', 'rename', 'finalize',
  'retype-edge', 'remove-edge', 'add-edge', 'drop-change',
  'preview-changes', 'apply-changes', 'clear-changes', 'save-body',
  'suggest', 'dismiss-suggestion', 'show-model', 'to-model',
])

const tab = ref('detail')
const showRaw = ref(false)
const writing = ref(false)
const bodyText = ref('')
const draft = reactive({ relation: '', target: '', year: '', note: '' })

/**
 * md 原文里"正文"那一段：去掉 frontmatter，切到 `## 关系` 为止。
 * 服务端写回时也是按这两条界线切的，所以这里取出来的就是它认的那一段。
 */
function bodyOf(raw) {
  let rest = String(raw || '')
  if (rest.startsWith('---\n')) {
    const end = rest.indexOf('\n---\n', 3)
    if (end !== -1) rest = rest.slice(end + 5)
  }
  const m = rest.match(/^## 关系\s*$/m)
  return (m ? rest.slice(0, m.index) : rest).replace(/\n+$/, '')
}

function startWriting() {
  bodyText.value = bodyOf(props.detail?.raw)
  writing.value = true
}

// 域工具条上点「写内容」时，节点详情往往还在路上；等 detail 到了再展开编辑框
const wantWrite = ref(false)
watch(() => props.writeNonce, () => { wantWrite.value = true; tab.value = 'detail' })
watch([() => props.detail, wantWrite], () => {
  if (wantWrite.value && props.detail) {
    startWriting()
    wantWrite.value = false
  }
})

// 选中新节点就回到详情页；没有选中但有待写回变更时，直接停在变更页
watch(() => props.selected?.id, (id) => {
  if (id) { tab.value = 'detail'; showRaw.value = false; writing.value = false }
  else if (props.pending.length) tab.value = 'changes'
})

const outCount = computed(() => props.detail?.out.length || 0)
const inCount = computed(() => props.detail?.in_edges.length || 0)
const isDraft = computed(() => props.selected?.placed?.state === 'draft')

function submitEdge() {
  if (!draft.relation || !draft.target) return
  emit('add-edge', { ...draft })
  draft.target = ''
  draft.year = ''
  draft.note = ''
}

function describeChange(c) {
  if (c.type === 'add_edge') return `新增 ${c.relation} → ${c.target}`
  if (c.type === 'remove_edge') return `删除 ${c.relation} → ${c.target}`
  if (c.type === 'update_edge') return `改类型 ${c.from_relation} → ${c.relation}（${c.target}）`
  return `改 frontmatter ${Object.entries(c.fields).map(([k, v]) => `${k} = ${v}`).join('、')}`
}

const CHANGE_ICON = { add_edge: 'plus', remove_edge: 'trash', update_edge: 'pencil' }

</script>

<template>
  <Drawer side="right" title="检查器" icon="sidebar" storage-key="inspector"
          :default-width="336" :min="288" :max="560" @close="emit('close')">
    <template #default>
      <div class="insp">
        <nav class="tabs">
          <button :class="{ on: tab === 'detail' }" @click="tab = 'detail'">
            <Icon name="file" :size="14" />详情
          </button>
          <button :class="{ on: tab === 'relations' }" :disabled="!detail" @click="tab = 'relations'">
            <Icon name="link" :size="14" />关系
            <span v-if="outCount + inCount" class="badge quiet">{{ outCount + inCount }}</span>
            <span v-if="pendingEdges.length" class="badge" data-pending-count
                  :title="`${pendingEdges.length} 条待审的边：导入时模型没把握，等你一句话`">{{ pendingEdges.length }} 待审</span>
          </button>
          <button :class="{ on: tab === 'changes' }" @click="tab = 'changes'">
            <Icon name="save" :size="14" />变更
            <span v-if="pending.length" class="badge">{{ pending.length }}</span>
          </button>
        </nav>

        <div class="insp-body scroll-thin">
          <!-- ---------- 详情 ---------- -->
          <template v-if="tab === 'detail'">
            <button v-if="selected && pendingEdges.length" class="insp-pend-hint" @click="tab = 'relations'">
              <Icon name="link" :size="12" />这个点有 {{ pendingEdges.length }} 条待审的边，去「关系」里看
            </button>
            <div v-if="!selected" class="empty">
              <Icon name="target" :size="30" :width="1.3" />
              <span class="t">没有选中节点</span>
              <span class="s">在画布上点一个知识点，或用顶栏搜索定位。</span>
            </div>
            <template v-else>
              <h3 class="node-title">{{ selected.name || selected.id }}</h3>
              <p class="node-desc">{{ selected.desc || '（还没写摘要）' }}</p>

              <div class="act-row">
                <a v-if="detail" class="btn primary" :href="detail.obsidian_uri">
                  <Icon name="external" :size="14" />在 Obsidian 打开
                </a>
                <button class="btn" title="就这个知识点出几道题（调 LLM，几秒）"
                        @click="emit('quiz', [selected.id])">
                  <Icon name="play" :size="14" />考一下
                </button>
                <button v-if="detail" class="btn" title="改 id（同时是文件名），引用会一起迁走"
                        @click="emit('rename', selected.id)">
                  <Icon name="pencil" :size="14" />重命名
                </button>
                <button v-if="isDraft" class="btn" title="位置确认下来，不再是草稿（只改 layout）"
                        @click="emit('finalize', selected.id)">
                  <Icon name="check" :size="14" />定稿
                </button>
              </div>

              <div v-if="isDue" class="act-row grade-row">
                <span class="dim" style="font-size: 11.5px">不考的话，直接记一笔：</span>
                <button v-for="g in ['忘了', '模糊', '记得']" :key="g" class="btn subtle tiny"
                        data-act="review" :data-grade="g"
                        :title="`记为「${g}」，只写 review-log.json`"
                        @click="emit('review', { id: selected.id, grade: g })">{{ g }}</button>
              </div>

              <dl class="meta-grid">
                <dt>id</dt><dd>{{ selected.id }}</dd>
                <template v-if="selected.field"><dt>领域</dt><dd>{{ selected.field }}</dd></template>
                <template v-if="selected.type"><dt>类型</dt><dd>{{ selected.type }}</dd></template>
                <template v-if="selected.year"><dt>年份</dt><dd class="tnum">{{ selected.year }}</dd></template>
                <!-- 抽象层：历史视图按它分泳道，新点也按它归位。空着的话这个点只能待在领域大框里 -->
                <dt>抽象层</dt>
                <dd>
                  <select class="layer-pick" :value="myLayers[0] || ''"
                          :title="detail ? '主层：画布上落在哪条道。改它会写回 md（走变更卡）' : '先打开这个节点'"
                          :disabled="!detail"
                          @change="setPrimaryLayer($event.target.value)">
                    <option value="">未分层</option>
                    <option v-for="l in LAYERS" :key="l" :value="l">{{ l }}</option>
                  </select>
                  <!-- 跨层（2026-10-12）：CPU 既是硬件又是体系结构。主层之外再勾的几层，历史「按抽象层」时画影子 -->
                  <div v-if="myLayers[0]" class="line-picks layer-also" data-layer-also>
                    <span class="dim">也在</span>
                    <button v-for="l in LAYERS.filter((x) => x !== myLayers[0])" :key="l" class="chip tiny"
                            :class="{ on: myLayers.includes(l) }" :disabled="!detail" :data-layer="l"
                            @click="toggleAlsoLayer(l)">{{ l }}</button>
                  </div>
                </dd>
                <!-- 归属（2026-10-12）：流派 / 领域线是点自己的字段，点一下加 / 去掉；先勾的是主道（真身画在那条道上） -->
                <template v-for="[key, field] in [['流派', 'schools'], ['领域线', 'domains']]" :key="key">
                  <template v-if="lineOptions[key]?.length">
                    <dt>{{ key }}</dt>
                    <dd class="line-picks" :data-line-key="key">
                      <button v-for="l in lineOptions[key]" :key="l.id" class="chip tiny"
                              :class="{ on: (selected[field] || []).includes(l.name) || (selected[field] || []).includes(l.id) }"
                              :disabled="!detail" :data-line="l.id"
                              :title="detail ? '改它会写回 md（走变更卡）；写在前面的是主道' : '先打开这个节点'"
                              @click="toggleLine(key, field, l)">{{ l.name }}</button>
                    </dd>
                  </template>
                </template>
                <template v-if="selected.weight">
                  <dt>权重</dt><dd class="tnum">{{ (selected.weight * 100).toFixed(0) }}% · pageRank</dd>
                </template>
                <template v-if="detail"><dt>文件</dt><dd>{{ detail.path }}</dd></template>
                <!-- 来源：这个点是从哪篇文章 / 哪张图导进来的（frontmatter `sources`，旧的 `source` 并在里面）。
                     翻两三个节点就能看出「这批是一次倒进来的」，而这件事以前在界面上完全不可见。
                     库里的原文点得开；外部来源（一张图、一篇论文）是灰字；链到的原文不在就标出来。
                     是不是原文要服务端解析过才知道（detail.sources），没打开之前先把原样文字摆着。 -->
                <template v-if="selected.sources?.length">
                  <dt>来源</dt>
                  <dd class="src-list">
                    <template v-if="detail?.sources?.length">
                      <span v-for="s in detail.sources" :key="s.raw" class="src-item">
                        <!-- 点名字在应用里读（跳到那一节）；想改原文时点旁边的小图标去 Obsidian -->
                        <template v-if="s.uri">
                          <a href="#" :title="`在应用里读原文：${s.path}${s.section ? ' §' + s.section : ''}`"
                             @click.prevent="emit('open-article', { path: s.path, section: s.section })">
                            <Icon name="book" :size="11" />{{ s.label }}<template v-if="s.section"> §{{ s.section }}</template></a>
                          <a :href="s.uri" class="src-obsidian" title="在 Obsidian 里打开（要改原文走这里）">
                            <Icon name="external" :size="11" /></a>
                        </template>
                        <span v-else-if="s.kind === 'article'" class="src-dead"
                              :title="`原文 ${s.path} 不在库里：先放进原文目录（设置 → 知识库）`">
                          {{ s.label }}（原文不在）</span>
                        <span v-else class="muted" title="外部来源：一张图、一篇论文、一本书">{{ s.label }}</span>
                      </span>
                    </template>
                    <template v-else>
                      <span v-for="s in selected.sources" :key="s" class="src-item muted"
                            title="先打开这个节点，才知道哪一项是库里的原文">{{ s }}</span>
                    </template>
                  </dd>
                </template>
                <!-- 模型目录里哪些型号的「技术」链着这个点：点一下去模型谱系里看那一个 -->
                <template v-if="models.length">
                  <dt>用到它的型号</dt>
                  <dd class="insp-models" data-models-using>
                    <button v-for="m in models" :key="m.id" class="art-chip" :title="`${m.familyName} · ${m.date || ''}，去模型谱系里看`"
                            data-act="show-model" @click="emit('show-model', m.id)">{{ m.name }}</button>
                  </dd>
                </template>
              </dl>

              <div v-if="detail" class="act-row">
                <button class="btn subtle tiny" @click="writing ? (writing = false) : startWriting()">
                  <Icon name="file" :size="13" />{{ writing ? '收起正文' : '写内容' }}
                </button>
                <button class="btn subtle tiny" data-act="to-model"
                        title="这个点其实是一个具体型号（DeepSeek-V3、GPT-4o 这种）：挪进模型目录，图上的点删掉，正文当笔记留着"
                        @click="emit('to-model')">
                  <Icon name="timeline" :size="13" />这是型号…
                </button>
                <button class="btn subtle tiny" @click="emit('edit-desc')">
                  <Icon name="pencil" :size="13" />改摘要
                </button>
                <button class="btn subtle tiny" title="在当前视口放一张指向它的引用卡" @click="emit('add-ref')">
                  <Icon name="bookmark" :size="13" />放引用卡
                </button>
                <button class="btn subtle tiny" @click="showRaw = !showRaw">
                  <Icon name="file" :size="13" />{{ showRaw ? '收起原文' : 'md 原文' }}
                </button>
              </div>
              <div v-if="writing && detail" class="write-box">
                <textarea v-model="bodyText" class="scroll-thin" rows="14"
                          placeholder="# 标题&#10;&#10;## 描述&#10;用自己的话说明它是什么。"></textarea>
                <div class="act-row">
                  <button class="btn primary" @click="emit('save-body', bodyText); writing = false">
                    <Icon name="check" :size="14" />保存正文
                  </button>
                  <button class="btn ghost" @click="writing = false">取消</button>
                  <span class="dim tail">只改正文，`## 关系` 和后面的章节原样保留</span>
                </div>
              </div>
              <pre v-if="showRaw && detail" class="code scroll-thin">{{ detail.raw }}</pre>

              <p v-if="selected.orphan" class="card warn-text" style="margin-top: 14px">
                这个节点在索引里不存在，只剩布局记录。
              </p>
            </template>
          </template>

          <!-- ---------- 关系 ---------- -->
          <template v-else-if="tab === 'relations'">
            <div v-if="!detail" class="empty">
              <Icon name="link" :size="30" :width="1.3" />
              <span class="t">先选一个节点</span>
            </div>
            <template v-else>
              <!-- 已有边的校验：review 角色判方向 / 类型 / 成不成立，结论存下来，边没改过就不重审 -->
              <div class="section pend-section" style="margin-top: 0" data-section="edge-check">
                <div class="section-head">
                  <Icon name="checklist" :size="13" />关系校验
                  <span v-if="edgeChecks.length" class="count" :title="`这个点的 ${outCount + inCount} 条关系里审过 ${edgeChecks.length} 条`">
                    审过 {{ edgeChecks.length }}/{{ outCount + inCount }}</span>
                  <button class="btn subtle tiny tail" :disabled="!!edgeRun || !(outCount + inCount)" data-act="check-edges"
                          title="让 review 角色审一遍这个点的关系（一次调用）；审过没改过的跳过"
                          @click="emit('check-edges', selected.id)">校验这个点的关系</button>
                </div>
                <EdgeRunBar :run="edgeRun" @stop="emit('stop-edge-check')" />
                <ul v-if="edgeChecks.some((c) => c.verdict !== 'ok' && !c.ignored)" class="pend-list">
                  <EdgeCheckRow v-for="c in edgeChecks.filter((x) => x.verdict !== 'ok' && !x.ignored)" :key="c.id"
                                :item="c" :busy="!!edgeRun" @fix="emit('fix-edge', $event)" @goto="emit('goto', $event)" />
                </ul>
                <p v-else-if="edgeChecks.length" class="dim" style="font-size: 11.5px; margin: 4px 0 0">审过的都没问题。</p>
              </div>
              <div v-if="pendingEdges.length" class="section pend-section" data-section="pending">
                <div class="section-head">
                  <Icon name="link" :size="13" />待审的边 <span class="count">{{ pendingEdges.length }}</span>
                </div>
                <ul class="pend-list">
                  <PendingEdgeRow v-for="e in pendingEdges" :key="e.id" :item="e" :relation-types="relationTypes"
                                  @decide="emit('decide-pending', $event)" @goto="emit('goto', $event)"
                                  @open-article="emit('open-article', $event)" />
                </ul>
              </div>
              <div class="section" style="margin-top: 0">
                <div class="section-head">
                  出边 <span class="count">{{ outCount }}</span>
                  <span class="tail dim">写回本文件</span>
                </div>
                <ul v-if="outCount">
                  <li v-for="e in detail.out" :key="e.id" class="edge-row">
                    <select :value="e.type" @change="emit('retype-edge', e, $event.target.value)">
                      <optgroup v-for="g in relationTypes" :key="g.family" :label="g.family">
                        <option v-for="t in g.types" :key="t" :value="t">{{ t }}</option>
                      </optgroup>
                    </select>
                    <Icon name="arrowRight" :size="13" class="dim" />
                    <span class="to link" @click="emit('goto', e.target)">{{ e.target }}</span>
                    <span v-if="e.year" class="yr tnum">{{ e.year }}</span>
                    <button class="icon-btn ghost tiny" title="删除这条关系" @click="emit('remove-edge', e)">
                      <Icon name="trash" :size="13" />
                    </button>
                  </li>
                </ul>
                <p v-else class="dim">还没有出边。</p>
              </div>

              <div class="section">
                <div class="section-head">
                  AI 建议
                  <button class="btn subtle tiny" style="margin-left: auto" :disabled="suggesting"
                          @click="emit('suggest')">
                    <Icon name="rotate" :size="13" />{{ suggesting ? '分析中…' : '请求建议' }}
                  </button>
                </div>
                <div v-if="suggesting" class="dim" style="padding: 6px 0">LLM 正在分析关系…</div>
                <template v-if="suggestions">
                  <div v-if="suggestions.duplicates?.length" class="card warn-text" style="margin-bottom: 8px">
                    <div v-for="(d, i) in suggestions.duplicates" :key="i" style="margin-bottom: 4px">
                      <strong>疑似重复：</strong>
                      <span class="link" @click="emit('goto', d.existing_id)">{{ d.existing_id }}</span>
                      <span class="dim" style="margin-left: 4px">{{ d.reason }}</span>
                    </div>
                  </div>
                  <ul v-if="suggestions.edges?.length">
                    <li v-for="(s, i) in suggestions.edges" :key="i" class="edge-row">
                      <span class="dim" style="min-width: 48px">{{ s.type }}</span>
                      <Icon :name="s.direction === 'out' ? 'arrowRight' : 'arrowLeft'" :size="13" class="dim" />
                      <span class="to link" @click="emit('goto', s.target)">{{ s.target }}</span>
                      <span v-if="s.reason" class="dim tail" style="font-size: 10.5px">{{ s.reason }}</span>
                      <button class="btn subtle tiny" title="采纳这条建议"
                              @click="emit('add-edge', { relation: s.type, target: s.target, direction: s.direction, year: '', note: s.reason })">
                        <Icon name="plus" :size="13" />采纳
                      </button>
                      <button class="icon-btn ghost tiny" title="忽略" @click="emit('dismiss-suggestion', i)">
                        <Icon name="x" :size="13" />
                      </button>
                    </li>
                  </ul>
                  <p v-else-if="!suggestions.duplicates?.length" class="dim">LLM 没有发现需要建议的关系。</p>
                  <p v-if="suggestions.suggested_field" class="dim" style="margin-top: 6px">
                    建议领域：<strong>{{ suggestions.suggested_field }}</strong>
                  </p>
                  <!-- 抽象层 / 年份：同一次调用顺手带回来的，只在这个点还空着时才会有。
                       不直接写盘——点「采纳」进变更卡，和改摘要一样人看过 diff 再落（4.4）。 -->
                  <p v-if="suggestions.suggested_layer" class="dim" style="margin-top: 6px">
                    建议抽象层：<strong>{{ suggestions.suggested_layer }}</strong>
                    <button class="btn subtle tiny" style="margin-left: 6px" :disabled="!detail"
                            :title="detail ? '进变更卡，确认后写回 md' : '先打开这个节点'"
                            @click="emit('set-layer', { id: selected.id, layer: suggestions.suggested_layer })">
                      采纳
                    </button>
                  </p>
                  <p v-if="suggestions.suggested_year" class="dim" style="margin-top: 6px">
                    建议年份：<strong class="tnum">{{ suggestions.suggested_year }}</strong>
                    <button class="btn subtle tiny" style="margin-left: 6px" :disabled="!detail"
                            :title="detail ? '进变更卡，确认后写回 md' : '先打开这个节点'"
                            @click="emit('set-year', { id: selected.id, year: suggestions.suggested_year })">
                      采纳
                    </button>
                  </p>
                </template>
                <p v-else-if="!suggesting" class="dim">点「请求建议」让 AI 分析可能的关系。</p>
              </div>

              <div class="section">
                <div class="section-head">新增关系</div>
                <div class="form-grid">
                  <select v-model="draft.relation" class="wide">
                    <option value="">选关系类型…</option>
                    <optgroup v-for="g in relationTypes" :key="g.family" :label="g.family">
                      <option v-for="t in g.types" :key="t" :value="t">{{ t }}</option>
                    </optgroup>
                  </select>
                  <input v-model="draft.target" class="wide" list="kg-nodes" placeholder="目标节点 id" />
                  <datalist id="kg-nodes"><option v-for="id in allNodeIds" :key="id" :value="id" /></datalist>
                  <input v-model="draft.year" placeholder="年份" />
                  <input v-model="draft.note" placeholder="说明（可选）" />
                  <button class="btn primary wide" :disabled="!draft.relation || !draft.target" @click="submitEdge">
                    <Icon name="plus" :size="14" />加入变更
                  </button>
                </div>
              </div>

              <div v-if="inCount" class="section">
                <div class="section-head">
                  入边 <span class="count">{{ inCount }}</span>
                  <span class="tail dim">写在对方文件里</span>
                </div>
                <ul>
                  <li v-for="e in detail.in_edges" :key="e.id" class="edge-row">
                    <span class="to link" @click="emit('goto', e.source)">{{ e.source }}</span>
                    <span class="dim">{{ e.type }}</span>
                    <Icon name="arrowRight" :size="13" class="dim" />
                  </li>
                </ul>
              </div>
            </template>
          </template>

          <!-- ---------- 变更 ---------- -->
          <template v-else>
            <div v-if="!pending.length" class="empty">
              <Icon name="save" :size="30" :width="1.3" />
              <span class="t">没有待写回的变更</span>
              <span class="s">在「关系」页改关系或改摘要，会先攒在这里，确认前不碰任何 md 文件。</span>
            </div>
            <template v-else>
              <div class="section-head" style="margin-top: 0">
                待写回 <span class="count">{{ pending.length }}</span>
                <span class="tail dim">确认前不碰 md</span>
              </div>
              <ul>
                <li v-for="(c, i) in pending" :key="i" class="edge-row">
                  <Icon :name="CHANGE_ICON[c.type] || 'pencil'" :size="13" class="dim" />
                  <span class="to">{{ describeChange(c) }}</span>
                  <button class="icon-btn ghost tiny" title="去掉这条" @click="emit('drop-change', i)">
                    <Icon name="x" :size="13" />
                  </button>
                </li>
              </ul>

              <div class="act-row">
                <button class="btn" @click="emit('preview-changes')">
                  <Icon name="eye" :size="14" />预览 diff
                </button>
                <button class="btn primary" :disabled="!changePreview" @click="emit('apply-changes')">
                  <Icon name="check" :size="14" />确认写入
                </button>
                <button class="btn ghost" @click="emit('clear-changes')">全部放弃</button>
              </div>

              <template v-if="changePreview">
                <div class="section">
                  <div class="section-head">将改动 <span class="count">{{ changePreview.files.length }}</span> 个文件</div>
                  <div v-for="f in changePreview.files" :key="f.path" style="margin-bottom: 10px">
                    <div class="dim" style="font-size: 11.5px">{{ f.path }}</div>
                    <pre class="code diff scroll-thin"><span v-for="(l, i) in parseDiff(f.diff).lines" :key="i"
                      :class="l.cls">{{ l.text }}
</span></pre>
                  </div>
                </div>
              </template>
            </template>
          </template>
        </div>
      </div>
    </template>
  </Drawer>
</template>

<style scoped>
.insp { display: flex; flex-direction: column; height: 100%; margin: -14px; }
.insp-body { flex: 1; min-height: 0; overflow: auto; padding: 14px; }
</style>
