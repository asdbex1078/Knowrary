/**
 * 路径搜索：两个知识点之间最短的那条解释链。
 *
 * 右键一个点选「以它为路径起点」，再右键另一个选「找到这里的路径」，中间那几跳高亮出来。
 * 回答的是"这两件事是怎么扯上关系的"——图大了之后，两个点之间往往隔着三四跳，
 * 光看线团根本追不出来。
 *
 * **只在当前可见的关系族里走**：把「弱关联」关掉之后再搜，走的就是硬关系。
 * 搜不通时明说是"在当前可见的关系族里"走不通，而不是笼统一句"没有路径"——
 * 那会让人以为图里真的没连，跑去重连一条已经有的边。
 */
import { ref, shallowRef } from 'vue'
import { clearPath, highlightPath } from '../canvas/render.js'
import { describePath, shortestPath } from '../canvas/paths.js'

/**
 * @param deps graph / indexDoc / layoutDoc / aggregate / expanded —— 响应式引用
 *             visibleFamilies() —— 当前勾着哪几个关系族
 *             setBanner —— 提示
 */
export function usePathSearch(deps) {
  const { graph, indexDoc, layoutDoc, aggregate, expanded, visibleFamilies, setBanner } = deps

  const pathFrom = ref(null)        // 起点（右键选定）
  const pathHit = shallowRef(null)  // 找到的路径 { nodes, edges }，纯展示，不落盘

  const nodeName = (id) => indexDoc.value?.nodes.find((n) => n.id === id)?.name || id

  /** 索引里的一条边，在画布上对应哪个 cell（跨组时是那一束聚合边）。 */
  function edgeCellId(e) {
    const layout = layoutDoc.value
    const a = layout.nodes[e.source]?.group || null
    const b = layout.nodes[e.target]?.group || null
    const pair = a && b ? `${a}->${b}` : null
    return !aggregate.value || !pair || a === b || expanded.value.has(pair) ? e.id : `agg:${pair}`
  }

  /** 重画之后 cell 是新的，高亮跟着没了——所以每次 render 完都要再点一遍。 */
  function applyPath() {
    const hit = pathHit.value
    if (!hit) return
    highlightPath(graph.value, new Set(hit.nodes), new Set(hit.edges.map(edgeCellId)))
  }

  function clearPathHighlight(quiet = false) {
    pathFrom.value = null
    if (!pathHit.value) return
    pathHit.value = null
    clearPath(graph.value)
    if (!quiet) setBanner('已取消路径高亮')
  }

  function startPath(id) {
    if (pathFrom.value === id) { clearPathHighlight(true); setBanner('已取消路径起点'); return }
    clearPathHighlight(true)
    pathFrom.value = id
    setBanner(`路径起点：「${nodeName(id)}」——右键另一个知识点选「找到这里的路径」`, 'success')
  }

  function endPath(id) {
    const from = pathFrom.value
    if (!from || from === id) return
    const hit = shortestPath(indexDoc.value, layoutDoc.value, from, id, visibleFamilies())
    if (!hit) {
      setBanner(`「${nodeName(from)}」和「${nodeName(id)}」之间在当前可见的关系族里走不通`, 'error')
      return
    }
    pathHit.value = hit
    pathFrom.value = null
    applyPath()
    setBanner(`${hit.edges.length} 跳：${describePath(indexDoc.value, hit)}`, 'success')
  }

  return { pathFrom, pathHit, nodeName, edgeCellId, applyPath, clearPathHighlight, startPath, endPath }
}
