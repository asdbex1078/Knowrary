// X6 形状与配色：纯 SVG（不用框架组件，几千节点下组件实例会成为瓶颈），
// 但"纯 SVG"不等于"素"——配色、字号层级、圆角、留白决定了它像线框图还是像手画的图。
import { Graph } from '@antv/x6'

// 分组配色：柔和的分类色板，每个分组一套（描边 / 填充 / 标题）。
// 取值参考手绘图的习惯：低饱和描边 + 极浅填充，白底上不刺眼，缩小后仍分得清。
export const PALETTE = [
  { key: 'blue',   line: '#3f74b5', fill: '#f2f7fd', head: '#eaf1fb', text: '#28527f' },
  { key: 'teal',   line: '#2f8d7b', fill: '#f0faf7', head: '#e4f5f0', text: '#1f6659' },
  { key: 'amber',  line: '#c07a26', fill: '#fdf7ee', head: '#faf0df', text: '#8a5615' },
  { key: 'violet', line: '#7f5bc4', fill: '#f7f4fd', head: '#efeafb', text: '#5b3d95' },
  { key: 'rose',   line: '#bb5570', fill: '#fdf3f5', head: '#fae9ee', text: '#8c3a51' },
  { key: 'cyan',   line: '#2b7f9e', fill: '#f0f9fc', head: '#e2f2f8', text: '#1d5e77' },
  { key: 'olive',  line: '#7c8a2c', fill: '#f8faee', head: '#f1f5de', text: '#5a661c' },
  { key: 'clay',   line: '#a45f3e', fill: '#fdf5f1', head: '#f8eae2', text: '#7b432a' },
  { key: 'indigo', line: '#5560b8', fill: '#f4f5fd', head: '#e9ebfa', text: '#3a4291' },
  { key: 'moss',   line: '#4e8c5a', fill: '#f2faf4', head: '#e6f4ea', text: '#356140' },
]
export const NEUTRAL = { key: 'gray', line: '#94a3b1', fill: '#fafbfc', head: '#f1f3f6', text: '#55636f' }

// 深色主题用同一批色相，把填充压暗、描边提亮，保证在深底上仍分得清簇
export const PALETTE_DARK = PALETTE.map((c) => ({
  key: c.key, line: c.line, fill: mixDark(c.line, 0.16), head: mixDark(c.line, 0.26), text: lighten(c.line, 0.45),
}))
export const NEUTRAL_DARK = { key: 'gray', line: '#6b7683', fill: '#1e242b', head: '#252c34', text: '#9fb0bf' }

export const THEME = {
  light: { bg: '#fafbfc', grid: '#e8ebef', title: '#1c2b3a', groupFill: '#ffffff', groupStroke: '#d8e0e8',
           pill: '#ffffff', edgeLabel: '#7a8794', labelBg: '#ffffff' },
  dark: { bg: '#14181d', grid: '#222931', title: '#e7eef5', groupFill: '#181d23', groupStroke: '#2b333c',
          pill: '#1e242b', edgeLabel: '#95a3b1', labelBg: '#1a1f26' },
}

let theme = 'light'
export const setTheme = (name) => { theme = name === 'dark' ? 'dark' : 'light' }
export const currentTheme = () => theme
export const tokens = () => THEME[theme]

