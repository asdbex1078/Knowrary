#!/usr/bin/env node
/**
 * 前端纯函数自测。零依赖——直接 `node web/tests/unit.mjs`，不引测试框架。
 *
 * 和 e2e（`web/tests/e2e_canvas.py`）的分工：e2e 开真无头 Chrome，验的是"拖一下真的落盘了吗"，
 * 一轮几十秒；这里只跑 `canvas/` 下那些**不碰 DOM 的算出来的东西**——折叠算到哪一层、
 * 菜单里这一项该不该出现、泳道怎么分。这些以前也只能靠 e2e 点开菜单才验得到，
 * 慢，而且失败了只知道"菜单不对"，不知道是哪条判断不对。
 *
 * 写法和 tools/knowrary/tests/run.py、server/tests/run.py 一样：一个 case 一个函数，
 * 名字就是断言本身，失败时把实际值打出来。
 */
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { blankMenu, buildMenu, edgeMenu, groupMenu, nodeMenu } from '../src/canvas/menus.js'
import { ancestors, computeCollapsed } from '../src/canvas/lod.js'
import { bucketOf, realGroups, zoneOf } from '../src/canvas/zones.js'
import { timelineOptions, bandCurve, buildTimeline, lineageOf, BY_LAYER, BY_SCHOOL, BY_DOMAIN }
  from '../src/canvas/timeline.js'
import { level, weekColumns } from '../src/panels/heat.js'
import { forModel } from '../src/composables/useChat.js'
import { localISO, todayISO } from '../src/today.js'
import { applyBody, mergeProposals, pickedCount, suspectsByArticle } from '../src/modelsPlan.js'
import { COL, LEFT, MIN_GAP, YEAR_PX, badgeOf, computeLanes, humanNumber, humanParams, techOptions } from '../src/canvas/modelLanes.js'
import { boundChildren, commonParent, draftPlan, groupBox, linkNodes, openReviewCounts, removalPatch, shapeAttrs, shapesInBox, splitByVersion, suggestions as topicSuggestions, removeEdgeChanges, ungroupPatch, upgradeChanges, versionStateOf, writtenEdgeId } from '../src/canvas/drawing.js'
import { findSenses, resolveSenses } from '../src/canvas/glossary.js'
import { nodeAttrs } from '../src/canvas/shapes.js'

const CASES = []
const test = (name, fn) => CASES.push([name, fn])

// ---------------------------------------------------------------- 夹具

/** 菜单 builder 要的那份状态快照。给最小的一份，需要什么再覆盖。 */
function ctx(over = {}) {
  return {
    index: { nodes: [], edges: [] },
    layout: { nodes: {}, groups: {}, edges: {} },
    dueIds: new Set(),
    pathFrom: null, pathHit: false, neighbor: null, focusGroup: null, showMap: true,
    nodeName: (id) => id,
    ...over,
  }
}

const ids = (menu) => menu.items.filter((i) => !i.sep).map((i) => i.id)
const item = (menu, id) => menu.items.find((i) => i.id === id)

// ---------------------------------------------------------------- 右键菜单

test('幽灵点只能建和拿掉，不能从它出发连边', () => {
  // 关系行要写进源节点的 md，而幽灵点连 md 都还没有——给了「建立关系」就是给一个必定失败的入口
  const menu = nodeMenu('还没建的点', ctx({ layout: { nodes: { '还没建的点': { state: 'ghost' } }, groups: {} } }))
  assert.deepEqual(ids(menu), ['build-ghost', 'drop-ghost'])
  assert.match(menu.subtitle, /还没建出来/)
})

test('草稿给「定稿」，已定稿的给「标记为草稿」，两者不同时出现', () => {
  const draft = nodeMenu('a', ctx({ index: { nodes: [{ id: 'a' }], edges: [] },
                                    layout: { nodes: { a: { state: 'draft' } }, groups: {} } }))
  assert.ok(ids(draft).includes('finalize') && !ids(draft).includes('draft'))
  const firm = nodeMenu('a', ctx({ index: { nodes: [{ id: 'a' }], edges: [] },
                                   layout: { nodes: { a: {} }, groups: {} } }))
  assert.ok(ids(firm).includes('draft') && !ids(firm).includes('finalize'))
})

test('只有到期的点才给「复习过了」', () => {
  const base = { index: { nodes: [{ id: 'a' }], edges: [] }, layout: { nodes: { a: {} }, groups: {} } }
  assert.ok(!ids(nodeMenu('a', ctx(base))).includes('review'))
  assert.ok(ids(nodeMenu('a', ctx({ ...base, dueIds: new Set(['a']) }))).includes('review'))
})

test('选了路径起点之后，别的点上变成「找到这里的路径」', () => {
  const base = { index: { nodes: [{ id: 'a' }, { id: 'b' }], edges: [] },
                 layout: { nodes: { a: {}, b: {} }, groups: {} } }
  assert.ok(ids(nodeMenu('b', ctx(base))).includes('path-from'))           // 没起点：给"设为起点"
  const withFrom = nodeMenu('b', ctx({ ...base, pathFrom: 'a' }))
  assert.ok(ids(withFrom).includes('path-to') && !ids(withFrom).includes('path-from'))
  const onSelf = nodeMenu('a', ctx({ ...base, pathFrom: 'a' }))            // 起点自己：给"取消"
  assert.equal(item(onSelf, 'path-from').label, '取消路径起点')
  assert.equal(item(onSelf, 'path-from').on, true)
})

test('没有高亮着的路径就不给「取消路径高亮」', () => {
  const base = { index: { nodes: [{ id: 'a' }], edges: [] }, layout: { nodes: { a: {} }, groups: {} } }
  assert.ok(!ids(nodeMenu('a', ctx(base))).includes('path-clear'))
  assert.ok(ids(nodeMenu('a', ctx({ ...base, pathHit: true }))).includes('path-clear'))
})

test('父域没有组内重排（里面只剩框，重排它没有意义）', () => {
  const layout = {
    groups: { top: { name: '顶' }, sub: { name: '子', parent: 'top' } },
    nodes: { a: { group: 'sub' }, b: { group: 'sub' } },
  }
  assert.ok(!ids(groupMenu('top', false, ctx({ layout }))).some((i) => i.startsWith('inner-')))
  assert.ok(ids(groupMenu('sub', false, ctx({ layout }))).some((i) => i.startsWith('inner-')))
})

test('只有一个点的域也不给组内重排', () => {
  const layout = { groups: { g: { name: 'G' } }, nodes: { a: { group: 'g' } } }
  assert.ok(!ids(groupMenu('g', false, ctx({ layout }))).some((i) => i.startsWith('inner-')))
})

test('折叠成簇卡片时只给「展开」，不给「折叠」', () => {
  const layout = { groups: { g: { name: 'G' } }, nodes: {} }
  assert.ok(!ids(groupMenu('g', true, ctx({ layout }))).includes('collapse'))
  assert.ok(ids(groupMenu('g', false, ctx({ layout }))).includes('collapse'))
})

test('绑了总览文档才给「打开 / 解绑」，没绑给「加一个」', () => {
  const bound = { groups: { g: { name: 'G', doc: 'x' } }, nodes: {} }
  const free = { groups: { g: { name: 'G' } }, nodes: {} }
  assert.ok(ids(groupMenu('g', false, ctx({ layout: bound }))).includes('open-doc'))
  assert.ok(ids(groupMenu('g', false, ctx({ layout: bound }))).includes('unbind-doc'))
  assert.ok(ids(groupMenu('g', false, ctx({ layout: free }))).includes('new-doc'))
  assert.ok(!ids(groupMenu('g', false, ctx({ layout: free }))).includes('unbind-doc'))
})

test('没手工拐点时，「清掉手工拐点」是灰的', () => {
  const index = { nodes: [], edges: [{ id: 'e1', source: 'a', target: 'b', type: '包含', family: '结构' }] }
  assert.equal(edgeMenu('e1', ctx({ index })).items.find((i) => i.id === 'edge-clear').disabled, true)
  const withV = ctx({ index, layout: { nodes: {}, groups: {}, edges: { e1: [{ x: 1, y: 1 }] } } })
  assert.equal(edgeMenu('e1', withV).items.find((i) => i.id === 'edge-clear').disabled, false)
})

test('小地图开着时菜单里写「隐藏」，关着写「显示」', () => {
  assert.equal(item(blankMenu(ctx({ showMap: true })), 'map').label, '隐藏小地图')
  assert.equal(item(blankMenu(ctx({ showMap: false })), 'map').label, '显示小地图')
})

test('buildMenu 按右键点到什么分派，cluster 就是折叠起来的 group', () => {
  const layout = { groups: { g: { name: 'G' } }, nodes: {} }
  assert.ok(ids(buildMenu('cluster', 'g', ctx({ layout }))).includes('enter'))
  assert.ok(!ids(buildMenu('cluster', 'g', ctx({ layout }))).includes('collapse'))
  assert.equal(buildMenu('deco', 'note:n1', ctx()).title, '便签')
  assert.equal(buildMenu('blank', null, ctx()).title, '画布')
})

// ---------------------------------------------------------------- LOD / 时间线

