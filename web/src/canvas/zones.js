// 层区块（2026-10-12 归属整理 ⑤-3，后端口径见 tools/knowrary/core/zones.py）。
//
// 领域框里按抽象层划出来的底色区域：`groups` 里 `kind: 'zone'` 的那些。它**不是分组**——
// 节点的 group 指向领域框，属于哪一层只看 md 的 layer；区块只管三件事：
//   1. 画一块带层名的底色（他手摆的二维网格，坐标大小一点不动）；
//   2. 节点取色按它落在哪个区块（一层一个色相，和以前每条道一个色一样）；
//   3. 缩小时领域内跨层的边按区块捆成粗线。
// 不单独折叠 / 钉住 / 钻入、不接收拖放、不出现在时间线选择器和分组菜单里——这些都只认真分组（realGroups）。

export const ZONE = 'zone'

export const isZone = (g) => !!g && g.kind === ZONE

/** 去掉区块的分组表：折叠、祖先链、选择器、菜单都只该看到真分组。 */
export function realGroups(groups = {}) {
  return Object.fromEntries(Object.entries(groups).filter(([, g]) => !isZone(g)))
}

/** 只留真分组的 layout 视图（折叠计算、邻居模式这些按树走的逻辑直接吃它）。 */
export function withoutZones(layout) {
  return layout ? { ...layout, groups: realGroups(layout.groups || {}) } : layout
}

/** 节点中心落在哪个区块里（只看它所属分组下面的区块）。不在任何区块里返回 null。 */
export function zoneOf(layout, nid) {
  const n = layout?.nodes?.[nid]
  if (!n?.group) return null
  const cx = n.x + (n.w || 0) / 2
  const cy = n.y + (n.h || 0) / 2
  for (const [zid, z] of Object.entries(layout.groups || {})) {
    if (!isZone(z) || z.parent !== n.group) continue
    if (z.x <= cx && cx <= z.x + z.w && z.y <= cy && cy <= z.y + z.h) return zid
  }
  return null
}

/** 取色 / 捆边按谁算：落在区块里的按区块，否则按它所属分组。 */
export function bucketOf(layout, nid) {
  return zoneOf(layout, nid) || layout?.nodes?.[nid]?.group || null
}