function hex(c) {
  const n = parseInt(c.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
function toHex([r, g, b]) {
  return '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')
}
function mixDark(color, ratio) {
  const [r, g, b] = hex(color)
  const base = hex('#14181d')
  return toHex([base[0] + (r - base[0]) * ratio, base[1] + (g - base[1]) * ratio, base[2] + (b - base[2]) * ratio])
}
function lighten(color, ratio) {
  const [r, g, b] = hex(color)
  return toHex([r + (255 - r) * ratio, g + (255 - g) * ratio, b + (255 - b) * ratio])
}

/** 分组 → 配色：按分组 id 稳定取色，同一分组每次都是同一个颜色。 */
export function paletteFor(groupId, allGroupIds) {
  const palette = theme === 'dark' ? PALETTE_DARK : PALETTE
  const neutral = theme === 'dark' ? NEUTRAL_DARK : NEUTRAL
  if (!groupId) return neutral
  const i = allGroupIds.indexOf(groupId)
  return i < 0 ? neutral : palette[i % palette.length]
}

// 节点尺寸分三档：度数越高的知识点越大，一眼能看出骨干与末梢
// 三种形态而不是三种大小的矩形：骨干=带色条的卡片，中间=圆角卡片，末梢=胶囊。
// 档位按 pageRank 权重（index.json 里的 weight，0~1）而不是度数——度数只数"连了几条"，
// pageRank 还看"连的对象重不重要"，更贴近"哪些是骨干"。实测 72 节点里 >=0.5 的 9 个、>=0.3 的 35 个。
export const SIZES = [
  { min: 0.5, w: 196, h: 64, rx: 14, title: 14.5, desc: 11, chars: 40, accent: 5, shape: 'card' },
  { min: 0.3, w: 176, h: 52, rx: 12, title: 13, desc: 10.5, chars: 22, accent: 0, shape: 'card' },
  { min: 0, w: 148, h: 38, rx: 19, title: 12.5, desc: 0, chars: 0, accent: 0, shape: 'pill' },
]
export const NODE_W = SIZES[1].w
export const NODE_H = SIZES[1].h

const ICONS = [
  [/Agent|任务|事件|恢复|检查点|幂等/, '🤖'],
  [/编译|解释|字节码|IR|JIT|VM|GCC|Clang|LLVM/, '⚙️'],
  [/CPU|寄存器|内存|总线|硬盘|缓存|硬件|IO|ROM|RAM|EPROM|控制器|运算器/, '🔧'],
  [/指令|ISA|RISC|CISC|微架构|汇编/, '🧩'],
  [/栈|堆|段|内存布局|栈帧|程序/, '🧱'],
  [/理论|逻辑|定理|数论|图灵|冯诺依曼|架构|电学/, '📐'],
  [/语言|C语言|JVM语言/, '💬'],
]

/** 节点图标：按名称 / 类型 / 标签匹配，一个知识点最多一个符号，认领域用。 */
export function iconFor(node) {
  const hay = [node?.name, node?.id, node?.type, ...(node?.tags || [])].filter(Boolean).join(' ')
  return ICONS.find(([re]) => re.test(hay))?.[1] || ''
}

/** 按 pageRank 权重定档；老索引没有 weight 时退回按度数估算。 */
export function sizeFor(node) {
  const weight = typeof node === 'number'
    ? Math.min(1, node / 12)
    : (node?.weight ?? Math.min(1, (node?.degree || 0) / 12))
  return SIZES.find((s) => weight >= s.min) || SIZES[SIZES.length - 1]
}

// 五个关系族各一种线型（设计文档 3.6）。结构族靠嵌套表达，默认不画线，可在工具条勾开。
export const FAMILY_STYLE = {
  结构: { stroke: '#93aec9', strokeWidth: 1.4, targetMarker: 'block', strokeDasharray: null, curved: true },
  依赖: { stroke: '#9aa7b4', strokeWidth: 1.2, targetMarker: 'block', strokeDasharray: null },
  演化: { stroke: '#e0891f', strokeWidth: 2.2, targetMarker: 'block', strokeDasharray: null },
  对照: { stroke: '#9169cf', strokeWidth: 1.2, targetMarker: null, strokeDasharray: '6 4' },
  弱关联: { stroke: '#c3cbd4', strokeWidth: 1, targetMarker: null, strokeDasharray: '2 4' },
}

export const FAMILIES = Object.keys(FAMILY_STYLE)

export const CURSOR_ID = '__time-cursor__'   // 时间游标那个 cell 的固定 id
export const CURSOR_W = 2.5
export const CURSOR_COLOR = '#e0891f'        // 和「演化」同一支橙：两者说的都是"时间往前走"
// 借来的外部邻居专用色。**不能用金色**：金色（#d9a53b）是草稿，而项目画布上草稿最多，
// 两种金虚线并排等于没标记。橙给了演化与到期，灰给了幽灵与弱关联——靛蓝是这张画布上唯一还空着的语义位。
export const BORROWED_COLOR = '#5a7fd6'

export function registerShapes() {
  Graph.registerNode('kg-node', {
    inherit: 'rect',
    width: NODE_W,
    height: NODE_H,
    markup: [
      { tagName: 'rect', selector: 'body' },
      { tagName: 'rect', selector: 'accent' },
      { tagName: 'text', selector: 'title' },
      { tagName: 'text', selector: 'desc' },
      { tagName: 'circle', selector: 'due' },
      { tagName: 'rect', selector: 'sqStudy' },
      { tagName: 'rect', selector: 'sqExam' },
    ],
    attrs: {
      body: { rx: 12, ry: 12, fill: '#fff', stroke: '#c6d0da', strokeWidth: 1.2, class: 'kg-card' },
      accent: { x: 0, y: 0, width: 0, refHeight: '100%', rx: 3, ry: 3, fill: 'transparent' },
      title: { refX: 13, refY: 20, fontSize: 13, fontWeight: 600, fill: '#1f2933', textAnchor: 'start',
               textWrap: { width: -26, ellipsis: true } },
      desc: { refX: 13, refY: 38, fontSize: 10.5, fill: '#8593a1', textAnchor: 'start',
              textWrap: { width: -26, ellipsis: true } },
      // 到期复习的小圆点：默认透明，due 时才点亮（不占布局，缩小后仍看得见）
      due: { r: 4.5, refX: '100%', refX2: -11, refY: 11, fill: 'transparent', stroke: 'none' },
      // 学 / 考双态：左下角两个小方块。默认透明，缩小到只剩标题时不画（性能守则）
      sqStudy: { x: 12, refY: '100%', refY2: -11, width: 7, height: 7, rx: 2, ry: 2, fill: 'transparent' },
      sqExam: { x: 22, refY: '100%', refY2: -11, width: 7, height: 7, rx: 2, ry: 2, fill: 'transparent' },
    },
  }, true)

  // 便签：画布上的自由文字，不属于任何知识点，只存在 layout.json 里
  Graph.registerNode('kg-note', {
    inherit: 'rect',
    width: 190, height: 74,
    markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'text', selector: 'text' }],
    attrs: {
      body: { rx: 6, ry: 6, fill: '#fff7d6', stroke: '#e3d08a', strokeWidth: 1, class: 'kg-card' },
      text: { refX: 12, refY: 16, fontSize: 12, fill: '#6b5a1e', textAnchor: 'start',
              textWrap: { width: -24, ellipsis: true } },
    },
  }, true)

  // 引用卡：同一个知识点在别处再出现一次（虚线边框表示"这是引用，不是本体"）
  Graph.registerNode('kg-ref', {
    inherit: 'rect',
    width: 170, height: 46,
    markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'text', selector: 'label' },
             { tagName: 'text', selector: 'tag' }],
    attrs: {
      body: { rx: 10, ry: 10, fill: 'transparent', stroke: '#9aa7b4', strokeWidth: 1.2,
              strokeDasharray: '5 4' },
      label: { refX: 12, refY: 18, fontSize: 12.5, fontWeight: 600, textAnchor: 'start',
               textWrap: { width: -24, ellipsis: true } },
      tag: { refX: 12, refY: 34, fontSize: 10.5, text: '引用 · 点击跳到本体', opacity: 0.7, textAnchor: 'start' },
    },
  }, true)

  // 图片：白板上贴的一张图（架构图、手绘稿）。文件在 vault 的 assets/ 里，位置存 layout.images
  Graph.registerNode('kg-image', {
    inherit: 'rect',
    width: 320, height: 200,
    markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'image', selector: 'img' },
             { tagName: 'text', selector: 'caption' }],
    attrs: {
      body: { rx: 8, ry: 8, fill: 'transparent', stroke: '#c6d0da', strokeWidth: 1, class: 'kg-card' },
      img: { refWidth: '100%', refHeight: '100%', preserveAspectRatio: 'xMidYMid meet' },
      caption: { refX: 8, refY2: -8, fontSize: 10.5, textAnchor: 'start', opacity: 0.75,
                 textWrap: { width: -16, ellipsis: true } },
    },
  }, true)

  // 历史视图的泳道：一条横贯全图的浅色带子，左上角写分组名
  Graph.registerNode('kg-lane', {
    inherit: 'rect',
    markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'text', selector: 'label' }],
    attrs: {
      body: { rx: 10, ry: 10, fill: '#f6f8fa', stroke: '#e3e8ee', strokeWidth: 1 },
      label: { refX: 14, refY: 18, fontSize: 12.5, fontWeight: 600, textAnchor: 'start' },
    },
  }, true)

  // 历史视图里的节点：一枚**圆点**，圆心精确钉在年份刻度上。
  //
  // 为什么不是卡片：卡片 196px 宽，而一年通常只有三四十像素——一张卡片横跨好几个年份，
  // 看不出它到底是哪一年的（实测 1971 的 EPROM 和 1972 的 C语言 在图上左右错开半张卡片，
  // 谁也说不清谁先谁后）。圆点没有宽度歧义：圆心落在哪一年就是哪一年。
  // 圆里只放**名字的第一个字**（缩写会变成 KVC 这种没人认得的东西），
  // 全名画在旁边；挤到放不下时藏起来，靠悬停和播放时的点亮显示。
  Graph.registerNode('kg-dot', {
    inherit: 'rect',
    width: DOT, height: DOT,
    markup: [
      { tagName: 'circle', selector: 'body' },
      { tagName: 'text', selector: 'initial' },
      { tagName: 'text', selector: 'name' },
      { tagName: 'title', selector: 'tip' },      // 原生 tooltip，兜底用
    ],
    attrs: {
      body: { r: DOT / 2, refCx: '50%', refCy: '50%', fill: '#fff', stroke: '#c6d0da',
              strokeWidth: 1.6, class: 'kg-card' },
      initial: { refX: '50%', refY: '50%', textAnchor: 'middle', textVerticalAnchor: 'middle',
                 fontSize: 13, fontWeight: 700 },
      name: { refX: DOT + 6, refY: '50%', textAnchor: 'start', textVerticalAnchor: 'middle',
              fontSize: 12, class: 'kg-dot-name' },
      tip: { text: '' },
    },
  }, true)

  // 历史视图的年份刻度：一根竖线加年份标签
  // 流派时间带：一条横跨 start_year～end_year 的浅色条，**背后压着一条累计走势**。
  //
  // 为什么带子和曲线画在同一个形状里：它们讲的是同一个流派的两件事——
  // 带子是"声明的跨度"，曲线是"实际积累到哪一步"。**两者不一致的地方就是欠账**
  // （带子很长而曲线一直平 = 这段时期一个点都没记），拆成两个 cell 就对不齐了。
  //
  // 曲线是 polyline 不是 path：累计值只在成员出现那一年跳，中间必须是水平的。
  // 画成平滑曲线会让人以为那几年在稳步增长，而事实是那几年什么都没发生——
  // 这条线存在的全部意义就是让"什么都没发生"看得见。
  Graph.registerNode('kg-band', {
    inherit: 'rect',
    markup: [{ tagName: 'rect', selector: 'body' },
             { tagName: 'polyline', selector: 'curve' },
             { tagName: 'text', selector: 'label' }],
    attrs: {
      body: { rx: 4, ry: 4, strokeWidth: 1 },
      curve: { fill: 'none', strokeWidth: 1.5, strokeLinejoin: 'miter', pointerEvents: 'none' },
      label: { refX: 8, refY: 11, fontSize: 11.5, fontWeight: 600, textAnchor: 'start' },
    },
  }, true)

  Graph.registerNode('kg-tick', {
    inherit: 'rect',
    width: 1, height: 40,
    markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'text', selector: 'label' }],
    attrs: {
      // stroke 必须显式关掉：inherit: 'rect' 会带上 X6 基础 rect 的 stroke: #000，
      // 1px 宽的方块被 1px 黑描边整个糊满——本该是浅灰参考线的刻度一直画成了黑线，
      // 二十几条黑竖线压过泳道和卡片，整张历史图的噪声大半来自这里。
      body: { width: 1, refHeight: '100%', fill: '#dfe4ea', stroke: 'none' },
      label: { refX: 0, refY: -8, fontSize: 12, fontWeight: 600, textAnchor: 'middle' },
    },
  }, true)

  // 历史视图的时间游标：一根贯穿全图的竖线，顶上挂一枚年份药丸。
  // 回放时只挪它 + 给节点加减一个 class，全程不重建 cell——这是"不闪不跳"的前提。
  Graph.registerNode('kg-cursor', {
    inherit: 'rect',
    width: CURSOR_W, height: 40,
    markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'rect', selector: 'pill' },
             { tagName: 'text', selector: 'label' }],
    attrs: {
      // 同样要显式 stroke: none，否则继承来的黑描边会把橙色游标画成黑线（见 kg-tick 的注释）
      body: { width: CURSOR_W, refHeight: '100%', rx: 1, ry: 1, fill: CURSOR_COLOR, stroke: 'none' },
      pill: { x: -31, y: -28, width: 64, height: 23, rx: 11.5, ry: 11.5, fill: CURSOR_COLOR,
              stroke: 'none' },
      label: { refX: CURSOR_W / 2, y: -12, fontSize: 13, fontWeight: 700, fill: '#fff',
               textAnchor: 'middle' },
    },
  }, true)

  Graph.registerNode('kg-cluster', {
    inherit: 'rect',
    width: CLUSTER_W,
    height: CLUSTER_H,
    markup: [
      { tagName: 'rect', selector: 'body' },
      { tagName: 'rect', selector: 'accent' },
      { tagName: 'text', selector: 'title' },
      { tagName: 'text', selector: 'count' },
      { tagName: 'text', selector: 'list' },
      { tagName: 'text', selector: 'hint' },
    ],
    attrs: {
      body: { rx: 16, ry: 16, fill: '#fff', stroke: '#c6d0da', strokeWidth: 1.6, class: 'kg-card' },
      accent: { x: 0, y: 0, width: 6, refHeight: '100%', rx: 3, ry: 3, fill: 'transparent' },
      title: { refX: 18, refY: 26, fontSize: 16, fontWeight: 700, textAnchor: 'start' },
      count: { refX: 18, refY: 48, fontSize: 11.5, textAnchor: 'start' },
      list: { refX: 18, refY: 72, fontSize: 11.5, textAnchor: 'start', textWrap: { width: -36, ellipsis: true } },
      hint: { refX: 18, refY: CLUSTER_H - 16, fontSize: 10.5, textAnchor: 'start' },
    },
  }, true)

  Graph.registerNode('kg-group', {
    inherit: 'rect',
    markup: [
      { tagName: 'rect', selector: 'body' },
      { tagName: 'rect', selector: 'head' },
      { tagName: 'text', selector: 'label' },
      { tagName: 'text', selector: 'doc' },     // 绑了总览文档时在标题条右端标一下
    ],
    attrs: {
      body: { rx: 16, ry: 16, fill: '#fbfcfd', stroke: '#d8e0e8', strokeWidth: 1.2, class: 'kg-group-box' },
      head: { rx: 14, ry: 14, refWidth: '100%', height: 34, fill: '#eef2f6' },
      label: { refX: 16, refY: 22, fontSize: 13, fontWeight: 600, fill: '#4a5b6d', textAnchor: 'start' },
      doc: { refX: '100%', refX2: -14, refY: 22, fontSize: 11.5, textAnchor: 'end', fill: '#4a5b6d', text: '' },
    },
  }, true)
}