test('ancestors 一路数到顶层', () => {
  const groups = { top: {}, mid: { parent: 'top' }, leaf: { parent: 'mid' } }
  assert.deepEqual(ancestors(groups, 'leaf'), ['mid', 'top'])
  assert.deepEqual(ancestors(groups, 'top'), [])
})

test('钉住展开的域，缩到再小也不折叠', () => {
  const layout = { groups: { g: { x: 0, y: 0, width: 400, height: 300, pinned: 'expanded' } },
                   nodes: { a: { group: 'g' } } }
  const folded = computeCollapsed(layout, 0.05, { auto: true })
  assert.ok(!folded.has('g'), [...folded])
})

test('钉住折叠的域，放到再大也不展开', () => {
  const layout = { groups: { g: { x: 0, y: 0, width: 400, height: 300, pinned: 'collapsed' } },
                   nodes: { a: { group: 'g' } } }
  assert.ok(computeCollapsed(layout, 2, { auto: true }).has('g'))
})

test('关掉自动折叠后，只剩钉住折叠的那些', () => {
  const layout = { groups: { a: { x: 0, y: 0, width: 400, height: 300, pinned: 'collapsed' },
                             b: { x: 0, y: 0, width: 400, height: 300 } },
                   nodes: { n1: { group: 'a' }, n2: { group: 'b' } } }
  const folded = computeCollapsed(layout, 0.05, { auto: false })
  assert.deepEqual([...folded], ['a'])
})

test('时间线选项来自 layout 的分组，空 layout 不炸', () => {
  assert.deepEqual(timelineOptions(null), [])
  const opts = timelineOptions({ groups: { g: { name: '硬件' } }, nodes: { a: { group: 'g' } } })
  assert.ok(Array.isArray(opts))
})

/** 真实布局的形状：顶层按主题、二级按抽象层，于是二级里必然有一堆重名。 */
const twoFields = {
  groups: {
    'g-计算机系统': { name: '计算机系统' },
    'g-计算机系统--理论': { name: '理论', parent: 'g-计算机系统' },
    'g-计算机系统--硬件': { name: '硬件', parent: 'g-计算机系统' },
    'g-AI': { name: 'AI' },
    'g-AI--理论': { name: '理论', parent: 'g-AI' },
    'g-AI--AI应用': { name: 'AI应用', parent: 'g-AI' },
  },
}

test('重名的二级分组带上父级——选择器里不能并排站两个「理论」', () => {
  // 布局按「主题 × 抽象层」铺开，理论 / 硬件 / 体系结构 / 系统软件 / AI应用 天然各有两份。
  // 只显示 name 的话点哪个全靠试，等于把这个功能藏了一半。
  const byId = new Map(timelineOptions(twoFields).map((o) => [o.id, o.name]))
  assert.equal(byId.get('g-计算机系统--理论'), '计算机系统／理论')
  assert.equal(byId.get('g-AI--理论'), 'AI／理论')
  assert.equal(byId.get('g-计算机系统--硬件'), '硬件', '不重名的不该加前缀，加了只是啰嗦')
  assert.equal(byId.get('g-计算机系统'), '计算机系统', '顶层没有父级，原样')
  const names = [...byId.values()]
  assert.equal(new Set(names).size, names.length, '还有显示名撞车的：' + names.join(' / '))
})

test('时间线选项按树序铺，父的紧跟着自己的孩子', () => {
  // 以前按 (depth, name) 排：所有二级分组被跨父级打散，AI 和它的孩子隔着半个列表，
  // 而两个「理论」正好被 name 排到一起。缩进就成了唯一线索，同名同缩进等于没线索。
  const flat = timelineOptions(twoFields).map((o) => `${'  '.repeat(o.depth)}${o.name}`)
  assert.equal(flat.length, 6)
  // 断言只锁"父子相邻"，不锁两个顶层谁在前——那由 zh 排序决定，不是这个函数的语义
  const block = (top) => flat.slice(flat.indexOf(top), flat.indexOf(top) + 3).sort()
  assert.deepEqual(block('AI'), ['  AI／理论', '  AI应用', 'AI'].sort())
  assert.deepEqual(block('计算机系统'), ['  硬件', '  计算机系统／理论', '计算机系统'].sort())
})

test('分组树坏掉也要把每个分组摆出来', () => {
  // 父指向不存在的分组 / 指向自己 / 两个互为父子：都是 layout 手改或迁移残留能造出来的。
  // 这种时候少列一个分组最坑——你只会以为它没了，而不会想到是选择器没走到它。
  const broken = timelineOptions({ groups: {
    ghost: { name: '爹没了', parent: '不存在' },
    self: { name: '自己当爹', parent: 'self' },
    a: { name: '甲', parent: 'b' },
    b: { name: '乙', parent: 'a' },
  } })
  assert.deepEqual(broken.map((o) => o.id).sort(), ['a', 'b', 'ghost', 'self'])
  assert.equal(broken.find((o) => o.id === 'ghost').depth, 0)
})

// ---------------------------------------------------------------- 日历热力图

test('日期一律按本地算，不是 UTC', () => {
  // `toISOString().slice(0, 10)` 在 UTC+8 会把本地午夜的 9/20 说成 9/19。
  // 疼在三处：`learned` 是永久写进 md 的、`placedAt` 决定草稿放了多久、
  // 热力图每一格的 key 全往前错一天（今天那格顶着昨天的 key 查，永远查不到）。
  assert.equal(localISO(new Date('2026-09-20T00:00:00')), '2026-09-20')
  assert.equal(localISO(new Date('2026-09-20T23:59:59')), '2026-09-20')
  assert.equal(localISO(new Date('2026-01-05T00:00:00')), '2026-01-05')   // 月 / 日都要补零
  assert.equal(todayISO(), localISO(new Date()))
  assert.match(todayISO(), /^\d{4}-\d{2}-\d{2}$/)
})

test('热力图铺到的最后一格就是 to 那天', () => {
  const cols = weekColumns('2026-09-01', '2026-09-20')
  const flat = cols.flat().filter(Boolean)
  assert.equal(flat[0], '2026-09-01')
  assert.equal(flat[flat.length - 1], '2026-09-20', '最后一天没铺进去 = 今天的数据看不见')
  assert.equal(cols[0].length, 7)
  assert.equal(new Set(flat).size, flat.length, '有重复的日期')
  assert.ok(cols[0].indexOf(null) >= 0, '9/1 是周二，那一列前面该有占位')
  assert.deepEqual(weekColumns(null, null), [])
})

test('热力等级：复习一次也要点亮，模型调用不算学习量', () => {
  assert.equal(level(null), 0)
  assert.equal(level({ built: 0, reviews: 0, answers: 0, calls: 9, cost_usd: 3 }), 0)
  assert.equal(level({ built: 0, reviews: 1, answers: 0 }), 1, '只复习了一次就该亮')
  assert.equal(level({ built: 1, reviews: 0, answers: 0 }), 2)
  assert.equal(level({ built: 4, reviews: 0, answers: 0 }), 4)
})

// ---------------------------------------------------------------- 只看一条演化线

/**
 * 注意力那一支的真实形状（实盘 2026-09-22）：
 *
 *   CTC → 注意力机制 → Transformer → BERT / ViT / Whisper / KV-Cache
 *                       MHA → MQA → GQA          MHA → MLA
 *
 * 要点全在这张图里：`MHA` 是 `MQA` 的上游但不是根的祖先，`MLA` 是 `MQA` 的同父兄弟，
 * 而 `CUDA 被激活 Transformer` 是跨代点燃、不该把硬件那边拉进来。
 */
function attentionGraph() {
  const y = { CTC: 2006, 注意力机制: 2014, Transformer: 2017, MHA: 2017, 'KV-Cache': 2017,
              BERT: 2018, MQA: 2019, ViT: 2020, Whisper: 2022, GQA: 2023, MLA: 2024, CUDA: 1999 }
  const E = [['CTC', '演化为', '注意力机制'], ['注意力机制', '演化为', 'Transformer'],
             ['Transformer', '演化为', 'BERT'], ['Transformer', '演化为', 'ViT'],
             ['Transformer', '演化为', 'Whisper'], ['Transformer', '扩展为', 'KV-Cache'],
             ['Transformer', '演化为', 'MQA'], ['MHA', '演化为', 'MQA'],
             ['MHA', '演化为', 'GQA'], ['MHA', '演化为', 'MLA'], ['MQA', '演化为', 'GQA'],
             ['CUDA', '被激活', 'Transformer']]
  return {
    nodes: Object.entries(y).map(([id, year]) => ({ id, name: id, year, field: 'AI' })),
    // `被激活` 也属于演化族——族相同、类型不同，血缘只认类型（这正是这组用例要盯的）
    edges: E.map(([source, type, target], i) => ({
      id: `e${i}`, source, target, type, family: '演化', year: y[target] ?? null })),
  }
}

