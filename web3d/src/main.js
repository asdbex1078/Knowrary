// Knowrary 3D 总览（原型）：只读 /api/index，不碰 layout.json、不参与编辑闭环。
// 目的就是让人肉眼判断"3D 值不值得做"，所以刻意不接编辑链路。
import { ExtensionCategory, Graph, register } from '@antv/g6'
import {
  D3Force3DLayout, Light, Line3D, ObserveCanvas3D, Sphere, ZoomCanvas3D, renderer,
} from '@antv/g6-extension-3d'

register(ExtensionCategory.PLUGIN, '3d-light', Light)
register(ExtensionCategory.NODE, 'sphere', Sphere)
register(ExtensionCategory.EDGE, 'line3d', Line3D)
register(ExtensionCategory.BEHAVIOR, 'observe-canvas-3d', ObserveCanvas3D)
register(ExtensionCategory.BEHAVIOR, 'zoom-canvas-3d', ZoomCanvas3D)
register(ExtensionCategory.LAYOUT, 'd3-force-3d', D3Force3DLayout)

// 与 2D 结构视图同一套色相，方便对照
const FIELD_COLORS = ['#4c8dd6', '#35a58e', '#d2903a', '#9268d8', '#cf6a86', '#3f97b8', '#94a63a']
const FAMILY_COLORS = {
  结构: '#7f9cc0', 依赖: '#8c97a3', 演化: '#e0891f', 对照: '#a077e0', 弱关联: '#5c6773',
}

const el = (id) => document.getElementById(id)

// ---- 自诊断：卡死时要能留下证据 ----
const TRACE = []                 // 最近若干次指针事件
let lastFrame = performance.now()
let frozenFor = 0

function startWatchdog() {
  const tick = () => {
    const now = performance.now()
    const gap = now - lastFrame
    lastFrame = now
    // 页面不可见时浏览器本来就会节流 rAF，别误报；只在可见且卡超过 3 秒才提示
    if (gap > 3000 && !document.hidden) {
      frozenFor = Math.round(gap)
      el('stat').textContent = `⚠️ 画布刚卡了 ${frozenFor}ms —— 点「重建画布」可恢复，按 D 看诊断`
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)

  for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'contextmenu', 'mousedown', 'mouseup']) {
    window.addEventListener(type, (e) => {
      TRACE.push(`${new Date().toISOString().slice(14, 23)} ${type} btn${e.button ?? '-'} ` +
                 `${(e.target?.tagName || '').toLowerCase()}${e.target?.id ? '#' + e.target.id : ''}`)
      if (TRACE.length > 24) TRACE.shift()
    }, { capture: true })
  }
  window.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() === 'd') showDiag()
  })
  window.__diag = () => TRACE.join('\n')
}

function showDiag() {
  const info = el('info')
  info.innerHTML = `<h3>诊断</h3><div class="d">最近一次卡顿：${frozenFor || 0}ms</div>` +
    `<pre style="white-space:pre-wrap;font-size:11px;max-height:320px;overflow:auto">${TRACE.join('\n')}</pre>` +
    `<button onclick="navigator.clipboard.writeText(window.__diag())">复制这些事件</button>`
  info.style.display = 'block'
}