// 分组标题条的上限 = 布局给标题留的内边距（core.layout.PAD_TOP），超了会被子分组压住
export const HEAD_MAX = 44
export const DOT = 28            // 时间轴上一个知识点的直径
export const CLUSTER_W = 260
export const CLUSTER_H = 128

/** 折叠后的簇卡片：分组名 + 节点数 + 度数最高的几个节点，缩小时一眼看清这块是什么。 */
const truncate = (text, max) => (text.length > max ? `${text.slice(0, max)}…` : text)

/**
 * 簇卡片**不越出它替代的那个分组框**。
 *
 * 早先为了"缩小时也读得清"，把卡片按 1/zoom 放大到 3 倍——于是 248×128 的泳道
 * 画出 780×384 的卡片，相邻的簇互相盖、连线全埋在卡片底下（zIndex 12 > 边的 5），
 * 看上去就是"卡片挡住连线、卡片和卡片压在一起"。
 * 现在反过来：**尺寸认框，字号认缩放**（clusterAttrs 里按 1/zoom 放大字，再按卡片
 * 尺寸夹一层，装不下的行直接不画）。缩小时看不清细节是对的——那时只需要知道
 * "这里有一簇"，要读内容就放大。
 */
export function clusterBox(group) {
  return { w: Math.min(group.w || CLUSTER_W, 1100), h: Math.min(group.h || CLUSTER_H, 480) }
}


