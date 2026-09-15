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
})
const emit = defineEmits([
  'close', 'goto', 'edit-desc', 'add-ref', 'review', 'quiz', 'rename', 'finalize',
  'retype-edge', 'remove-edge', 'add-edge', 'drop-change',
  'preview-changes', 'apply-changes', 'clear-changes', 'save-body',
  'suggest', 'dismiss-suggestion',
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

/** diff 按行上色：+ 绿、- 红，其余保持正文色。 */
function diffLines(text) {
  return String(text || '').split('\n').map((line) => ({
    text: line,
    cls: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '',
  }))
}
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
          </button>
          <button :class="{ on: tab === 'changes' }" @click="tab = 'changes'">
            <Icon name="save" :size="14" />变更
            <span v-if="pending.length" class="badge">{{ pending.length }}</span>
          </button>
        </nav>

        <div class="insp-body scroll-thin">
          <!-- ---------- 详情 ---------- -->
          <template v-if="tab === 'detail'">
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
                <template v-if="selected.weight">
                  <dt>权重</dt><dd class="tnum">{{ (selected.weight * 100).toFixed(0) }}% · pageRank</dd>
                </template>
                <template v-if="detail"><dt>文件</dt><dd>{{ detail.path }}</dd></template>
              </dl>

              <div v-if="detail" class="act-row">
                <button class="btn subtle tiny" @click="writing ? (writing = false) : startWriting()">
                  <Icon name="file" :size="13" />{{ writing ? '收起正文' : '写内容' }}
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
                    <pre class="code diff scroll-thin"><span v-for="(l, i) in diffLines(f.diff)" :key="i"
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