async function boot() {
  const index = await (await fetch('../api/index')).json()
  const real = index.nodes.filter((n) => !n.virtual)
  const fields = [...new Set(real.map((n) => n.field || '(未指定)'))].sort()
  const colorOf = (n) => FIELD_COLORS[fields.indexOf(n.field || '(未指定)') % FIELD_COLORS.length]


  // d3-force 的初始点是二维螺旋撒的（z≈0），靠斥力挤不开 →  整团会是"扁饼"，转起来像平面。
  // 所以先按球面随机撒一遍初始位置，让模拟从三维状态开始收敛。
  const seed = (i, total) => {
    const golden = Math.PI * (3 - Math.sqrt(5))
    const yy = 1 - (i / Math.max(total - 1, 1)) * 2
    const r = Math.sqrt(Math.max(0, 1 - yy * yy))
    const theta = golden * i
    const R = 420
    return { x: Math.cos(theta) * r * R, y: yy * R, z: Math.sin(theta) * r * R }
  }

  const nodes = real.map((n, i) => ({
    id: n.id,
    data: { ...n },
    style: {
      ...seed(i, real.length),
      // 球径按 pageRank 权重（index.json 的 weight）：连得多且连的对象重要，球才大
      size: 10 + 30 * Math.sqrt(n.weight ?? Math.min(1, (n.degree || 0) / 12)),
      fill: n.stub ? '#5c6773' : colorOf(n),
      materialType: 'phong',
      labelText: n.name || n.id,
      labelFill: '#e6edf3',
      labelFontSize: 12,
      labelPlacement: 'bottom',
    },
  }))
  const ids = new Set(nodes.map((n) => n.id))
  const edges = index.edges
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({
      id: e.id, source: e.source, target: e.target, data: { ...e },
      style: { stroke: FAMILY_COLORS[e.family] || '#5c6773', lineWidth: e.family === '演化' ? 2.5 : 1 },
    }))

  el('stat').textContent = `${nodes.length} 个知识点 · ${edges.length} 条关系 · ${fields.length} 个领域`
  el('legend').innerHTML = fields
    .map((f, i) => `<span><i style="background:${FIELD_COLORS[i % FIELD_COLORS.length]}"></i>${f}</span>`)
    .join('')

  const graph = new Graph({
    container: 'container',
    renderer,
    background: '#0d1117',
    data: { nodes, edges },
    // 不给 3D 元素配 state 样式：lineWidth / stroke 是 2D 描边属性，球体是 Mesh 没有描边，
    // 套上去会把球渲染没、并把渲染管线搞坏（表现为"鼠标碰到线就卡死、两端的球消失"）。
    node: { type: 'sphere' },
    edge: { type: 'line3d' },
    layout: {
      type: 'd3-force-3d',
      numDimensions: 3,
      link: { distance: 80, strength: 0.3 },
      manyBody: { strength: -280 },      // 斥力够大，团才会"鼓"起来而不是摊平
      collide: { radius: 24 },
      center: { x: 0, y: 0, z: 0 },
      alphaDecay: 0.02,                  // 收敛慢一点，给 z 方向展开的时间
    },
    // 只留"环绕 + 缩放"：drag-canvas-3d（平移相机）和 observe-canvas-3d（环绕）都绑左键拖动，
    // 同时开会互相打架，相机可能被推到越过焦点的位姿，看起来就是"卡死不能动"。
    // 同理去掉 hover-activate / click-select：它们都靠 state 样式工作。
    // 悬停要看邻居，改成安全的做法——只读数据、不改 3D 元素的样式（见下面的 bindHover）。
    behaviors: ['observe-canvas-3d', 'zoom-canvas-3d'],
    plugins: [
      { type: '3d-light', directional: { direction: [0, 0.5, 1], specular: [0.3, 0.3, 0.3] },
        ambient: { color: '#ffffff', intensity: 0.55 } },
    ],
    animation: false,
  })

  // 自己的回调包一层：回调抛异常会打断 G6 的事件处理，之后整张图不再响应（2D 那边踩过同样的坑）
  const safe = (fn) => (...args) => {
    try {
      fn(...args)
    } catch (err) {
      el('stat').textContent = `交互出错：${err.message}（点「适应视图」回正）`
    }
  }
  guardRightButton(graph)
  startWatchdog()
  bindHover(graph, index)
  graph.on('node:click', safe((e) => showInfo(index, e.target?.id || e.itemId)))
  graph.on('canvas:click', safe(() => { el('info').style.display = 'none' }))
  await graph.render()
  await fitCamera(graph)
  // 力导向收敛要几秒，期间包围盒还在长：稳定后再对两次焦（用户一旦自己操作就不再抢镜头）
  let touched = false
  document.getElementById('container').addEventListener('pointerdown', () => { touched = true }, { once: true })
  for (const delay of [1500, 4000]) {
    setTimeout(() => { if (!touched) fitCamera(graph) }, delay)
  }
  autoSpin(graph)
  // 「适应视图」同时兼任"卡住了点这里"：先重置交互状态，再回正相机
  document.getElementById('fit').onclick = () => {
    try {
      graph.setBehaviors((prev) => [...prev])
    } catch (err) {
      el('stat').textContent = `重置交互失败：${err.message}`
    }
    fitCamera(graph)
  }
  // 最后一招：整个图销毁重建（相当于 2D 那边的「恢复画布」）
  document.getElementById('rebuild').onclick = async () => {
    el('stat').textContent = '重建中…'
    try {
      graph.destroy()
    } catch { /* 已经坏掉的实例，忽略 */ }
    document.getElementById('container').replaceChildren()   // 清掉残留的旧 canvas（否则底部会留一条黑边）
    el('info').style.display = 'none'
    frozenFor = 0
    await boot()
  }
  window.__g6 = graph
}

