// 路径搜索：两个知识点之间"最短的那条解释链"。
//
// 知识图谱里最有价值的问题往往是"这两件事怎么连上的"——GPU 和 Transformer 隔着
// 五六跳，谁也记不住中间是什么。louvain 回答"谁跟谁是一伙的"，路径回答"从这里怎么走到那里"。
// 方向一律忽略：读图的人问的是"有没有关系链"，不是"箭头顺不顺"。
import { findShortestPath } from '@antv/algorithm'

/**
 * 只在**画布上摆着的**节点之间找路：算出一条画不出来的路径没有意义。
 * 返回 { nodes: [id...], edges: [索引里的边...] }，找不到返回 null。
 */
export function shortestPath(index, layout, from, to, families = null) {
  const placed = new Set(Object.keys(layout.nodes || {}))
  const usable = index.nodes.filter((n) => !n.virtual && placed.has(n.id))
  const ids = new Set(usable.map((n) => n.id))
  if (!ids.has(from) || !ids.has(to) || from === to) return null
  const edges = index.edges.filter(
    (e) => ids.has(e.source) && ids.has(e.target) && (!families || families.has(e.family)),
  )
  const data = { nodes: usable.map((n) => ({ id: n.id })),
                 edges: edges.map((e) => ({ source: e.source, target: e.target })) }
  const { length, path } = findShortestPath(data, from, to, false)
  if (!Number.isFinite(length) || !Array.isArray(path) || path.length < 2) return null
  // 算法只还给我们节点序列；每一跳挑一条真实的边，好让画布知道该点亮哪根线
  const steps = []
  for (let i = 0; i < path.length - 1; i++) {
    const [a, b] = [path[i], path[i + 1]]
    const hit = edges.find((e) => (e.source === a && e.target === b) || (e.source === b && e.target === a))
    if (hit) steps.push(hit)
  }
  return { nodes: path, edges: steps }
}

/** 路径读出来是什么样：「A —包含→ B ←演化— C」，箭头指向边在索引里的真实方向。 */
export function describePath(index, path) {
  const name = new Map(index.nodes.map((n) => [n.id, n.name || n.id]))
  const label = (id) => name.get(id) || id
  let text = label(path.nodes[0])
  path.edges.forEach((e, i) => {
    const forward = e.source === path.nodes[i]
    text += forward ? ` —${e.type}→ ` : ` ←${e.type}— `
    text += label(path.nodes[i + 1])
  })
  return text
}