test('一条演化线：祖先 + 后代 + 直接旁系，`被激活` 不算血缘', () => {
  const { core, kin } = lineageOf(attentionGraph().edges, '注意力机制')
  assert.deepEqual([...core].sort(),
    ['BERT', 'CTC', 'GQA', 'KV-Cache', 'MQA', 'Transformer', 'ViT', 'Whisper', '注意力机制'].sort(),
    '主线 = 往上到 CTC、往下到 GQA 的全部')
  assert.deepEqual([...kin].sort(), ['MHA', 'MLA'].sort(),
    'MHA 是 MQA 的上游（不是根的祖先），MLA 是 MQA 的同父兄弟——正是"这一支"的内容')
  assert.ok(!core.has('CUDA') && !kin.has('CUDA'),
            'CUDA 被激活 Transformer 是跨代点燃，算进来一条线就窜到硬件那边去了')
})

test('一条演化线：没有演化边的点只剩它自己', () => {
  const { core, kin } = lineageOf(attentionGraph().edges, 'CUDA')
  assert.deepEqual([...core], ['CUDA'], '入口那层据此拦下来，不让人进一张只有一个点的图')
  assert.equal(kin.size, 0)
})

test('一条演化线：`源自` 反向折算，方向不会读反', () => {
  const edges = [{ id: 'e0', source: 'B', target: 'A', type: '源自', family: '演化' }]
  assert.deepEqual([...lineageOf(edges, 'A').core].sort(), ['A', 'B'], 'B 源自 A ⇒ A 是 B 的上游')
})

test('只看一条线：可见集合先裁再算比例尺，旁系带 kin 标记', () => {
  const index = attentionGraph()
  const all = buildTimeline(index, { nodes: {}, groups: {} }, {})
  const one = buildTimeline(index, { nodes: {}, groups: {} }, { lineage: '注意力机制' })
  assert.equal(all.placed.size, 12, '全图 12 个点')
  assert.equal(one.placed.size, 11, '裁掉 CUDA：它只靠「被激活」挂在这条线上')
  assert.ok(one.ticks[0].year === 2006,
            `年份尺度按裁完的集合重标定（最早从 2006 起），实际 ${one.ticks[0].year}`)
  assert.equal(one.placed.get('MLA').kin, true, '旁系要能在渲染层画淡')
  assert.ok(!one.placed.get('Transformer').kin, '主线不带 kin')
  assert.deepEqual([one.lineage.root, one.lineage.core.length, one.lineage.kin.length],
                   ['注意力机制', 9, 2], 'plan 带着血缘信息，横幅和面板都读它')
})

test('只看一条线 + 主干道：主轴就是这条线里最长的那串', () => {
  const plan = buildTimeline(attentionGraph(), { nodes: {}, groups: {} },
                             { lineage: '注意力机制', trunk: true })
  assert.deepEqual(plan.chain, ['CTC', '注意力机制', 'Transformer', 'MQA', 'GQA'],
                   '并行的捷径（MHA → GQA）抄不短主轴')
  assert.equal(plan.placed.get('CTC').trunk, true)
  assert.ok(!plan.placed.get('MLA').trunk, 'MLA 是旁支，挂在主轴上下')
})

// ---------------------------------------------------------------- 流派泳道

test('流派分道：出现早的在上面，未归派垫底', () => {
  const index = { nodes: [
    { id: '甲', name: '甲', year: 1990 }, { id: '丙', name: '丙', year: 2005 },
    { id: '辛', name: '辛', year: 2000 },
  ], edges: [] }
  const schools = [
    { id: '老派', kind: '流派', name: '老派', start: 1985, end: 2000, members: ['甲'], curve: [], peak: 0 },
    { id: '新派', kind: '流派', name: '新派', start: 2005, open: true, members: ['丙'], curve: [], peak: 0 },
  ]
  const plan = buildTimeline(index, { nodes: {}, groups: {} }, { timelines: [BY_SCHOOL], schools })
  assert.deepEqual(plan.lanes.map((l) => l.name), ['老派', '新派', '未归派'],
                   '按 start_year 排，「未归派」是余数不是一派，永远垫底')
  assert.equal(plan.lanes[0].school.id, '老派', '道自己就带着流派信息，渲染层按它上色')
  assert.equal(plan.lanes[2].school, null, '未归派没有流派')
})

test('流派分道：跨两派的点每道一份，第二份是影子', () => {
  const index = { nodes: [{ id: '现代Intel', name: '现代Intel', year: 1995 }], edges: [] }
  const schools = [
    { id: 'CISC', kind: '流派', name: 'CISC', start: 1964, open: true, members: ['现代Intel'], curve: [], peak: 0 },
    { id: 'RISC', kind: '流派', name: 'RISC', start: 1980, open: true, members: ['现代Intel'], curve: [], peak: 0 },
  ]
  const plan = buildTimeline(index, { nodes: {}, groups: {} }, { timelines: [BY_SCHOOL], schools })
  // 前端 CISC 指令集、后端拆成 RISC 式 μops —— 它真的同时属于两派。
  // placed 是 Map<id, 位置>、一个点只有一个位置，所以第二派起用合成 id 补影子。
  assert.deepEqual([...plan.placed.keys()], ['现代Intel', '现代Intel@RISC'])
  assert.equal(plan.placed.get('现代Intel@RISC').shadow, true)
  assert.equal(plan.placed.get('现代Intel@RISC').realId, '现代Intel', '影子要能跳回真身')
  assert.ok(!plan.placed.get('现代Intel').shadow)
})

test('按抽象层（2026-10-12 跨层）：主层画真身，其余几层画影子，那几层的道也开出来', () => {
  const index = { nodes: [
    { id: 'CPU', name: 'CPU', year: 1971, layer: '硬件', layers: ['硬件', '体系结构'] },
    { id: '图灵机', name: '图灵机', year: 1936, layer: '理论' },
  ], edges: [{ source: '图灵机', target: 'CPU', type: '演化为', family: '演化', year: 1971 }] }
  const plan = buildTimeline(index, { nodes: {}, groups: {} }, { timelines: [BY_LAYER] })
  assert.deepEqual(plan.lanes.map((l) => l.name), ['体系结构', '硬件', '理论'], '只在副层出现的「体系结构」也要开道，次序照旧底层在下')
  assert.equal(plan.placed.get('CPU').lane, '硬件', '真身在主层')
  assert.equal(plan.placed.get('CPU@体系结构').shadow, true)
  assert.equal(plan.placed.get('CPU@体系结构').realId, 'CPU')
  const noLayer = buildTimeline(index, { nodes: {}, groups: {} }, { timelines: [] })
  assert.ok(![...noLayer.placed.keys()].some((k) => k.includes('@')), '不按层时不画影子')
})

test('层区块（2026-10-12）：不进选择器、不是分组；选领域框时泳道按层分；取色 / 捆边按点落在哪个区块', () => {
  const layout = {
    groups: {
      'g-计': { name: '计算机系统', x: 0, y: 0, w: 1000, h: 600, parent: null },
      'g-计--理论': { name: '理论', x: 20, y: 40, w: 400, h: 200, parent: 'g-计', kind: 'zone' },
      'g-计--硬件': { name: '硬件', x: 450, y: 40, w: 400, h: 200, parent: 'g-计', kind: 'zone' },
      'g-AI': { name: 'AI', x: 0, y: 700, w: 600, h: 300, parent: null },
      'g-AI--理论': { name: '理论', x: 20, y: 740, w: 300, h: 200, parent: 'g-AI', kind: 'zone' },
      'g-我的': { name: '我的子簇', x: 460, y: 300, w: 200, h: 200, parent: 'g-计' },
    },
    nodes: {
      图灵机: { x: 40, y: 80, w: 180, h: 60, group: 'g-计' },
      CPU: { x: 500, y: 80, w: 180, h: 60, group: 'g-计' },
      散点: { x: 40, y: 400, w: 180, h: 60, group: 'g-计' },
    },
  }
  const opts = timelineOptions(layout).map((o) => o.name)
  assert.deepEqual(opts, ['计算机系统', '我的子簇', 'AI'], '区块不进选择器，同名的「理论」不再并排站两个')
  assert.deepEqual(Object.keys(realGroups(layout.groups)), ['g-计', 'g-AI', 'g-我的'])
  assert.equal(zoneOf(layout, 'CPU'), 'g-计--硬件')
  assert.equal(bucketOf(layout, '散点'), 'g-计', '不在任何区块里的按所属分组')
  const index = { nodes: [
    { id: '图灵机', name: '图灵机', year: 1936, layer: '理论' }, { id: 'CPU', name: 'CPU', year: 1971, layer: '硬件' },
    { id: '散点', name: '散点', year: 2000 },
  ], edges: [] }
  const plan = buildTimeline(index, layout, { timelines: ['g-计'] })
  assert.deepEqual(plan.lanes.map((l) => l.name), ['计算机系统／未分层', '计算机系统／硬件', '计算机系统／理论'],
                   '直属领域框的点按层分道（上层在上），不全挤进「直属」')
})

test('流派走势：阶梯不是折线，平台就是停摆', () => {
  const scale = { at: (y) => y, width: 100 }
  const school = { start: 1940, end: null, open: true,
                   curve: [{ year: 1940, n: 0 }, { year: 1943, n: 1 }, { year: 1986, n: 2 }] }
  const pts = bandCurve(school, scale, 2, 2500, 40)
  const flat = pts.filter((p, i) => i > 0 && p[1] === pts[i - 1][1])
  assert.ok(flat.length >= 2, '阶梯要有平段，画成斜线会让人以为在稳步增长')
  assert.equal(pts[pts.length - 1][0], 2500, '最后一级之后一直平推到轴尾')
})