/**
 * 悬停提示：只读 index 数据，绝不改 3D 元素样式。
 * G6 的 hover-activate 会给元素套 state 样式，而 3D 元素吃不下 2D 的描边属性——
 * 这正是"鼠标碰到线就卡死"的根因，所以这里自己做，且只动 DOM。
 */
function bindHover(graph, index) {
  const tip = el('tip')
  const base = tip.textContent
  const byId = new Map(index.nodes.map((n) => [n.id, n]))
  const byEdge = new Map(index.edges.map((e) => [e.id, e]))
  const show = (text) => { tip.textContent = text || base }
  const onNode = (e) => {
    const node = byId.get(e.target?.id)
    if (node) show(`${node.name || node.id}　度数 ${node.degree}　权重 ${(node.weight * 100).toFixed(0)}%　（点击看详情）`)
  }
  const onEdge = (e) => {
    const edge = byEdge.get(e.target?.id)
    if (edge) show(`${edge.source}　${edge.type} →　${edge.target}　（${edge.family}${edge.year ? ' ' + edge.year : ''}）`)
  }
  // 不同版本的事件名不一致，两种都绑上（只改 DOM 文本，重复触发也无害）
  for (const evt of ['node:pointerenter', 'node:pointerover']) graph.on(evt, onNode)
  for (const evt of ['edge:pointerenter', 'edge:pointerover']) graph.on(evt, onEdge)
  for (const evt of ['node:pointerleave', 'edge:pointerleave', 'canvas:pointerenter']) {
    graph.on(evt, () => show(''))
  }
}

function showInfo(index, id) {
  const node = index.nodes.find((n) => n.id === id)
  if (!node) return
  const byId = new Map(index.edges.map((e) => [e.id, e]))
  const line = (eid, dir) => {
    const e = byId.get(eid)
    if (!e) return ''
    return `<li>${dir === 'out' ? `${e.type} → ${e.target}` : `${e.source} ${e.type} →`}
      <span class="muted">（${e.family}）</span></li>`
  }
  el('info').innerHTML = `<h3>${node.name || node.id}</h3>
    <div class="d">${node.desc || ''}</div>
    <div class="muted">${node.field || ''}${node.year ? ' · ' + node.year : ''} · 度数 ${node.degree}</div>
    ${node.out?.length ? `<ul>${node.out.slice(0, 6).map((x) => line(x, 'out')).join('')}</ul>` : ''}
    ${node.in?.length ? `<ul>${node.in.slice(0, 6).map((x) => line(x, 'in')).join('')}</ul>` : ''}
    <a class="btn" href="../?focus=${encodeURIComponent(node.id)}">在 2D 结构视图里定位 →</a>`
  el('info').style.display = 'block'
}