export function clusterAttrs(name, summary, color = NEUTRAL, box = { w: CLUSTER_W, h: CLUSTER_H },
                             doc = null, zoom = 1) {
  // 字跟着缩放放大（缩小时才读得出来），但必须装得进卡片：宽了会溢出到隔壁，
  // 高了会把下面几行挤出框外——所以三个上限取最小的那个。
  const k = Math.min(3, Math.max(1, 1 / Math.max(zoom, 0.05)))
  const title = Math.max(9, Math.min(17 * k, box.h * 0.3, box.w / 5.2))
  const small = title * 0.72
  const fits = (need) => box.h >= need
  const chars = (size) => Math.max(4, Math.floor((box.w - title * 1.6) / (size * 1.05)))
  const showCount = fits(title * 3.4)
  const showList = fits(title * 5.2)
  const showHint = fits(title * 6.4)
  return {
    body: { fill: color.fill, stroke: color.line, strokeWidth: Math.max(1.2, title / 11),
            rx: 16, ry: 16, class: 'kg-card' },
    accent: { width: Math.max(4, title / 3), height: box.h, rx: 3, ry: 3, fill: color.line },
    title: { text: truncate(name, chars(title)), fill: color.text, fontSize: title, fontWeight: 700,
             refX: title * 0.9, refY: title * 1.5 },
    count: { text: showCount ? `${summary.count} 个知识点${doc ? ' · 📄 有总览' : ''}` : '',
             fill: color.text, fontSize: small, refX: title * 0.9, refY: title * 2.8, opacity: 0.85 },
    list: { text: showList ? truncate(summary.top.join(' · '), chars(small)) : '',
            fill: tokens().title, fontSize: small, refX: title * 0.9, refY: title * 4.2, opacity: 0.75 },
    hint: { text: showHint ? '点开展开这一簇' : '', fill: color.text, fontSize: small * 0.92,
            refX: title * 0.9, refY: box.h - title * 0.8, opacity: 0.5 },
  }
}