test('流派走势：共用 y 刻度，成员少的就该画得矮', () => {
  const scale = { at: (y) => y, width: 100 }
  const few = { start: 1900, end: 2000, curve: [{ year: 1900, n: 0 }, { year: 1950, n: 1 }] }
  const many = { start: 1900, end: 2000, curve: [{ year: 1900, n: 0 }, { year: 1950, n: 10 }] }
  const top = (s) => Math.min(...bandCurve(s, scale, 10, 2000, 40).map((p) => p[1]))
  assert.ok(top(few) > top(many), '各自归一化会把 1 个和 10 个画成一样高')
  assert.ok(top(many) < 40 / 2, '满格的那条要真的顶到上面')
})

test('流派走势：道的高度变了，曲线跟着缩放', () => {
  // 曲线是道的背景，写死高度会让它在高的道里缩在底部、在矮的道里溢出去
  const scale = { at: (y) => y, width: 100 }
  const s = { start: 1900, end: 2000, curve: [{ year: 1900, n: 0 }, { year: 1950, n: 1 }] }
  // 顶点永远离道顶 3px（满格就是满格），**跟着高度变的是纵向跨度**
  const span = (h) => {
    const ys = bandCurve(s, scale, 1, 2000, h).map((p) => p[1])
    return Math.max(...ys) - Math.min(...ys)
  }
  assert.ok(span(80) > span(40) * 1.5, `道高翻倍，跨度也该翻倍：${span(80)} vs ${span(40)}`)
  assert.deepEqual(bandCurve(s, scale, 1, 2000, 0), [], '高度为 0 就别画')
})

test('流派和领域线是两个正交的维度，同一个点归属不同', () => {
  // AlexNet 在「主张」这一维属于连接主义，在「领域」这一维属于 CV。
  // 混成一档的话它会和两者排在同一列里 —— 而那是两件不同的事。
  const index = { nodes: [{ id: 'AlexNet', name: 'AlexNet', year: 2012 }], edges: [] }
  const lines = [
    { id: '连接主义', kind: '流派', name: '连接主义', start: 1943, open: true,
      members: ['AlexNet'], curve: [], peak: 0 },
    { id: 'CV', kind: '领域线', name: 'CV', start: 1960, open: true,
      members: ['AlexNet'], curve: [], peak: 0 },
  ]
  const lane = (mode) => buildTimeline(index, { nodes: {}, groups: {} },
                                       { timelines: [mode], schools: lines }).lanes.map((l) => l.name)
  assert.deepEqual(lane(BY_SCHOOL), ['连接主义'], '按流派只看流派')
  assert.deepEqual(lane(BY_DOMAIN), ['CV'], '按领域线只看领域线')
})

test('线只筛当档那一种，另一种不该冒出来当泳道', () => {
  // 少了 kind 过滤的话，「按流派」会把 NLP / CV / ASR 也铺成道 —— 不报错，只是读不懂
  const index = { nodes: [{ id: 'x', name: 'x', year: 2000 }], edges: [] }
  const lines = [{ id: 'NLP', kind: '领域线', name: 'NLP', start: 1950, open: true,
                   members: ['x'], curve: [], peak: 0 }]
  const plan = buildTimeline(index, { nodes: {}, groups: {} },
                             { timelines: [BY_SCHOOL], schools: lines })
  assert.deepEqual(plan.lanes.map((l) => l.name), ['未归派'],
                   '按流派时，只属于领域线的点落「未归派」')
})

test('跨多条线的点：真身落在声明的主道上，影子在别的道', () => {
  // 边只连真身，所以主道选错会让「源自 X」的箭头汇聚到错的道上
  const index = { nodes: [{ id: 'T', name: 'T', year: 2017 }], edges: [] }
  const lines = [
    { id: '早线', kind: '领域线', name: '早线', start: 1950, open: true, members: ['T'], curve: [], peak: 0 },
    { id: '晚线', kind: '领域线', name: '晚线', start: 1990, open: true, members: ['T'], curve: [], peak: 0 },
  ]
  const plan = buildTimeline(index, { nodes: {}, groups: {} },
                             { timelines: [BY_DOMAIN], schools: lines, homes: { T: '晚线' } })
  assert.equal(plan.placed.get('T').lane, '晚线', '真身在声明的主道')
  assert.equal(plan.placed.get('T@早线').lane, '早线', '另一条道上是影子')
  assert.equal(plan.placed.get('T@早线').shadow, true)
  assert.ok(!plan.placed.has('T@晚线'), '主道上不该再多一个影子')
})

// ---------------------------------------------------------------- 对话：哪几条该发给模型

test('没答成的那一轮不喂回模型', () => {
  // 断线时收到的半截话留在屏幕上是有用的，但它不是"模型答过的一轮"：
  // 发回去模型会当成自己上次就是这么答的接着往下编，而服务端留档里记的是"（这一轮没答成：…）"
  const log = [
    { role: 'user', content: '聊一下 n-gram' },
    { role: 'assistant', content: '我先查一下……', failed: true, error: '连接失败' },
    { role: 'user', content: '再试一次' },
    { role: 'assistant', content: '好的' },
  ]
  assert.deepEqual(forModel(log).map((m) => m.content),
                   ['聊一下 n-gram', '再试一次', '好的'])
})

test('刷新后从留档读回来的那一轮同样摘掉', () => {
  // 留档行里正文就是记号本身，服务端读回来时标了 failed（server/chat.py 的 _is_failed）
  const log = [
    { role: 'user', content: '在吗', failed: false },
    { role: 'assistant', content: '', error: '（这一轮没答成：连接失败）', failed: true },
  ]
  assert.deepEqual(forModel(log).map((m) => m.role), ['user'])
})

test('正常的空回复照旧不发，但我说的话一个都不少', () => {
  const log = [{ role: 'user', content: '在吗' }, { role: 'assistant', content: '' }]
  assert.deepEqual(forModel(log).map((m) => m.role), ['user'])
})

// ---------------------------------------------------------------- 模板：props 名字要对得上

const camelize = (s) => s.replace(/-(\w)/g, (_, c) => c.toUpperCase())
/** defineProps({...}) 里**第一层**的键名。
 *  `default: () => ({})` 里也有 `})`，所以只能数括号，不能用非贪婪正则去够那个收尾。 */
function declaredProps(text) {
  const at = text.indexOf('defineProps({')
  if (at < 0) return null
  const body = text.slice(text.indexOf('{', at))
  let flat = '', depth = 0, quote = ''
  for (let i = 0; i < body.length; i++) {
    const c = body[i]
    if (quote) { if (c === quote && body[i - 1] !== '\\') quote = ''; continue }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue }
    if (c === '/' && body[i + 1] === '/') { i = body.indexOf('\n', i); if (i < 0) break; continue }
    if ('{(['.includes(c)) { depth++; continue }
    if ('})]'.includes(c)) { depth--; if (!depth) break; continue }
    if (depth === 1) flat += c
  }
  const names = [...flat.matchAll(/(?:^|,)\s*([A-Za-z_$][\w$]*)\s*:/g)].map((x) => x[1])
  return names.length ? new Set(names) : null
}
/** 取 `<Tag ...>` 开标签里的那段属性文本（属性值里可能有 `>`，所以要按引号走）。 */
function openTags(text, tag) {
  const out = []
  const re = new RegExp(`<${tag}(?=[\\s/>])`, 'g')
  for (let m = re.exec(text); m; m = re.exec(text)) {
    let i = m.index + m[0].length, quote = ''
    for (; i < text.length; i++) {
      const c = text[i]
      if (quote) { if (c === quote) quote = '' } else if (c === '"' || c === "'") quote = c
      else if (c === '>') break
    }
    out.push(text.slice(m.index + m[0].length, i))
  }
  return out
}
// 这些是落到根元素上的普通属性 / 指令，不是 props，别拿去和 defineProps 对
const NOT_A_PROP = new Set(['class', 'style', 'id', 'key', 'ref', 'title', 'is', 'slot',
                            'name', 'disabled', 'placeholder', 'type', 'value', 'tabindex', 'role'])