/**
 * 右键防护。ObserveCanvas3D 绑的是 G6 的 `drag`，**不区分鼠标按键**：
 * 右键按下也会进入拖拽状态，而浏览器弹出的原生右键菜单会吞掉 pointerup，
 * 拖拽状态就永远结束不了 —— 表现就是"右键点几下之后整个图都不能动了"。
 * 对策：容器上禁掉原生右键菜单，并在捕获阶段拦掉右键/中键的指针事件，
 * 让相机行为只认左键。
 */
function guardRightButton(graph) {
  const container = document.getElementById('container')
  container.addEventListener('contextmenu', (e) => e.preventDefault())
  for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup']) {
    container.addEventListener(type, (e) => {
      if (e.button === 0) return
      e.preventDefault()
      e.stopPropagation()
    }, { capture: true })
  }
  // 万一还是卡住（切标签页、拖到窗口外松手），重置行为即可清掉内部拖拽状态
  const reset = () => graph.setBehaviors((prev) => [...prev])
  window.addEventListener('blur', reset)
  document.addEventListener('visibilitychange', () => document.hidden && reset())
  window.__resetBehaviors = reset
}

/** 把相机拉到能看全整团的距离：3D 没有 zoomToFit，得按包围盒自己算。 */
async function fitCamera(graph) {
  const data = graph.getNodeData()
  const pts = data.map((n) => [n.style?.x ?? 0, n.style?.y ?? 0, n.style?.z ?? 0])
  if (!pts.length) return
  const axis = (i) => pts.map((p) => p[i])
  const min = [0, 1, 2].map((i) => Math.min(...axis(i)))
  const max = [0, 1, 2].map((i) => Math.max(...axis(i)))
  const center = [0, 1, 2].map((i) => (min[i] + max[i]) / 2)
  const span = Math.max(...[0, 1, 2].map((i) => max[i] - min[i]), 200)
  const camera = graph.getCanvas().getCamera()
  const dist = span * 2.2      // 留足余量：力导向收敛期间团还在长
  camera.setPosition(center[0], center[1], center[2] + dist)
  camera.setFocalPoint(center[0], center[1], center[2])
  if (camera.setNear) camera.setNear(0.1)
  if (camera.setFar) camera.setFar(dist * 8)
}

/** 缓慢自转：3D 的形态感主要靠运动，静止的球团反而不如 2D 好认。 */
function autoSpin(graph) {
  let spinning = false      // 默认不转：想看形态时自己点「开始自转」
  let raf = 0
  const camera = graph.getCanvas?.()?.getCamera?.()
  const tick = () => {
    if (spinning && camera?.rotate) camera.rotate(0.15, 0, 0)
    raf = requestAnimationFrame(tick)
  }
  tick()
  // 一旦用户自己开始拖动就停自转：两者同时改相机很容易把视角转飞
  document.getElementById('container').addEventListener('pointerdown', () => {
    if (!spinning) return
    spinning = false
    el('spin').textContent = '开始自转'
  })
  el('spin').textContent = '开始自转'
  el('spin').onclick = () => {
    spinning = !spinning
    el('spin').textContent = spinning ? '停止自转' : '开始自转'
  }
  window.addEventListener('beforeunload', () => cancelAnimationFrame(raf))
}

// 兜底：任何未捕获错误都显示出来，并把"重置视角"露出来——3D 里最常见的"卡死"
// 其实是视角转飞了（相机越过焦点、朝向外侧），一键回正即可。
window.addEventListener('error', (e) => {
  el('stat').textContent = `出错了：${e.message}（点「适应视图」可回正视角）`
})
window.addEventListener('unhandledrejection', (e) => {
  el('stat').textContent = `出错了：${e.reason}（点「适应视图」可回正视角）`
})

boot().catch((err) => {
  el('stat').textContent = `加载失败：${err.message}`
  console.error(err)
})
