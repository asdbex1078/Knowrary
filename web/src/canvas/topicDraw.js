// 专题画布上的画图交互（2026-10-13）：加图形、改字、连线、嵌进大框、删。
//
// 单独成文件是因为 App.vue 已经四千行；这里只认一份 layout 和几个注入进来的动作，
// 不认识模式、项目、对话。所有改动走 layout 补丁（和拖动同一条路），撤销栈照常记。
import { computed, ref } from 'vue'
import { isDrawnShape, isLink, linkEndOf, linksOfNode, newShape, removalPatch, shapeCellId,
         withDescendants } from './drawing.js'

const PREVIEW_ID = 'link:__preview'
const uid = (prefix) => `${prefix}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`

/**
 * ctx：
 *   layoutDoc   当前画布那份 layout（ref）
 *   graph       X6 实例（ref）
 *   queue(bucket, id, patch)  记一笔补丁（null = 删）；调用方负责撤销起点和节流写盘
 *   render()    重画（保持视口）
 *   center()    画面中心的图坐标
 *   meta(id)    索引里的知识点（升级过的框要显示它的名字）
 *   say(text, kind)  提示条
 */
export function createTopicDrawing(ctx) {
  const selShape = ref(null)
  const selLink = ref(null)
  const linking = ref(null)          // null = 不在连线；{ from: 端点 }（右键「从这里连线」开始）
  let pointer = null                  // 连线时鼠标在图上的位置：预览线的终点

  const shape = computed(() => {
    const s = ctx.layoutDoc.value?.shapes?.[selShape.value]
    if (!s) return null
    return { sid: selShape.value, ...s, nodeName: s.node ? ctx.meta(s.node)?.name || '' : '' }
  })
  const link = computed(() => {
    const l = ctx.layoutDoc.value?.links?.[selLink.value]
    return l ? { lid: selLink.value, ...l } : null
  })

  /** 本地 layout 先改（画面立刻对），补丁排进写盘队列。 */
  function put(bucket, id, patch) {
    const doc = ctx.layoutDoc.value
    const store = { ...(doc[bucket] || {}) }
    if (patch === null) delete store[id]
    else store[id] = { ...(store[id] || {}), ...patch }
    ctx.queue(bucket, id, patch)
    ctx.layoutDoc.value = { ...ctx.layoutDoc.value, [bucket]: store }
  }

  function select(kind, id) {
    selShape.value = kind === 'shape' ? id : null
    selLink.value = kind === 'link' ? id : null
  }

  /** 放一个图形：`at` 是它的中心（拖进来松手的点 / 右键落点；不给就放画面中间）。落在大框里就挂到那个大框下面。 */
  function addAt(kind, at = null) {
    const spot = at || ctx.center()
    const sid = uid('s')
    put('shapes', sid, { ...newShape(kind, spot), parent: frameAt(spot) })
    select('shape', sid)
    ctx.render()
    return sid
  }

  /** 落点所在的最里层大框（没有就 null）。 */
  function frameAt(pt) {
    const shapes = ctx.layoutDoc.value.shapes || {}
    let hit = null
    for (const [sid, s] of Object.entries(shapes)) {
      if (s.kind !== 'frame' || pt.x < s.x || pt.x > s.x + s.w || pt.y < s.y || pt.y > s.y + s.h) continue
      if (!hit || s.w * s.h < shapes[hit].w * shapes[hit].h) hit = sid
    }
    return hit
  }

  /** 把一个已有知识点放上来：`at` 是卡片中心。 */
  function addNodeAt(id, at = null) {
    if (ctx.layoutDoc.value.nodes?.[id]) {
      ctx.say(`「${ctx.meta(id)?.name || id}」已经在这张图上了`)
      return
    }
    const spot = at || ctx.center()
    put('nodes', id, { x: Math.round(spot.x - 80), y: Math.round(spot.y - 30) })
    ctx.render()
  }

  function patchShape(sid, patch) {
    put('shapes', sid, patch)
    ctx.render()
  }

  function deleteShape(sid) {
    applyRemoval(removalPatch(ctx.layoutDoc.value, sid))
    select(null)
    ctx.render()
  }

  function applyRemoval(patch) {
    for (const [bucket, items] of Object.entries(patch)) for (const id of Object.keys(items)) put(bucket, id, null)
  }

  function patchLink(lid, patch) {
    put('links', lid, patch)
    ctx.render()
  }

  function deleteLink(lid) {
    put('links', lid, null)
    select(null)
    ctx.render()
  }

  /** 照审图意见加一条线（意见里两端已经核过在画布上）。 */
  function addLink(source, target, text = '') {
    const lid = uid('l')
    put('links', lid, { source, target, text })
    select('link', lid)
    ctx.render()
    return lid
  }

  /** 一键初稿：一批新图形和卡片当补丁写进来（同一批改动，⌘Z 一步撤回）。 */
  function applyPlan(plan) {
    for (const [sid, shape] of Object.entries(plan.shapes || {})) put('shapes', sid, shape)
    for (const [nid, box] of Object.entries(plan.nodes || {})) put('nodes', nid, box)
    ctx.render()
  }

  /** 图形升级成 / 绑定到知识点；`null` = 解除绑定（只改画布，md 不动）。 */
  function bind(sid, nodeId) {
    put('shapes', sid, { node: nodeId })
    ctx.render()
  }

  /** 从画布上拿掉一个知识点之前调：连着它的线一起拿掉，不然服务端拒（悬空的线）。 */
  function dropNodeLinks(nid) {
    for (const lid of linksOfNode(ctx.layoutDoc.value, nid)) put('links', lid, null)
  }

  /** 右键「从这里连线」：从这个端点拉出一条跟着鼠标的虚线，点到终点就连上。 */
  function startLink(end) {
    linking.value = { from: end }
    pointer = null
    ctx.say('点终点（框或知识点）连上；Esc 取消')
    ctx.render()
  }

  /** 连线时鼠标挪到哪，预览线就画到哪（直接改那条临时边，不重画整张图）。 */
  function trackPointer(pt) {
    if (!linking.value) return
    pointer = pt
    ctx.graph.value?.getCellById(PREVIEW_ID)?.setTarget(pt)
  }

  /** 预览线：画在 drawCells 的结果后面。终点先放在起点上，鼠标一动就跟过去。 */
  function preview() {
    const from = linking.value?.from
    if (!from) return null
    const source = from.kind === 'shape' ? shapeCellId(from.id) : from.id
    const anchor = ctx.graph.value?.getCellById(source)?.getBBox()?.center
    return { id: PREVIEW_ID, source, target: pointer || anchor || { x: 0, y: 0 }, zIndex: 30,
             // 预览线不能接鼠标：终点就在鼠标底下，接了的话点终点会点到它自己身上
             attrs: { line: { stroke: '#d8a838', strokeWidth: 1.6, strokeDasharray: '6 4', pointerEvents: 'none',
                              targetMarker: { name: 'block', width: 8, height: 6 } },
                      wrap: { pointerEvents: 'none' } },
             data: { kind: 'link-preview' } }
  }

  /** 连线中的一次点击。返回 true 表示这次点击被连线吃掉了。 */
  function linkClick(cell) {
    const end = linkEndOf(cell)
    if (!end) { ctx.say('只能连框或知识点'); return true }
    const from = linking.value.from
    if (from.kind === end.kind && from.id === end.id) return true
    const lid = uid('l')
    put('links', lid, { source: from, target: end, text: '' })
    linking.value = null
    pointer = null
    select('link', lid)
    ctx.say('连上了。线上的字在右边改', 'success')
    ctx.render()
    return true
  }

  function onNodeClick(cell) {
    if (linking.value) return linkClick(cell)
    if (isDrawnShape(cell)) { select('shape', cell.getData().sid); ctx.render(); return true }
    select(null)
    return false
  }

  function onEdgeClick(edge) {
    if (!isLink(edge)) return false
    select('link', edge.getData().lid)
    ctx.render()
    return true
  }

  /** 拖完：X6 带着小框一起挪了，小框的坐标也要一起记。 */
  function onMoved(cell) {
    const shapes = ctx.layoutDoc.value.shapes || {}
    for (const sid of withDescendants(shapes, cell.getData().sid)) {
      const c = ctx.graph.value.getCellById(shapeCellId(sid))
      if (!c) continue
      const p = c.position()
      put('shapes', sid, { x: Math.round(p.x), y: Math.round(p.y) })
    }
  }

  function onParent(cell, current) {
    const host = current ? ctx.graph.value.getCellById(current)?.getData()?.sid || null : null
    const p = cell.position()
    put('shapes', cell.getData().sid, { parent: host, x: Math.round(p.x), y: Math.round(p.y) })
  }

  function onResized(cell) {
    const { width, height } = cell.size()
    const p = cell.position()
    put('shapes', cell.getData().sid, { x: Math.round(p.x), y: Math.round(p.y), w: Math.round(width), h: Math.round(height) })
  }

  /** Delete / Backspace：删选中的图形或连线。返回 true 表示处理了。 */
  function onDelete() {
    if (selShape.value) { deleteShape(selShape.value); return true }
    if (selLink.value) { deleteLink(selLink.value); return true }
    return false
  }

  function onEscape() {
    if (!linking.value && !selShape.value && !selLink.value) return false
    linking.value = null
    select(null)
    ctx.render()
    return true
  }

  function reset() {
    linking.value = null
    select(null)
  }

  return { shape, link, linking, selShape, selLink, bind, applyPlan, addLink, uid, addAt, addNodeAt, patchShape, deleteShape, patchLink, deleteLink,
           dropNodeLinks, startLink, trackPointer, preview, select, onNodeClick, onEdgeClick, onMoved, onParent,
           onResized, onDelete, onEscape, reset }
}