test('父组件传的每个 prop，子组件都要真的收得到', () => {
  // `:save-llm` 会被 camelize 成 `saveLlm`。子组件声明成 `saveLLM` 就永远对不上，
  // 拿到的是 default（null），编译不报错、lint 不报错——只有点那个按钮才发现它是哑的。
  const src = fileURLToPath(new URL('../src', import.meta.url))
  const props = new Map()
  for (const file of vueFiles(src)) props.set(file, declaredProps(readFileSync(file, 'utf8')))
  const bad = []
  for (const file of vueFiles(src)) {
    const text = readFileSync(file, 'utf8')
    for (const [, tag, rel] of text.matchAll(/import\s+(\w+)\s+from\s+'([^']+\.vue)'/g)) {
      const known = props.get(join(dirname(file), rel))
      if (!known) continue
      for (const chunk of openTags(text, tag)) {
        const attrs = [...chunk.matchAll(/(?:^|\s)(?::|v-bind:)([\w.-]+)=/g)].map((m) => m[1])
        for (const raw of attrs) {
          const attr = raw.split('.')[0]
          if (NOT_A_PROP.has(attr) || /^(data-|aria-|v-|@)/.test(attr)) continue
          if (!known.has(camelize(attr))) {
            bad.push(`${file.slice(src.length + 1)} 里的 <${tag} :${attr}> → 子组件声明里没有 \`${camelize(attr)}\``)
          }
        }
      }
    }
  }
  assert.deepEqual(bad, [], `这些 prop 传过去会变成 default：\n${bad.join('\n')}`)
})

// ---------------------------------------------------------------- 模板：别把事件当参数喂进去

/** 递归收 .vue 文件。测试里只走这一处，没必要引 glob。 */
function vueFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = join(dir, e.name)
    if (e.isDirectory()) return vueFiles(full)
    return e.name.endsWith('.vue') ? [full] : []
  })
}

/** 形参列表里的第一个（按括号深度切，`{ a = 1 }` 里的逗号不算分隔）。 */
function firstParam(params) {
  let depth = 0
  for (let i = 0; i < params.length; i += 1) {
    const ch = params[i]
    if ('([{'.includes(ch)) depth += 1
    else if (')]}'.includes(ch)) depth -= 1
    else if (ch === ',' && depth === 0) return params.slice(0, i)
  }
  return params
}

test('事件绑到带默认参数的函数上，必须写成 fn()', () => {
  // `@click="saveModels"` 编译成 `onClick: saveModels`，点一下 MouseEvent 就成了第一个实参，
  // 把 `providers = draft.providers` 那个默认值顶掉——「保存配置」当场报「至少保留一个 provider」。
  // 带默认值的形参等于在说"调用方不传"，所以这一类一律要求模板里自己加括号。
  //
  // **不只 @click**：自定义事件同样会把 emit 的载荷塞进第一个形参。
  // `@apply-changes="applyChanges"` 碰上哪天 Inspector 改成 `emit('apply-changes', x)`，
  // `force` 就悄悄变成真——审核当场被绕过，而且不报任何错。
  const src = fileURLToPath(new URL('../src', import.meta.url))
  const bad = []
  for (const file of vueFiles(src)) {
    const text = readFileSync(file, 'utf8')
    for (const [, ev, name] of text.matchAll(/@([a-z][\w.-]*)="([A-Za-z_$][\w$]*)"/g)) {
      const sig = text.match(new RegExp(`\\b(?:async\\s+)?function\\s+${name}\\s*\\(([^)]*)\\)`))
      // **只看第一个形参**：事件载荷落在它身上。后面的形参有默认值是正常写法
      // （`applyChatCard({ card, i, j }, force = false)` 就该这么写），一并报会全是噪音。
      if (sig && firstParam(sig[1]).includes('=')) {
        bad.push(`${file.slice(src.length + 1)}: @${ev}="${name}" → ${name}(${sig[1]})`)
      }
    }
  }
  assert.deepEqual(bad, [], `这些绑定会把事件载荷当第一个参数塞进去：\n${bad.join('\n')}`)
})

// ---------------------------------------------------------------- 抽成模型谱系（2026-09-30）

const PROPOSALS = [
  { path: 'a.md', source: '[[a]]', families: [{ name: 'DeepSeek', target: 'm/DeepSeek.md', digest: 'd1', exists: true,
    models: [{ name: 'V3', status: 'new', fields: { 参数: '671B' }, conflicts: [], suspicious: [{ key: '参数', value: '671B', why: 'x' }] },
      { name: 'V2', status: 'same', fields: {}, conflicts: [{ key: '发布', article: '2024-06', catalog: '2024-05' }], suspicious: [] }] }] },
  { path: 'b.md', source: '[[b]]', families: [{ name: 'DeepSeek', target: 'm/DeepSeek.md', digest: 'd1', exists: true,
    models: [{ name: 'v3', status: 'new', fields: { 参数: '670B', 激活: '37B' }, conflicts: [],
      suspicious: [{ key: '参数', value: '670B', why: 'y' }, { key: '激活', value: '37B', why: 'z' }] }] }] },
]

test('两篇都抽到同一个型号：并成一条，先出现的格子为准、后面的只补空格', () => {
  const fams = mergeProposals(PROPOSALS)
  assert.equal(fams.length, 1)
  const v3 = fams[0].models.find((m) => m.name === 'V3')
  assert.deepEqual(v3.fields, { 参数: '671B', 激活: '37B' })
  assert.deepEqual(v3.from, ['a.md', 'b.md'])
})

test('没东西可写的型号默认不勾，写入请求里也不带；可疑的格子按出自哪篇分组', () => {
  const fams = mergeProposals(PROPOSALS)
  assert.equal(fams[0].models.find((m) => m.name === 'V2').pick, false)
  assert.equal(pickedCount(fams), 1)
  // b.md 对「参数 670B」的可疑不相干（留下的是 a.md 的 671B）；它对「激活」的可疑要归到 b.md
  assert.deepEqual(suspectsByArticle(fams), { 'a.md': [{ model: 'V3', key: '参数', value: '671B' }],
                                              'b.md': [{ model: 'V3', key: '激活', value: '37B' }] })
  const body = applyBody(fams, PROPOSALS)
  assert.deepEqual(body.families[0].sources, ['[[a]]', '[[b]]'], '出处跟着家族走（两篇都抽到了这个家族）')
  assert.deepEqual(body.families[0].models.map((m) => m.name), ['V3'])
  fams[0].models[0].pick = false
  assert.deepEqual(applyBody(fams, PROPOSALS).families, [], '一个都没勾的家族整个不带')
})

// ---------------------------------------------------------------- 模型谱系排版（2026-09-30）

const m = (id, t, extra = {}) => ({ id, name: id.split('/')[1], t, year: t ? Math.floor(t) : null, tech: [], ...extra })
const MODELS = {
  lineages: [{ id: 'llm', name: '大模型', badge: '参数', fields: [{ key: '参数', numeric: true, unit: '' }] },
             { id: 'cpu', name: 'CPU', badge: '位宽', fields: [{ key: '位宽', numeric: false, unit: '' }] }],
  families: [
    { id: 'Qwen', name: 'Qwen', lineage: 'llm', open: true, models: [m('Qwen/Q2', 2024.4), m('Qwen/Q1', 2023.6), m('Qwen/草稿', null)] },
    { id: 'GPT', name: 'GPT', lineage: 'llm', open: false, models: [m('GPT/G3', 2020.5,
      { values: { 参数: '175B' }, numbers: { 参数: 175e9 } }), m('GPT/G4', 2023.2,
      { tech: [{ text: 'MoE', link: 'MoE', node: 'MoE' }] })] },
    { id: 'Intel', name: 'Intel', lineage: 'cpu', models: [m('Intel/80386', 1985, { values: { 位宽: '32 位' },
      tech: [{ text: 'MoE', link: 'MoE', node: 'MoE' }] })] },
  ],
  links: [{ source: 'Qwen/Q1', target: 'Qwen/Q2', kind: 'next' }, { source: 'GPT/G4', target: 'Qwen/Q2', kind: 'base' }],
}

test('模型谱系：家族按首发时间排，泳道里按发布顺序等距，没日期的排最后不丢', () => {
  const plan = computeLanes(MODELS, { lineage: 'llm' })
  assert.deepEqual(plan.lanes.map((l) => l.family.id), ['GPT', 'Qwen'], '只排这张图里的家族')
  const qwen = plan.items.filter((it) => it.family.id === 'Qwen')
  assert.deepEqual(qwen.map((it) => it.model.name), ['Q1', 'Q2', '草稿'])
  assert.deepEqual(qwen.map((it) => it.x), [LEFT + COL / 2, LEFT + COL * 1.5, LEFT + COL * 2.5])
  assert.deepEqual(qwen.map((it) => it.labelUp), [true, false, true], '名字一上一下交错')
  assert.deepEqual(plan.lanes[1].ticks.map((t) => t.year), [2023, 2024], '跨进新的一年标一个刻度')
  assert.equal(plan.items.find((it) => it.model.name === 'G3').label, 'G3 · 175B', '参数写在名字旁')
})

test('模型谱系：开源筛选、收起家族、按技术点高亮，连线只连两端都在的', () => {
  assert.deepEqual(computeLanes(MODELS, { lineage: 'llm', open: 'open' }).lanes.map((l) => l.family.id), ['Qwen'])
  const folded = computeLanes(MODELS, { lineage: 'llm', hidden: new Set(['GPT']) })
  assert.ok(folded.lanes[0].folded && !folded.items.some((it) => it.family.id === 'GPT'))
  assert.deepEqual(folded.links.map((l) => l.kind), ['next'], '收起的家族那头的线不画')
  const lit = computeLanes(MODELS, { lineage: 'llm', tech: 'node:MoE' })
  assert.deepEqual(lit.items.filter((it) => !it.dim).map((it) => it.model.name), ['G4'])
  assert.deepEqual(techOptions(MODELS, 'llm'), [{ key: 'node:MoE', label: 'MoE', count: 1 }], '只数这张图里的')
  assert.deepEqual(techOptions(MODELS)[0].count, 2)
  assert.equal(humanParams(2.8e12), '2.8T');
  assert.equal(humanNumber(65, 'nm'), '65nm'); assert.equal(humanNumber(1.9e9), '1.9B'); assert.equal(humanNumber(32), '32')
  assert.equal(humanParams(671e9), '671B'); assert.equal(humanParams(null), '')
})