/** 节点视觉：按所属分组配色，按度数定档，draft 虚线、stub 灰调。 */
/** 图片元素的属性：图源走服务的 /api/asset/，标题就是文件名。 */
export function imageAttrs(image) {
  return {
    img: { 'xlink:href': `/api/asset/${encodeURIComponent(image.file)}` },
    body: { stroke: tokens().groupStroke },
    caption: { text: image.file, fill: tokens().edgeLabel },
  }
}

export function laneAttrs(name) {
  const t = tokens()
  return { body: { fill: t.groupFill, stroke: t.groupStroke }, label: { text: name, fill: t.edgeLabel } }
}

/** 一条流派带的样式。`points` 是已经算好的阶梯折线（相对带子左上角）。 */
export function bandAttrs(band, points) {
  const base = band.color || (theme === 'dark' ? NEUTRAL_DARK.line : NEUTRAL.line)
  return {
    // 带子本身压得很淡：它是背景，圆点和连线才是主角
    body: { fill: withAlpha(base, 0.13), stroke: withAlpha(base, 0.45) },
    // 曲线用同一个色但不透明——**重叠时两条带子叠在一起，只有曲线还分得出谁是谁**
    curve: { stroke: base, points: points.map(([x, y]) => `${x},${y}`).join(' ') },
    label: { text: bandLabel(band), fill: base },
  }
}

