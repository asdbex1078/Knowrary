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
import { blankMenu, buildMenu, edgeMenu, groupMenu, nodeMenu } from '../src/canvas/menus.js'
import { ancestors, computeCollapsed } from '../src/canvas/lod.js'
import { timelineOptions, bandCurve, buildTimeline, BY_SCHOOL, BY_DOMAIN }
  from '../src/canvas/timeline.js'
import { level, weekColumns } from '../src/panels/heat.js'
import { localISO, todayISO } from '../src/today.js'

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