test('谱系图（2026-10-12）：一张图一个一级子目录，名字旁写 谱系.md 的 badge，跨图的线不画', () => {
  const cpu = computeLanes(MODELS, { lineage: 'cpu' })
  assert.deepEqual(cpu.items.map((it) => it.label), ['80386 · 32 位'], '文字 badge 照抄原文')
  assert.equal(badgeOf({ values: {}, numbers: {} }, MODELS.lineages[0]), '', '没写这一格就不写')
  const linked = { ...MODELS, links: [{ source: 'Intel/80386', target: 'GPT/G3', kind: 'base' }] }
  assert.deepEqual(computeLanes(linked, { lineage: 'llm' }).links, [], '另一张图里的那头不在，线不画')
})

test('谱系图叠加（2026-10-12 第 2 步）：两张以上换共用真实年份轴，每张图一行图名，跨图关系连得上', () => {
  const doc = { ...MODELS, links: [{ source: 'Intel/80386', target: 'GPT/G3', kind: 'ref', label: '运行在' }] }
  const one = computeLanes(doc, { lineages: ['llm'] })
  assert.equal(one.timed, false, '只选一张还是等距')
  const plan = computeLanes(doc, { lineages: ['cpu', 'llm'] })
  assert.ok(plan.timed)
  assert.deepEqual(plan.heads.map((h) => h.name), ['CPU', '大模型'])
  assert.ok(plan.heads[1].y > plan.lanes[0].y, '大模型的图名行在 CPU 的泳道下面')
  const x = (name) => plan.items.find((it) => it.model.name === name).x
  assert.equal(x('G3') - x('80386'), (2020.5 - 1985) * YEAR_PX, '横坐标按真实年份差')
  assert.equal(x('G4') - x('G3'), (2023.2 - 2020.5) * YEAR_PX)
  assert.equal(plan.axis[0].year, 1985, '年份轴从最早那一年起')
  assert.deepEqual(plan.links.map((l) => [l.kind, l.label]), [['ref', '运行在']], '跨图的线两头都在才画')
  const crowd = { lineages: MODELS.lineages, links: [], families: [{ id: 'X', name: 'X', lineage: 'llm', models:
    [m('X/a', 2020.1), m('X/b', 2020.1), m('X/c', null)] }, { id: 'Y', name: 'Y', lineage: 'cpu', models: [m('Y/z', 2019)] }] }
  const c = computeLanes(crowd, { lineages: ['llm', 'cpu'] })
  const cx = (name) => c.items.find((it) => it.model.name === name).x
  assert.equal(cx('b') - cx('a'), MIN_GAP, '同一年的两个让开，不叠成一个点')
  assert.equal(cx('c') - cx('b'), COL, '没日期的接在最后')
  const shownLabels = c.items.filter((it) => it.showLabel).map((it) => it.model.name)
  assert.deepEqual(shownLabels, ['a', 'b', 'c', 'z'], '短名字隔 34px 写得下（上下交错）')
  const long = { lineages: MODELS.lineages, links: [], families: [{ id: 'L', name: 'L', lineage: 'llm', models:
    [m('L/DeepSeek-V2-长名字', 2024.3), m('L/DeepSeek-V3-长名字', 2024.9), m('L/DeepSeek-R1-长名字', 2025.0)] }] }
  const lc = computeLanes(long, { lineages: ['llm', 'cpu'] })
  assert.deepEqual(lc.items.map((it) => it.showLabel), [true, true, false], '同一侧撞了的后一个不写，留给悬停')
})


// ---------------------------------------------------------------- 专题画布：框升级成知识点（2026-10-13）

const box = (kind, x, extra = {}) => ({ kind, x, y: 0, w: 80, h: 40, text: '', tip: '', ...extra })
const topicLayout = () => ({ shapes: {
  F: box('frame', 0, { w: 600 }),
  b: box('rect', 300, { parent: 'F', node: '超级块' }),
  a: box('rect', 100, { parent: 'F', node: '引导块' }),
  c: box('rect', 500, { parent: 'F' }),                  // 还没升级：不进部件
}, links: {} })

test('大框里已升级的小框按从左到右排，没升级的不算', () => {
  assert.deepEqual(boundChildren(topicLayout(), 'F'), ['引导块', '超级块'])
})

test('新建的大框：部件按从左到右补，专题字段交给 create_node 不重复写', () => {
  const ch = upgradeChanges({ layout: topicLayout(), index: { nodes: [], edges: [] }, sid: 'F', nodeId: 'FS',
                              topic: '文件系统', isNew: true })
  assert.deepEqual(ch, [{ type: 'add_edge', source: 'FS', relation: '部件', target: '引导块' },
                        { type: 'add_edge', source: 'FS', relation: '部件', target: '超级块' }])
})

test('绑定已有点当大框：补专题字段；已经是部件 / 包含的不重复补', () => {
  const index = { nodes: [{ id: 'FS', topic: '别的' }],
                  edges: [{ source: 'FS', target: '引导块', type: '包含' }] }
  const ch = upgradeChanges({ layout: topicLayout(), index, sid: 'F', nodeId: 'FS', topic: '文件系统' })
  assert.deepEqual(ch, [{ type: 'update_frontmatter', source: 'FS', fields: { 专题: '文件系统' } },
                        { type: 'add_edge', source: 'FS', relation: '部件', target: '超级块' }])
})

test('小框升级时，所在大框已绑定：大框的点补一条「部件:: 它」', () => {
  const layout = topicLayout()
  layout.shapes.F.node = 'FS'
  const ch = upgradeChanges({ layout, index: { nodes: [], edges: [] }, sid: 'c', nodeId: 'inode', topic: '文件系统' })
  assert.deepEqual(ch, [{ type: 'add_edge', source: 'FS', relation: '部件', target: 'inode' }])
})

test('删大框：连同套着的小框、连着这些框的线一起删，连别处的线不动', () => {
  const layout = topicLayout()
  layout.shapes.G = box('rect', 900)
  layout.links = { l1: { source: { kind: 'shape', id: 'a' }, target: { kind: 'node', id: 'x' } },
                   l2: { source: { kind: 'shape', id: 'G' }, target: { kind: 'node', id: 'x' } } }
  const patch = removalPatch(layout, 'F')
  assert.deepEqual(Object.keys(patch.shapes).sort(), ['F', 'a', 'b', 'c'])
  assert.deepEqual(Object.keys(patch.links), ['l1'])
})

test('施工图：绑定框常显学/考方块，没学出来的淡显，没绑定的不染', () => {
  const meta = { id: 'FS', name: '文件系统' }
  // 学过的：方块按双态上色、不淡显
  const on = shapeAttrs({ kind: 'rect', node: 'FS' }, meta, { study: '绿', exam: '黄' })
  assert.equal(on.sqStudy.fill, '#3f9e5d')
  assert.equal(on.sqExam.fill, '#e0891f')
  assert.equal(on.body.fillOpacity, 1)
  // 只有壳：整框淡显 + 虚线
  const stub = shapeAttrs({ kind: 'rect', node: 'FS' }, { ...meta, stub: true }, { study: '红', exam: '灰' })
  assert.ok(stub.body.fillOpacity < 1 && stub.body.strokeDasharray === '4 3')
  assert.equal(stub.sqStudy.fill, '#d9534f')
  // 绑定了但索引里没有（未建）：同样淡显
  assert.ok(shapeAttrs({ kind: 'rect', node: '幽灵' }, null, null).body.fillOpacity < 1)
  // 没绑定的图形：不画方块、不淡显；大框自带的虚线不被覆盖
  const plain = shapeAttrs({ kind: 'frame' }, null, null)
  assert.equal(plain.sqStudy.fill, 'transparent')
  assert.equal(plain.body.fillOpacity, 1)
  assert.ok(!('strokeDasharray' in plain.body), '非淡显不碰 strokeDasharray，大框默认虚线靠注册默认值')
})

test('图形右键：没升级给新建 / 绑定，升级过的大框给查看 / 补部件 / 解除', () => {
  const layout = topicLayout()
  const ids = (m) => m.items.filter((i) => !i.sep).map((i) => i.id)
  assert.ok(ids(buildMenu('shape', 'c', ctx({ layout }))).includes('shape-new-node'))
  layout.shapes.F.node = 'FS'
  const frame = ids(buildMenu('shape', 'F', ctx({ layout })))
  assert.ok(frame.includes('shape-open') && frame.includes('shape-sync') && frame.includes('shape-unbind')
            && !frame.includes('shape-bind'), frame.join(','))
  assert.ok(!ids(buildMenu('shape', 'a', ctx({ layout }))).includes('shape-sync'), '小框没有「补部件」')
})

