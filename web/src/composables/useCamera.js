/**
 * 镜头：视口读写、缩放、平滑飞行、小地图取景框、域工具条定位。
 *
 * 放在一起是因为它们共用**同一把写盘闸**（`applyingViewport`）和同一条"视口变了"的
 * 通路：程序化挪镜头（贴合、飞行、导览）不该把视口写回 layout.json，只有人自己拖和缩才该。
 * 拆开放两处，迟早出现"切个关系族就白涨一个 revision"那种脏改动。
 *
 * **不含 `bindEvents`**：那个是 X6 事件到各个动作的接线板，牵着写回、渲染、面板、
 * 项目、对话二十几样东西。把它搬进 composable 只会让入参对象变成 App.vue 的第二份
 * surface——耦合没减少，只是换了个地方写。接线板就该待在编排层。
 */
import { computed, ref, shallowRef } from 'vue'
import {
  LABEL_ZOOM, applyEdgeLabels, currentViewport,
} from '../canvas/render.js'
import { computeCollapsed } from '../canvas/lod.js'

export function useCamera(deps) {
  const {
    graph, indexDoc, layoutDoc, patcher, mode, collapsedIds, autoLod, focusGroup,
    ready, applyingViewport, writable, render,
  } = deps

  const zoom = ref(1)                    // 给右下角缩放条读数用，随 scale 事件更新
  const labelsOn = ref(false)
  const viewBox = ref({ cx: 0, cy: 0, w: 0, h: 0 })   // 当前视口（图坐标），小地图画那个白框
  const activeGroup = ref(null)          // 工具条正指着哪个域
  const groupBarAt = shallowRef(null)    // 工具条的屏幕坐标，跟着缩放平移走
  let lastBucket = 0                     // 簇卡片的尺寸档，跨档才重画
  let flyGen = 0

  const activeGroupBox = computed(() => (activeGroup.value
    ? layoutDoc.value?.groups?.[activeGroup.value] || null : null))
  const activeGroupCount = computed(() => (activeGroup.value
    ? Object.values(layoutDoc.value?.nodes || {}).filter((n) => n.group === activeGroup.value).length : 0))
  const activeFolded = computed(() => !!activeGroup.value && collapsedIds.value.has(activeGroup.value))

  function saveViewport() {
    if (!ready.value || applyingViewport.value || !writable()) return
    patcher.value.queueViewport(currentViewport(graph.value))
  }

  function setActiveGroup(gid) {
    activeGroup.value = layoutDoc.value?.groups?.[gid] ? gid : null
    placeGroupBar()
  }

  /**
   * 把工具条摆到域的上沿。
   *
   * 按"域与可视区的交集"算而不是只看左上角：放大之后域往往比屏幕还大，左上角早就在
   * 视口外，但你明明正看着它——那时也得给工具条。整块都挪出屏幕了才收起来。
   */
  function placeGroupBar() {
    const g = graph.value
    const box = activeGroupBox.value
    if (!g || !box) { groupBarAt.value = null; return }
    const cell = g.getCellById(activeGroup.value)
    const at = cell ? cell.position() : box        // 折叠时簇卡片的位置才是它现在的样子
    const size = cell ? cell.size() : { width: box.w, height: box.h }
    const tl = g.localToClient(at.x, at.y)
    const br = g.localToClient(at.x + size.width, at.y + size.height)
    const view = g.container.getBoundingClientRect()
    const x0 = Math.max(tl.x, view.left + 8)
    const x1 = Math.min(br.x, view.right - 8)
    const y0 = Math.max(tl.y, view.top + 52)       // 至少给工具条自己留出一条的高度
    const y1 = Math.min(br.y, view.bottom - 8)
    groupBarAt.value = x1 > x0 && y1 > y0 ? { x: x0, y: y0 - 44 } : null
  }

  /** 当前视口换算成图坐标，小地图靠它画那个白框。 */
  function syncView() {
    const g = graph.value
    if (!g) return
    const z = g.zoom() || 1
    const el = g.container
    const c = currentViewport(g)
    viewBox.value = { cx: c.cx, cy: c.cy, w: (el.clientWidth || 1200) / z, h: (el.clientHeight || 800) / z }
    placeGroupBar()
  }

  function onZoom() {
    const g = graph.value
    zoom.value = g.zoom()
    saveViewport()
    syncView()
    // 历史视图没有 LOD 折叠：缩放就只是看大看小，走结构视图那套会每滚一格就整图重建，
    // 还会把结构视图的折叠集合改掉（切回去时折叠状态就错了）
    if (mode.value === 'history') return
    const next = computeCollapsed(layoutDoc.value, g.zoom(),
      { auto: autoLod.value, focus: focusGroup.value })
    const changed = next.size !== collapsedIds.value.size
      || [...next].some((id) => !collapsedIds.value.has(id))
    // 折叠集合变了要重画；有簇卡片时缩放跨档（卡片尺寸分档跟随缩放）也要重画
    const bucket = Math.round(Math.min(3, Math.max(1, 1 / Math.max(g.zoom(), 0.05))) * 2)
    if (changed || (next.size && bucket !== lastBucket)) {
      lastBucket = bucket
      render()
      return
    }
    const shouldShow = g.zoom() > LABEL_ZOOM
    if (shouldShow !== labelsOn.value) {
      labelsOn.value = shouldShow
      applyEdgeLabels(g, indexDoc.value, shouldShow)
    }
  }

  /** 右下角缩放条：按固定倍率缩放，落点夹在 X6 的上下限内。 */
  function stepZoom(factor) {
    const g = graph.value
    if (!g) return
    ready.value = true
    g.zoomTo(Math.min(3, Math.max(0.05, g.zoom() * factor)))
  }

  function resetZoom() {
    ready.value = true
    graph.value?.zoomTo(1)
  }

  /** 小地图上点一下 / 拖一把：视口中心跟着走。 */
  function jumpTo({ x, y }) {
    ready.value = true
    graph.value?.centerPoint(x, y)
    syncView()
  }

  /** 一批节点的外接框（图坐标）。飞行取景和"两端一起框进来"都用它。 */
  function boxOf(ids, pad = 120) {
    const boxes = ids.map((id) => layoutDoc.value?.nodes?.[id]).filter(Boolean)
    if (!boxes.length) return null
    const x0 = Math.min(...boxes.map((b) => b.x)) - pad
    const y0 = Math.min(...boxes.map((b) => b.y)) - pad
    const x1 = Math.max(...boxes.map((b) => b.x + (b.w || 160))) + pad
    const y1 = Math.max(...boxes.map((b) => b.y + (b.h || 60))) + pad
    return { x: x0, y: y0, w: Math.max(x1 - x0, 1), h: Math.max(y1 - y0, 1) }
  }

  /** 连完线自动飞过去，两端一起框进视口。 */
  function flyToPair(a, b) {
    const box = boxOf([a, b], 140)
    if (!box) return
    const el = graph.value.container
    const z = Math.min(1.2, (el.clientWidth || 1200) / box.w, (el.clientHeight || 800) / box.h)
    flyTo({ cx: box.x + box.w / 2, cy: box.y + box.h / 2, zoom: z })
  }

  function cancelFly() { flyGen += 1 }

  /**
   * 平滑飞过去。用 setTimeout 而不是 requestAnimationFrame：
   * 后台标签页和无头浏览器里 rAF 不触发，动画会卡在半路，视口再也存不回去（阶段 2 踩过）。
   *
   * 可打断：导览一站站走时，新的一站要能立刻接管镜头；用户自己拖画布时更要马上松手，
   * 否则下一帧又把他拽回去——两个人抢方向盘比不动还糟。
   */
  function flyTo({ cx, cy, zoom: to }, ms = 420) {
    const g = graph.value
    const gen = ++flyGen
    const from = currentViewport(g)
    const end = Math.max(0.05, Math.min(3, to))
    const t0 = Date.now()
    const step = () => {
      if (gen !== flyGen) return                  // 已经被下一次 flyTo / cancelFly 接管
      const p = Math.min(1, (Date.now() - t0) / ms)
      const e = 1 - (1 - p) ** 3                  // easeOutCubic
      g.zoomTo(from.zoom + (end - from.zoom) * e)
      g.centerPoint(from.cx + (cx - from.cx) * e, from.cy + (cy - from.cy) * e)
      if (p < 1) { setTimeout(step, 16); return }
      zoom.value = g.zoom()
      syncView()
      saveViewport()
    }
    step()
  }

  return {
    zoom, labelsOn, viewBox, activeGroup, groupBarAt,
    activeGroupBox, activeGroupCount, activeFolded,
    saveViewport, setActiveGroup, placeGroupBar, syncView, onZoom,
    stepZoom, resetZoom, jumpTo, boxOf, flyToPair, flyTo, cancelFly,
  }
}
