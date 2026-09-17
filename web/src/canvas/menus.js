/**
 * 右键菜单的内容：画布上每类元素一套动作。
 *
 * **全是纯函数**：入参是一份画布状态快照，出参是菜单描述（`{ title, subtitle, items }`），
 * 不碰 DOM、不碰响应式、不执行任何动作。动作表（哪个 id 干什么）留在 App.vue——
 * 那是编排，牵着写回、渲染、面板切换十几样东西，搬过来只会把耦合原样搬个地方。
 *
 * 拆出来是为了**能单测**：菜单里"这一项什么时候该出现"是一条条判断
 * （幽灵点不能从它出发连边、父域里没有组内重排、没拐点时清拐点是灰的），
 * 以前只能靠真无头浏览器点开菜单才验得到。
 */
import { GROUP_LAYOUTS, hasSubGroups } from './groupLayout.js'

/**
 * @typedef {object} MenuCtx
 * @property {object|null} index      index.json
 * @property {object|null} layout     当前这份 layout
 * @property {Set<string>} dueIds     今天该复习的
 * @property {string|null} pathFrom   路径搜索的起点
 * @property {boolean} pathHit        眼下有没有高亮着的路径
 * @property {string|null} neighbor   正在"只看邻居"的那个点
 * @property {string|null} focusGroup 聚焦中的域
 * @property {boolean} showMap        小地图开着没
 * @property {(id: string) => string} nodeName
 */

export function nodeMenu(id, ctx) {
  const meta = ctx.index?.nodes.find((n) => n.id === id)
  const place = ctx.layout?.nodes?.[id]
  // 幽灵占位（计划里有、还没建）：能建、能**被**连到，但不能从它出发连边——
  // 关系行要写进源节点的 md，而它连 md 都还没有。
  if (!meta && place?.state === 'ghost') {
    return { title: id, subtitle: '计划里的点，还没建出来',
             items: [{ id: 'build-ghost', label: '现在把它建出来…', icon: 'plus', hint: '写 md' },
                     { id: 'drop-ghost', label: '从这块画布上去掉', icon: 'x',
                       hint: '不动清单' }] }
  }
  const items = [{ id: 'relate', label: '建立关系…', icon: 'link', hint: '⌘L' },
                 { id: 'ref', label: '放引用卡', icon: 'bookmark' }]
  if (place?.state === 'draft') items.push({ id: 'finalize', label: '定稿', icon: 'check' })
  else if (place) items.push({ id: 'draft', label: '标记为草稿（待关联）', icon: 'pencil' })
  if (ctx.dueIds.has(id)) items.push({ id: 'review', label: '复习过了', icon: 'rotate' })
  items.push(
    { sep: true },
    ...(ctx.pathFrom && ctx.pathFrom !== id
      ? [{ id: 'path-to', label: `找「${ctx.nodeName(ctx.pathFrom)}」到这里的路径`, icon: 'timeline' }]
      : [{ id: 'path-from', label: ctx.pathFrom === id ? '取消路径起点' : '以它为路径起点',
           icon: 'timeline', on: ctx.pathFrom === id }]),
    ...(ctx.pathHit ? [{ id: 'path-clear', label: '取消路径高亮', icon: 'x' }] : []),
    { sep: true },
    { id: 'detail', label: '查看详情', icon: 'file' },
    { id: 'neighbor', label: ctx.neighbor === id ? '退出只看邻居' : '只看它的邻居',
      icon: 'eye', on: ctx.neighbor === id },
    { id: 'obsidian', label: '在 Obsidian 打开', icon: 'external' },
    { id: 'copy', label: `复制 [[${meta?.name || id}]]`, icon: 'copy' },
    ...(place?.group ? [{ id: 'as-doc', label: `设为「${ctx.layout.groups[place.group]?.name}」的总览`,
                          icon: 'bookmark', on: ctx.layout.groups[place.group]?.doc === id }] : []),
    { sep: true },
    { id: 'unplace', label: '移出画布（不删 md）', icon: 'trash', danger: true },
  )
  const deg = meta?.degree || 0
  return { title: meta?.name || id, subtitle: `${meta?.field || '未归类'} · ${deg} 条关系`, items }
}