/** 带子上的标签：名字 + 年份区间。`open` 用「–」收尾，表示还在延续而不是数据缺了。 */
export function bandLabel(band) {
  const span = band.open || typeof band.end !== 'number'
    ? `${band.start}–` : `${band.start}–${band.end}`
  return `${band.name}  ${span}`
}

/** #rrggbb → rgba()。带子要半透明，而 X6 的 fill 不吃独立的 opacity。 */
function withAlpha(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

export function tickAttrs(year) {
  const t = tokens()
  return { body: { fill: t.grid }, label: { text: String(year), fill: t.edgeLabel } }
}

/** 跨代激活（GPU 1999 → 深度学习 2012）：金色虚线 + 流动动画，一眼看出"点燃"关系。 */
export function activationAttrs() {
  return {
    line: { stroke: '#d8a838', strokeWidth: 2.4, strokeDasharray: '8 5', class: 'kg-flow',
            targetMarker: { name: 'block', width: 9, height: 7 } },
  }
}

// 双态的四个颜色。和面板上的 .sq.s-* 是同一套语义，只是这里画在 SVG 上。
const STATE_FILL = { 灰: 'transparent', 红: '#d9534f', 黄: '#e0891f', 绿: '#3f9e5d' }

/** 名字的第一个字：中文取首字，英文取首字母（大写）。缩写会变成没人认得的 KVC。 */
export function initialOf(name = '') {
  const t = String(name).trim()
  if (!t) return '?'
  const first = [...t][0]
  return /[a-z]/.test(first) ? first.toUpperCase() : first
}

/** 时间轴上的圆点：圆 + 首字 + 旁边的全名（挤的时候由 showName 关掉）。 */
export function dotAttrs(meta, color = NEUTRAL, { showName = true, year = null } = {}) {
  const name = meta?.name || meta?.id || ''
  return {
    body: { fill: color.fill, stroke: color.line, strokeWidth: 1.8 },
    initial: { text: initialOf(name), fill: color.text },
    // 文本**永远画出来**，只把 opacity 压成 0：这样悬停和回放点亮时，
    // CSS 一句 opacity:1 就能让它现形（presentation 属性打不过 CSS）。
    // 真删掉文本的话，DOM 里没东西可现。
    name: { text: name, fill: tokens().title, opacity: showName ? 1 : 0 },
    tip: { text: year ? `${name}（${year}）` : name },
  }
}

/**
 * 知识点卡片的样式。
 *
 * `opts`：`due` 到期、`state` 掌握度、`ghost` 幽灵占位、`borrowed` 借来的外部邻居。
 * 摊成位置参数的话这里就是六七个 flag，调用处全是一串 `false, null, true`。
 */
export function nodeAttrs(indexNode, layoutNode, color = NEUTRAL, opts = {}) {
  const { due = false, state = null, ghost = false, borrowed = false } = opts
  const draft = layoutNode?.state === 'draft'
  // 幽灵：计划里有、图里还没建。比 stub 更淡——stub 是"建了个壳"，幽灵是"压根还没有"。
  // 只活在项目画布上，不进全局 layout、不进 vault。
  // **由调用方判定**：这里为了显示标题会收到一个合成的 indexNode，光看它有没有是判不出来的。
  //
  // 借来的：图里真有、只是不属于这个项目（GPU 前面的 CPU）。它跟幽灵**反过来**——
  // 幽灵是"还没有的东西"，借来的是"已经有、但不归你管"，所以照常上色、只是整张卡调淡并画虚线框，
  // 一眼能认出"这个不是我项目里的点"，又不至于淡到读不出它是什么。
  const stub = !!indexNode?.stub
  const size = sizeFor(indexNode)
  const tone = stub ? NEUTRAL : color
  return {
    accent: { width: size.accent, height: size.h, fill: size.accent ? tone.line : 'transparent' },
    body: {
      rx: size.rx, ry: size.rx,
      class: size.shape === 'pill' ? 'kg-pill' : 'kg-card',
      fill: ghost ? 'transparent' : draft ? '#fffdf4' : size.shape === 'pill' ? tokens().pill : tone.fill,
      stroke: ghost ? '#c3cbd4' : borrowed ? BORROWED_COLOR : draft ? '#d9a53b' : tone.line,
      strokeWidth: ghost || stub ? 1 : size.shape === 'pill' ? 1.1 : 1.3,
      strokeDasharray: ghost ? '2 5' : borrowed ? '4 3' : draft ? '5 3' : stub ? '3 3' : null,
      strokeOpacity: ghost ? 0.7 : borrowed ? 0.55 : 1,
      fillOpacity: borrowed ? 0.4 : 1,
    },
    title: {
      opacity: ghost ? 0.55 : borrowed ? 0.7 : 1,
      text: ((borrowed ? '↗ ' : '')
        + ((size.shape === 'pill' ? '' : `${iconFor(indexNode)} `).trimStart()
          ? `${iconFor(indexNode)} ${indexNode?.name || indexNode?.id || ''}`.trim()
          : indexNode?.name || indexNode?.id || '')),
      fontSize: size.title,
      fill: stub ? tone.text : tokens().title,
      refX: size.accent ? 13 + size.accent : 13,
      refY: size.desc ? 20 : size.h / 2,
      textVerticalAnchor: 'middle',
      textWrap: { width: size.accent ? -32 : -26, ellipsis: true },
    },
    desc: {
      // 按字数截断而不是靠 textWrap 限高：行数确定，永远不会溢出卡片
      text: size.chars ? (indexNode?.desc || '').slice(0, size.chars) : '',
      fontSize: size.desc || 1,
      fill: tone.text,
      opacity: size.desc ? (borrowed ? 0.5 : 0.75) : 0,
      refX: size.accent ? 13 + size.accent : 13,
      refY: size.h - 16,
      textVerticalAnchor: 'middle',
      textWrap: { width: size.accent ? -32 : -26, ellipsis: true },
    },
    due: { fill: due ? '#e0891f' : 'transparent', r: size.shape === 'pill' ? 3.5 : 4.5 },
    // 只在卡片形态（有 desc 的尺寸）上画：缩小成一行标题时这两个点只会变成噪声
    sqStudy: { fill: size.desc && state ? (STATE_FILL[state.study] || 'transparent') : 'transparent' },
    sqExam: { fill: size.desc && state ? (STATE_FILL[state.exam] || 'transparent') : 'transparent' },
  }
}

export function groupAttrs(name, color = NEUTRAL, doc = null, zoom = 1, box = null) {
  // 标题条跟着缩放放大：13px 的字缩到 30% 只剩 4px，整块框就成了没名字的方块。
  // **但条子不能超过布局给标题预留的那 44px**（core.layout.PAD_TOP）——
  // 撑过头的话，里面的子分组会直接压在标题上把它盖掉（试出来的：条子 75px 时标题被削一半）。
  const k = Math.min(2.4, Math.max(1, 1 / Math.max(zoom, 0.05)))
  const head = Math.min(Math.max(34, 13 * k * 2.2), HEAD_MAX)
  const size = Math.min(13 * k, head * 0.52, Math.max(11, (box?.h || 200) / 3.2))
  return {
    body: { fill: tokens().groupFill, stroke: color.line, strokeWidth: 1.1, strokeOpacity: 0.4, rx: 16, ry: 16,
            class: 'kg-group-box' },
    head: { fill: color.head, rx: 16, ry: 16, height: head, refWidth: '100%' },
    // refY 是**基线**不是中心：按一半放会把字顶到标题条外面去（框边一圆角就削掉半个字）
    label: { text: name, fill: color.text, fontSize: size, fontWeight: 600,
             refX: 16, refY: head * 0.5 + size * 0.36 },
    doc: { text: doc ? '📄 总览' : '', fill: color.text, opacity: 0.75, fontSize: size * 0.9,
           refY: head * 0.5 + size * 0.32 },
  }
}

// 跨分组边聚合成一条「分组 A → 分组 B (n)」：中性灰，粗细随条数增长，点它展开明细
export function aggregateAttrs(count) {
  return {
    line: {
      stroke: '#aeb9c5',
      strokeWidth: Math.min(5, 1.2 + count * 0.35),
      strokeOpacity: 0.4,          // 跨组束是"有联系"的提示，不该抢主干的视觉；选中节点时会高亮
      targetMarker: { name: 'block', width: 8, height: 6 },
    },
  }
}

export function aggregateLabel(count) {
  return {
    attrs: {
      text: { text: String(count), fontSize: 11, fill: tokens().edgeLabel, fontWeight: 600 },
      rect: { fill: tokens().labelBg, stroke: tokens().groupStroke, rx: 8, ry: 8, refWidth: '140%', refHeight: '130%' },
    },
  }
}

export function noteAttrs(note, color = NEUTRAL) {
  return {
    body: { fill: note.color || (currentTheme() === 'dark' ? '#3a3419' : '#fff7d6'),
            stroke: currentTheme() === 'dark' ? '#5e5427' : '#e3d08a' },
    text: { text: note.text || '（空便签，双击编辑）',
            fill: currentTheme() === 'dark' ? '#e8dca9' : '#6b5a1e' },
  }
}

export function refAttrs(name, color = NEUTRAL) {
  return {
    body: { stroke: color.line, strokeDasharray: '5 4', fill: 'transparent' },
    label: { text: name, fill: tokens().title },
    tag: { fill: color.text },
  }
}

export function edgeAttrs(family) {
  const s = FAMILY_STYLE[family] || FAMILY_STYLE['弱关联']
  return {
    line: {
      stroke: s.stroke,
      strokeWidth: s.strokeWidth,
      strokeDasharray: s.strokeDasharray,
      targetMarker: s.targetMarker ? { name: 'block', width: 8, height: 6 } : null,
    },
  }
}
