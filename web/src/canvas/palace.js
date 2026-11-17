// 记忆宫殿（2026-11-17）：全局图的分组固定两层——岛（顶层框）→ 簇（岛里的框），不许再往下套。
//
// 治理靠规矩，不靠自觉：专题套专题之所以越理越乱，就是层级没有上限；全局图乱，是所有东西摊在同一层。
// 两层 + 「每篇只有一个家」（node.group 本来就是单值）是这两头之间的那条线；再细的东西写进笔记正文。
// 层区块（kind: 'zone'）只是领域框里的底色，不是分组，不算层。
// 只管全局图：项目 / 专题 / 对比画布是各自的草稿纸，不受这条约束。后端同一条规矩见 server/layout_store.py。

export const PALACE_DEPTH = 2

const realGroup = (layout, gid) => {
  const g = layout?.groups?.[gid]
  return g && g.kind !== 'zone' ? g : null
}

/** 这个分组在第几层：岛 = 1，簇 = 2。不存在 / 层区块返回 0。 */
export function groupDepth(layout, gid) {
  let depth = 0
  const seen = new Set()
  for (let cur = gid; cur && realGroup(layout, cur) && !seen.has(cur); cur = layout.groups[cur].parent) {
    seen.add(cur)
    depth++
  }
  return depth
}

/** 以它为根往下有几层分组（自己算 1 层）。 */
export function subtreeHeight(layout, gid, seen = new Set()) {
  if (seen.has(gid)) return 1
  seen.add(gid)
  const kids = Object.entries(layout?.groups || {})
    .filter(([, g]) => g.parent === gid && g.kind !== 'zone')
    .map(([id]) => subtreeHeight(layout, id, seen))
  return 1 + Math.max(0, ...kids)
}

/** 把分组 child 放进 parent（null = 放成顶层岛）后，层数还在上限内吗。cap 为 null 表示这块画布不限层。 */
export function canNest(layout, child, parent, cap = PALACE_DEPTH) {
  if (cap == null) return true
  return (parent ? groupDepth(layout, parent) : 0) + subtreeHeight(layout, child) <= cap
}

/** 能不能在 gid 里再开一个子簇。 */
export function canAddSubgroup(layout, gid, cap = PALACE_DEPTH) {
  return cap == null || groupDepth(layout, gid) < cap
}