export function groupMenu(gid, folded, ctx) {
  const g = ctx.layout?.groups?.[gid]
  const count = Object.values(ctx.layout?.nodes || {}).filter((n) => n.group === gid).length
  const items = folded
    ? [{ id: 'enter', label: '展开这个域（放大进去）', icon: 'unfold' }]
    : [{ id: 'collapse', label: '折叠成簇卡片', icon: 'fold' },
       { id: 'enter', label: '放大到这个域', icon: 'target' }]
  const doc = g?.doc || null
  items.push(
    { sep: true },
    doc ? { id: 'open-doc', label: `打开总览「${doc}」`, icon: 'file' }
        : { id: 'new-doc', label: '给这个域加总览文档…', icon: 'file', hint: '写 md' },
    ...(doc ? [{ id: 'unbind-doc', label: '解除总览文档绑定', icon: 'x' }] : []),
    { sep: true },
    { id: 'pin-expanded', label: '一直展开（缩小也不折叠）', icon: 'pin', on: g?.pinned === 'expanded' },
    { id: 'pin-auto', label: '恢复自动折叠', icon: 'rotate', disabled: !g?.pinned },
    { sep: true },
    { id: 'new-node', label: '在这里新建知识点…', icon: 'plus', hint: '写 md' },
    { id: 'new-subgroup', label: '在这里新建子簇', icon: 'grid' },
    { id: 'rename', label: '重命名这个域', icon: 'pencil' },
  )
  // 组内重排：每个域自己挑摆法。子域各摆各的，父域里只剩框，重排它没有意义。
  const inner = !folded && count >= 2 && !hasSubGroups(ctx.layout, gid)
  if (inner) {
    items.push({ sep: true })
    for (const [kind, spec] of Object.entries(GROUP_LAYOUTS)) {
      items.push({ id: `inner-${kind}`, label: `组内重排：${spec.label}`, icon: 'grid' })
    }
  }
  if (ctx.focusGroup) items.push({ id: 'exit-focus', label: '返回全景', icon: 'arrowLeft', hint: 'Esc' })
  const pinned = g?.pinned === 'expanded' ? '已钉住展开' : g?.pinned === 'collapsed' ? '已钉住折叠' : '自动折叠'
  return { title: g?.name || gid,
           subtitle: `${count} 个知识点 · ${doc ? '有总览文档' : '没有总览文档'} · ${pinned}`, items }
}

export function edgeMenu(id, ctx) {
  const e = ctx.index?.edges.find((x) => x.id === id)
  return {
    title: e ? `${e.source} → ${e.target}` : id,
    subtitle: e ? `${e.type}（${e.family}族）` : '',
    items: [
      { id: 'edge-delete', label: '删除这条关系（写回 md）', icon: 'trash', danger: true },
      { id: 'edge-clear', label: '清掉手工拐点', icon: 'rotate', disabled: !ctx.layout?.edges?.[id] },
      { sep: true },
      { id: 'edge-source', label: `打开 ${e?.source || ''}`, icon: 'file' },
      { id: 'edge-target', label: `打开 ${e?.target || ''}`, icon: 'file' },
    ],
  }
}

export function decoMenu(cellId) {
  const [kind] = cellId.split(':')
  const label = { note: '便签', img: '贴图', ref: '引用卡' }[kind] || '元素'
  return { title: label, subtitle: '只存在 layout 里，不碰 md',
           items: [...(kind === 'note' ? [{ id: 'deco-edit', label: '编辑便签', icon: 'pencil' }] : []),
                   { id: 'deco-delete', label: `删除这张${label}`, icon: 'trash', danger: true }] }
}

export function blankMenu(ctx) {
  return { title: '画布', subtitle: '右键落点就是新元素的位置', items: [
    { id: 'new-node', label: '新建知识点…', icon: 'plus', hint: '写 md' },
    { id: 'new-group', label: '新建簇（分组框）', icon: 'grid' },
    { id: 'note', label: '贴便签', icon: 'note' },
    { id: 'image', label: '贴图…', icon: 'image' },
    { sep: true },
    { id: 'fit', label: '适应窗口', icon: 'fit', hint: 'F' },
    { id: 'map', label: ctx.showMap ? '隐藏小地图' : '显示小地图', icon: 'map', on: ctx.showMap },
    { id: 'inbox', label: '打开 Inbox', icon: 'inbox', hint: 'I' },
  ] }
}

/** 按右键点到的是什么，挑一个 builder。`cluster` 就是折叠起来的 group。 */
export function buildMenu(kind, id, ctx) {
  if (kind === 'node') return nodeMenu(id, ctx)
  if (kind === 'group' || kind === 'cluster') return groupMenu(id, kind === 'cluster', ctx)
  if (kind === 'edge') return edgeMenu(id, ctx)
  if (kind === 'deco') return decoMenu(id)
  return blankMenu(ctx)
}