test('组合框外接框：罩住所有选中图形，四周留内边距', () => {
  const shapes = { a: box('rect', 100, { y: 50, w: 80, h: 40 }), b: box('ellipse', 300, { y: 120, w: 60, h: 60 }) }
  // a: x[100,180] y[50,90]；b: x[300,360] y[120,180] → 外接 [100,360]×[50,180]，pad=10 → [90,70] 大小 280×140
  assert.deepEqual(groupBox(shapes, ['a', 'b'], 10), { x: 90, y: 40, w: 280, h: 150 })
  assert.equal(groupBox(shapes, ['没有的']), null)
})

test('共同父：同属一个父才把组合框挂过去，否则顶层', () => {
  const shapes = { a: { parent: 'F' }, b: { parent: 'F' }, c: { parent: null } }
  assert.equal(commonParent(shapes, ['a', 'b']), 'F')
  assert.equal(commonParent(shapes, ['a', 'c']), null)
})

test('取消组合：删组合框、里面的图形改挂到组合框原来的父上（不删）', () => {
  const layout = { shapes: { G: { kind: 'group', parent: 'F' }, a: { kind: 'rect', parent: 'G' },
                             b: { kind: 'rect', parent: 'G' } } }
  const patch = ungroupPatch(layout, 'G')
  assert.equal(patch.shapes.G, null)                       // 组合框删掉
  assert.deepEqual(patch.shapes.a, { parent: 'F' })        // 孩子回到组合框原来的父
  assert.deepEqual(patch.shapes.b, { parent: 'F' })
})

test('知识海迷雾：按掌握态给整个节点上滤镜（学绿+考绿=点亮发光、考红=心魔变暗、考灰=待点亮淡、幽灵/壳=新边疆没入雾）', () => {
  const node = { id: 'x', name: 'x', weight: 0.6 }   // weight 够高是卡片（kg-card），不是胶囊
  const cls = (opts) => nodeAttrs(node, null, undefined, opts).body.class
  // 不开迷雾：只有底色卡片类，不掺迷雾
  assert.equal(cls({ state: { study: '绿', exam: '绿' } }), 'kg-card')
  // 点亮
  assert.ok(cls({ state: { study: '绿', exam: '绿' }, fog: true }).includes('kg-lit'))
  // 心魔：错过（红）或到期/模糊（黄）
  assert.ok(cls({ state: { study: '绿', exam: '红' }, fog: true }).includes('kg-demon'))
  assert.ok(cls({ state: { study: '绿', exam: '黄' }, fog: true }).includes('kg-demon'))
  // 待点亮：建好没考过（考灰）
  assert.ok(cls({ state: { study: '绿', exam: '灰' }, fog: true }).includes('kg-dim'))
  // 新边疆：幽灵 / 壳
  assert.ok(nodeAttrs(node, null, undefined, { ghost: true, fog: true }).body.class.includes('kg-fog'))
  assert.ok(nodeAttrs({ id: 'x', name: 'x', stub: true, weight: 0.6 }, null, undefined, { fog: true }).body.class.includes('kg-fog'))
  // 点亮不压暗、新边疆压到 0.5
  assert.equal(nodeAttrs(node, null, undefined, { state: { study: '绿', exam: '绿' }, fog: true }).body.opacity, 1)
  assert.equal(nodeAttrs(node, null, undefined, { ghost: true, fog: true }).body.opacity, 0.5)
})

test('框选命中：相交就算选中，框外的不选', () => {
  const shapes = {
    a: box('rect', 100, { y: 100, w: 80, h: 40 }),      // x[100,180] y[100,140]
    b: box('ellipse', 300, { y: 100, w: 60, h: 60 }),   // x[300,360] y[100,160]
    c: box('rect', 1000, { y: 1000, w: 80, h: 40 }),    // 远在天边
  }
  // 框 [90,90]→[370,170]：罩住 a、b，碰不到 c
  assert.deepEqual(shapesInBox(shapes, { x1: 90, y1: 90, x2: 370, y2: 170 }).sort(), ['a', 'b'])
  // 只擦到 a 的一角也算（相交即选）
  assert.deepEqual(shapesInBox(shapes, { x1: 170, y1: 130, x2: 200, y2: 200 }), ['a'])
  // 两角顺序反过来结果一样
  assert.deepEqual(shapesInBox(shapes, { x1: 370, y1: 170, x2: 90, y2: 90 }).sort(), ['a', 'b'])
  assert.deepEqual(shapesInBox(shapes, { x1: 0, y1: 0, x2: 10, y2: 10 }), [])
})

test('组合框右键：给「取消组合」而不是升级 / 改形状；多选右键给「组合」', () => {
  const ids = (m) => m.items.filter((i) => !i.sep).map((i) => i.id)
  const layout = { shapes: { G: { kind: 'group' }, a: { kind: 'rect', parent: 'G' } } }
  const gmenu = ids(buildMenu('shape', 'G', ctx({ layout })))
  assert.ok(gmenu.includes('shape-ungroup') && !gmenu.includes('shape-new-node'), gmenu.join(','))
  // 框选两个普通图形、右键其一：出「组合」
  const sel = ids(buildMenu('shape', 'a', ctx({ layout: topicLayout(), shapeSel: ['a', 'b'] })))
  assert.ok(sel.includes('shape-group'), sel.join(','))
  // 只选一个：不出「组合」
  assert.ok(!ids(buildMenu('shape', 'a', ctx({ layout: topicLayout(), shapeSel: ['a'] }))).includes('shape-group'))
})


test('连线两端：知识点卡片直接用，框用它升级成的点；有一头还只是图形就写不成关系', () => {
  const layout = topicLayout()
  layout.links = { l1: { source: { kind: 'shape', id: 'a' }, target: { kind: 'node', id: '甲' } },
                   l2: { source: { kind: 'shape', id: 'c' }, target: { kind: 'node', id: '甲' } } }
  assert.deepEqual(linkNodes(layout, 'l1'), { from: '引导块', to: '甲' })
  assert.equal(linkNodes(layout, 'l2'), null)
})

test('写进去的关系在索引里的 id：反向写法被归一了也认得出', () => {
  const index = { edges: [{ id: '父->子#包含', source: '父', target: '子', type: '包含', raw_types: ['属于'] }] }
  assert.equal(writtenEdgeId(index, '子', '父', '属于'), '父->子#包含')
  assert.equal(writtenEdgeId(index, '子', '父', '依赖'), null)
})

test('删边按它写在哪个文件里删：「子 属于 父」去子的 md 删「属于」，两边都写了两边都删', () => {
  const index = { nodes: [{ id: '父', path: 'nodes/父.md' }, { id: '子', path: 'nodes/子.md' }] }
  const e = { source: '父', target: '子', type: '包含', raw_types: ['属于'], declared_in: ['nodes/子.md'] }
  assert.deepEqual(removeEdgeChanges(e, index), [{ type: 'remove_edge', source: '子', relation: '属于', target: '父' }])
  const both = { ...e, raw_types: ['包含', '属于'], declared_in: ['nodes/父.md', 'nodes/子.md'] }
  assert.deepEqual(removeEdgeChanges(both, index), [
    { type: 'remove_edge', source: '父', relation: '包含', target: '子' },
    { type: 'remove_edge', source: '子', relation: '属于', target: '父' }])
})


const draftOf = () => ({ center: 'FS', parts: ['引导块', '超级块'],
  up: [{ id: 'MySQL', hop: 1, via: 'FS' }, { id: 'Java', hop: 2, via: 'MySQL' }],
  down: [{ id: '扇区', hop: 1, via: 'FS' }], left: [{ id: 'Minix', hop: 1, via: 'FS' }],
  right: [{ id: 'ext2', hop: 1, via: 'FS' }, { id: 'ext3', hop: 2, via: 'ext2' }], compare: [{ id: 'FAT', hop: 1, via: 'FS' }],
  pending: [{ id: 'Redis', hop: 1, via: '超级块', why: '弱关联' }] })
let seq = 0
const sid = (p) => `${p}${++seq}`

test('一键初稿（空画布）：建中心大框、部件按顺序成方块，上下按跳数往外、左右是演化链', () => {
  const plan = draftPlan({ shapes: {}, nodes: {} }, draftOf(), { x: 0, y: 0 }, sid)
  const shapes = Object.values(plan.shapes)
  const frame = shapes.find((x) => x.kind === 'frame')
  assert.equal(frame.node, 'FS')
  const parts = shapes.filter((x) => x.kind === 'rect').sort((a, b) => a.x - b.x).map((x) => x.node)
  assert.deepEqual(parts, ['引导块', '超级块'])
  const n = plan.nodes
  assert.ok(n.MySQL.y < frame.y && n.Java.y < n.MySQL.y, '上：第 2 跳比第 1 跳更靠上')
  assert.ok(n['扇区'].y > frame.y + frame.h, '下')
  assert.ok(n.Minix.x + 160 < frame.x && n.ext2.x > frame.x + frame.w && n.ext3.x > n.ext2.x, '左前身、右后继一列一列往外')
  assert.ok(n.FAT && !n.Redis, '对照摆上；其他相关只在侧栏，不自动摆')
})

