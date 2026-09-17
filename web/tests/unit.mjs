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
import { timelineOptions } from '../src/canvas/timeline.js'

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