test('一键初稿（已经摆过）：已有中心大框就围着它摆，图上已有的点不动，部件接在已有小框后面、放不下撑宽框', () => {
  const layout = { shapes: { F: { kind: 'frame', x: 0, y: 0, w: 200, h: 170, node: 'FS' },
                             a: { kind: 'rect', x: 15, y: 60, w: 140, h: 70, parent: 'F', node: '引导块' } },
                   nodes: { MySQL: { x: 999, y: 999 } } }
  const plan = draftPlan(layout, draftOf(), { x: 5000, y: 5000 }, sid)
  const added = Object.entries(plan.shapes).filter(([k]) => k !== 'F').map(([, v]) => v)
  assert.deepEqual(added.map((x) => x.node), ['超级块'])
  assert.equal(added[0].x, 165, '接在已有小框后面')
  assert.ok(plan.shapes.F.w > 200 && Object.keys(plan.shapes.F).join() === 'w', '只撑宽，不动框的位置')
  assert.ok(!plan.nodes.MySQL, '已经在图上的不动')
  assert.ok(plan.nodes.Java.y < 0, '围着已有的框摆，不是落在 at')
})

test('侧栏建议：按方位分组，只列还没上图的，部件在最前', () => {
  const layout = { shapes: { a: { kind: 'rect', node: '引导块' } }, nodes: { MySQL: {} } }
  const groups = topicSuggestions(layout, draftOf())
  assert.deepEqual(groups.map((g) => g.key), ['parts', 'up', 'down', 'left', 'right', 'compare', 'pending'])
  assert.deepEqual(groups[0].items.map((x) => x.id), ['超级块'])
  assert.deepEqual(groups[1].items.map((x) => x.id), ['Java'])
})


test('审图角标：只数还没处理的；连线意见挂在起点那个框上', () => {
  const counts = openReviewCounts({ items: [
    { kind: 'fact', target: 'a', status: 'open' }, { kind: 'missing', target: 'F', status: 'open' },
    { kind: 'fact', target: 'a', status: 'adopted' },
    { kind: 'link', status: 'open', fix: { from: { kind: 'shape', id: 'a' }, to: { kind: 'node', id: 'x' } } }] })
  assert.deepEqual(counts, { a: 2, F: 1 })
})

// ---------------------------------------------------------------- 版本演进（A 方案）

const VERS = ['0.12', '2.6', '7.2']

test('版本：没写 since/until 的方块全版本常驻', () => {
  for (const v of VERS) assert.equal(versionStateOf({}, v, VERS), 'present')
})

test('版本：since 之前淡成虚影，正好那一版是新增，之后常驻', () => {
  assert.equal(versionStateOf({ since: '2.6' }, '0.12', VERS), 'ghost')
  assert.equal(versionStateOf({ since: '2.6' }, '2.6', VERS), 'appear')
  assert.equal(versionStateOf({ since: '2.6' }, '7.2', VERS), 'present')
})

test('版本：until 之后淡成虚影（废弃的部件）', () => {
  assert.equal(versionStateOf({ until: '2.6' }, '0.12', VERS), 'present')
  assert.equal(versionStateOf({ until: '2.6' }, '2.6', VERS), 'present')
  assert.equal(versionStateOf({ until: '2.6' }, '7.2', VERS), 'ghost')
})

test('版本：只活在中间一版（since==until），前后都虚影', () => {
  assert.equal(versionStateOf({ since: '2.6', until: '2.6' }, '0.12', VERS), 'ghost')
  assert.equal(versionStateOf({ since: '2.6', until: '2.6' }, '2.6', VERS), 'appear')
  assert.equal(versionStateOf({ since: '2.6', until: '2.6' }, '7.2', VERS), 'ghost')
})

test('版本：没选版本 / 没有版本轴 / 版本不在轴上，一律按常驻（不误伤）', () => {
  assert.equal(versionStateOf({ since: '2.6' }, '', VERS), 'present')
  assert.equal(versionStateOf({ since: '2.6' }, '2.6', []), 'present')
  assert.equal(versionStateOf({ since: '2.6' }, '3.0', VERS), 'present')   // '3.0' 不在轴上
})

test('版本：since/until 打错字（不在轴上）当没写，那一边开口', () => {
  assert.equal(versionStateOf({ since: '0.11' }, '0.12', VERS), 'present') // 下界失效 → 开口
  assert.equal(versionStateOf({ until: '9.9' }, '7.2', VERS), 'present')   // 上界失效 → 开口
})

test('正文分版本：前言 + 各版本小节，标题行摘掉', () => {
  const body = '轮转调度的演进。\n\n## 0.12\n最简单的时间片轮转。\n\n## 2.6\nO(1) 调度器。\n\n## 7.2\nCFS，红黑树。'
  const blocks = splitByVersion(body, VERS)
  assert.deepEqual(blocks.map((b) => b.ver), [null, '0.12', '2.6', '7.2'])
  assert.equal(blocks[0].text, '轮转调度的演进。')
  assert.equal(blocks[1].text, '最简单的时间片轮转。')          // `## 0.12` 标题行不在正文里
  assert.equal(blocks[3].text, 'CFS，红黑树。')
})

test('正文分版本：没有版本小节 → 整篇当一段前言', () => {
  const blocks = splitByVersion('就一段普通正文，没分版本。', VERS)
  assert.deepEqual(blocks, [{ ver: null, text: '就一段普通正文，没分版本。' }])
})

test('正文分版本：一上来就是版本小节 → 不留空前言', () => {
  const blocks = splitByVersion('## 0.12\n甲。\n## 7.2\n乙。', VERS)
  assert.deepEqual(blocks.map((b) => b.ver), ['0.12', '7.2'])
  assert.equal(blocks[0].text, '甲。')
})

test('正文分版本：对不上版本轴的 `##` 留在小节里当普通标题', () => {
  const body = '## 2.6\n正文。\n\n## 细节\n这个二级标题不是版本，照原样留着。'
  const blocks = splitByVersion(body, VERS)
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].ver, '2.6')
  assert.ok(blocks[0].text.includes('## 细节'))   // 非版本标题不切段、标题行保留
})

test('正文分版本：没有版本轴（order 空）→ 不切，整篇前言', () => {
  assert.deepEqual(splitByVersion('## 0.12\n甲。', []), [{ ver: null, text: '## 0.12\n甲。' }])
})

test('正文分版本：空正文 → 空数组', () => {
  assert.deepEqual(splitByVersion('', VERS), [])
  assert.deepEqual(splitByVersion(null, VERS), [])
})

// ---------------------------------------------------------------- 术语按需查

const GLOSS = [
  { term: 'CR3', gloss: '页目录基址寄存器', scope: '硬件', aka: [], see: null },
  { term: '段', gloss: '内存分段', scope: '专题/Linux内存', aka: ['segment'], see: null },
  { term: '段', gloss: 'TCP 报文段', scope: '全局', aka: [], see: null },
  { term: 'GDT', gloss: '全局描述符表', scope: '全局', aka: ['描述符表'], see: '段机制' },
]

test('术语查词：整词相等命中（词条或别名），ASCII 不分大小写', () => {
  assert.deepEqual(findSenses(GLOSS, 'CR3').map((e) => e.term), ['CR3'])
  assert.deepEqual(findSenses(GLOSS, 'cr3').map((e) => e.term), ['CR3'])      // 大小写不敏感
  assert.deepEqual(findSenses(GLOSS, '描述符表').map((e) => e.term), ['GDT'])  // 命中别名
  assert.deepEqual(findSenses(GLOSS, 'segment').map((e) => e.scope), ['专题/Linux内存'])
})

test('术语查词：一词多义全命中，不丢', () => {
  assert.equal(findSenses(GLOSS, '段').length, 2)                              // 两个「段」都在
})

test('术语查词：没命中 / 空选 → 空', () => {
  assert.deepEqual(findSenses(GLOSS, 'CR3 寄存器'), [])                         // 整词不等，不部分匹配
  assert.deepEqual(findSenses(GLOSS, '  '), [])
  assert.deepEqual(findSenses(null, 'x'), [])
})

test('术语查词：多义按语境排序，当前专题在前、全局次之', () => {
  const ordered = resolveSenses(findSenses(GLOSS, '段'), { topic: '专题/Linux内存' })
  assert.deepEqual(ordered.map((e) => e.scope), ['专题/Linux内存', '全局'])
  // 不在那个专题时，全局排前
  const other = resolveSenses(findSenses(GLOSS, '段'), { topic: '专题/调度' })
  assert.equal(other[0].scope, '全局')
})

// ---------------------------------------------------------------- 跑

let failed = 0
for (const [name, fn] of CASES) {
  try {
    fn()
    console.log(`  ✓ ${name}`)
  } catch (err) {
    failed += 1
    console.log(`  ✗ ${name}\n      ${String(err.message).split('\n').join('\n      ')}`)
  }
}
console.log(`\n${CASES.length - failed}/${CASES.length} 通过`)
process.exit(failed ? 1 : 0)
