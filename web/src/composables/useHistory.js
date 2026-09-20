/**
 * 历史视图 / 谱系树 / 年份回放 / 沿演化链导览。
 *
 * 这四件事共用同一条**时间游标**（`hist.upto`），所以必须待在一起：导览每走到一站
 * 就把游标挪到那年，回放直接推游标，两边的镜头、点亮和讲解才永远对得上。拆开放
 * 两个地方，迟早出现"导览走到了 2017、滑块还停在 1997"这种事。
 *
 * 和结构视图的分工：结构视图的坐标是人摆的、要落盘；**这里的坐标是算出来的**
 * （X 锁死年份、Y 按泳道），进来一次算一次，不进 layout.json。
 */
import { computed, reactive, ref } from 'vue'
import {
  buildHistoryCells, buildLineageCells, highlightEdges, markStop, mount, paintHistoryTime,
} from '../canvas/render.js'
import { timelineOptions } from '../canvas/timeline.js'
import { fetchSchools } from '../api.js'

export const STEP_MS = 760   // 回放每站停多久。跳的是"有事发生的年份"，不是日历年，所以可以停久一点
export const TOUR_MS = 3200  // 导览每站停多久：够读完一句 desc
export const TOUR_ZOOM = 0.85

/**
 * @param deps 画布那一侧的东西，全部由 App.vue 传进来：
 *   graph / indexDoc / layoutDoc / zoom  —— 响应式引用
 *   applyingViewport                     —— 程序化设视口期间的写盘闸（和结构视图共用同一把）
 *   setBanner / flyTo / cancelFly        —— 提示与镜头
 */
export function useHistory(deps) {
  const { graph, indexDoc, layoutDoc, zoom, applyingViewport, setBanner, flyTo, cancelFly } = deps

  const hist = reactive({ compact: false, validity: false, upto: null, trunk: false,
                          演化: true, 依赖: false, 对照: false })
  const histPlan = ref(null)
  const linPlan = ref(null)          // 谱系树算出来的那份
  const timelines = ref([])          // 选中的 layout 分组 id（空 = 全部）
  const histActiveCount = ref(0)
  // 排查"点该亮没亮"时要能从控制台看到这一帧到底点亮了谁（App.vue 的 window.knowrary）
  const activeIds = () => (histActive ? [...histActive] : null)
  const isPlaying = ref(false)
  // 倍速：讲给别人听时要能放慢。存 localStorage——分享前调好，下次还是它
  const speed = ref(Number(localStorage.getItem('knowrary-speed')) || 1)
  // 沿演化链导览：跟着 plan.chain 一站站走。年份回放管"到哪一年"，导览管"走到哪一站"
  const tour = reactive({ on: false, i: 0, auto: false })

  // 游标停在当前年份时"已发生"的那批节点。留着当下一站的对照，才知道该点亮谁。
  let histActive = null
  let playing = null
  let enterTimer = null
  let tourTimer = null

  const stepMs = () => Math.round(STEP_MS / speed.value)
  const histFamilies = () => new Set(['演化', '依赖', '对照'].filter((f) => hist[f]))
  const histChain = computed(() => histPlan.value?.trunk || null)
  const timelineChoices = computed(() => timelineOptions(layoutDoc.value))

  const yearRange = computed(() => {
    const years = (indexDoc.value?.nodes || []).filter((n) => typeof n.year === 'number').map((n) => n.year)
    return years.length ? [Math.min(...years), Math.max(...years)] : [0, 0]
  })

  /** 「按抽象层」那一档旁边的提示：有多少节点填了 layer。没填的会全挤进「未分层」。 */
  const layeredHint = computed(() => {
    const withYear = (indexDoc.value?.nodes || []).filter((n) => !n.virtual && typeof n.year === 'number')
    const n = withYear.filter((x) => x.layer).length
    return withYear.length ? `${n}/${withYear.length} 已分层` : ''
  })

  /** 演化链＝一个系列：不另设 series 字段，谱系树里那几块连通块本来就是"一家子"。 */
  const lineageChains = computed(() => {
    if (!indexDoc.value) return []
    const edges = indexDoc.value.edges.filter((e) => e.family === '演化')
    const near = new Map()
    const touch = (a, b) => { if (!near.has(a)) near.set(a, []); near.get(a).push(b) }
    for (const e of edges) { touch(e.source, e.target); touch(e.target, e.source) }
    const seen = new Set()
    const out = []
    for (const id of near.keys()) {
      if (seen.has(id)) continue
      const bag = []
      const stack = [id]
      seen.add(id)
      while (stack.length) {
        const cur = stack.pop()
        bag.push(cur)
        for (const t of near.get(cur) || []) if (!seen.has(t)) { seen.add(t); stack.push(t) }
      }
      const names = bag.map((x) => indexDoc.value.nodes.find((n) => n.id === x))
        .filter(Boolean).sort((a, b) => (a.year ?? 9999) - (b.year ?? 9999))
      out.push({ ids: bag, name: `${names[0]?.name || bag[0]} 一系（${bag.length}）` })
    }
    return out.sort((a, b) => b.ids.length - a.ids.length)
  })

  // 流派时间带的数据。**只在历史视图这一条路上取**——流派没有独立入口，
  // 别的视图不需要知道它存在，所以这份取数也不该爬到 App.vue 去。
  // 按 index revision 缓存：图没变就不重复问服务端（换时间线 / 切紧凑都会重渲染）。
  const schools = ref([])
  let schoolsRev = Symbol('未取过')
  async function ensureSchools() {
    const rev = indexDoc.value?.revision ?? null
    if (schoolsRev === rev) return
    try {
      schools.value = (await fetchSchools()).schools || []
    } catch {
      schools.value = []      // 取不到就不画带子，历史图照常能看——别为一层背景把整张图拖垮
    }
    schoolsRev = rev
  }

  /**
   * 重建整张历史图。
   *
   * **只在"图本身变了"时调用**：换时间线 / 切主干道 / 改紧凑 / 改关系族。
   * 拖滑块和回放不走这里——那两件事只挪游标（paintTime），一个 cell 都不重建。
   */
  async function renderHistory({ view = 'fit' } = {}) {
    await ensureSchools()
    const g = graph.value
    const cells = buildHistoryCells(indexDoc.value, layoutDoc.value, {
      timelines: timelines.value, families: histFamilies(), compact: hist.compact, trunk: hist.trunk,
      schools: schools.value,
    })
    histPlan.value = cells.plan
    applyingViewport.value = true
    // 先定视口再建 cell：时间轴的宽高比极端，先按算好的框定缩放，mount 出来就是完整一屏
    if (view !== 'keep') {
      g.zoomToRect({ x: -80, y: 0, width: cells.plan.width + 160, height: cells.plan.height + 60 },
                   { maxScale: 1, minScale: 0.35 })
    }
    mount(g, cells)
    applyingViewport.value = false
    zoom.value = g.zoom()
    histActive = null                 // cell 是新的，class 也没了；这一帧不做点亮动画
    paintTime()
    paintTour()
    const d = cells.plan.diagnostics
    const parts = [`${cells.plan.placed.size} 个有 year 的节点 · ${cells.edges.length} 条边`]
    if (d.noYear) parts.push(`${d.noYear} 个节点没有 year，不进历史图`)
    if (d.missingYear.length) parts.push(`${d.missingYear.length} 条演化边缺年份（${d.missingYear[0]} …）`)
    setBanner(parts.join('；'), d.missingYear.length ? 'error' : '')
  }

  /**
   * 谱系树：只画演化族，根在下、叶在上，枝丫粗细按这条枝上挂着多少东西算。
   *
   * 和历史视图一样**不持久化坐标**：它是算出来的视图，不是人摆的图。
   */
  function renderLineage({ view = 'fit' } = {}) {
    const g = graph.value
    const cells = buildLineageCells(indexDoc.value, layoutDoc.value)
    linPlan.value = cells.plan
    applyingViewport.value = true
    if (view !== 'keep') {
      g.zoomToRect({ x: -60, y: -40, width: cells.plan.width + 120, height: cells.plan.height + 80 },
                   { maxScale: 1, minScale: 0.25 })
    }
    mount(g, cells)
    applyingViewport.value = false
    zoom.value = g.zoom()
    const p = cells.plan
    const parts = [`演化族 ${cells.edges.length} 条边 · ${p.placed.size} 个点 · ${p.levels} 层`,
                   `根：${p.roots.slice(0, 3).join('、')}${p.roots.length > 3 ? ` 等 ${p.roots.length} 个` : ''}`]
    if (p.dropped.length) parts.push(`断掉 ${p.dropped.length} 条环边（${p.dropped[0].id} ${p.dropped[0].why}）`)
    setBanner(parts.join('；'), p.dropped.length ? 'error' : '')
  }

  /** 把游标挪到当前年份。拖滑块、回放、切有效期都只走这条路——不重建任何 cell。 */
  function paintTime({ pulse = true } = {}) {
    histActive = paintHistoryTime(graph.value, histPlan.value,
                                  { upto: hist.upto, validity: hist.validity,
                                    prev: pulse ? histActive : null })
    histActiveCount.value = histActive?.size ?? 0
  }

  /**
   * 历史视图给容器加个类名，游标相关的样式只在这个模式下生效。
   *
   * kg-enter 是一次性的：进场淡入只该在刚切进来那一下放一次。留着的话，
   * 每次重建 cell（换时间线、改关系族）都会整屏重放一遍。
   */
  function markHistoryContainer(next) {
    const el = graph.value?.container
    if (!el) return
    const on = next === 'history'
    el.classList.toggle('kg-history', on)
    clearTimeout(enterTimer)
    el.classList.toggle('kg-enter', on)
    if (on) enterTimer = setTimeout(() => el.classList.remove('kg-enter'), 400)
  }

  function setUpto(value) {
    hist.upto = value === '' || value === null ? null : Number(value)
    paintTime()
  }

  /**
   * 按年回放。
   *
   * 跳的是"有事发生的年份"而不是日历年：真实数据 1936–2018 跨 83 年，
   * 其中只有 22 年有节点——逐年走的话 73% 的站什么都不会变，纯粹在空转。
   */
  function togglePlay() {
    if (playing) return stopPlay()
    stopTour()                        // 两个都在推游标会打架，同一时刻只留一个
    const years = histPlan.value?.eventYears || []
    if (years.length < 2) return setBanner('这张图上只有一个年份，没什么可回放的', 'error')
    // 已经放到最后一站（或压根没设年份）就从头来，否则接着当前位置往下走
    let i = hist.upto === null ? -1 : years.findIndex((y) => y > hist.upto)
    if (i < 0) i = 0
    isPlaying.value = true
    setUpto(years[i])
    playing = setInterval(() => {
      i += 1
      if (i >= years.length) return stopPlay()
      setUpto(years[i])
    }, stepMs())
  }

  /** 换倍速：正在放就重起一次定时器，否则要等下一站才生效。 */
  function setSpeed(x) {
    speed.value = x
    try { localStorage.setItem('knowrary-speed', String(x)) } catch { /* 无痕模式 */ }
    if (playing) { stopPlay(); togglePlay() }
    setBanner(`回放速度 ${x}×`, 'success')
  }

  function stopPlay() {
    if (playing) clearInterval(playing)
    playing = null
    isPlaying.value = false
  }

  // —— 沿演化链导览 ——
  //
  // 技术史的叙事单位是"谁接谁"，不是"哪一年"：年份只是坐标轴。
  // 实盘 1936–2018 里 61 年是空的，按年走一路都是空档；按演化链走，站站有内容。

  const tourChain = computed(() => histPlan.value?.chain || [])
  const tourStopId = computed(() => tourChain.value[tour.i] || null)
  const tourStop = computed(() => {
    const id = tourStopId.value
    if (!id) return null
    const meta = (indexDoc.value?.nodes || []).find((n) => n.id === id) || {}
    return { id, name: meta.name || id, desc: meta.desc || '', year: histPlan.value?.placed.get(id)?.year }
  })
  /** 上一站是怎么接到这一站的：把那条演化边的类型写出来，"谁接谁"才算讲清楚。 */
  const tourVia = computed(() => {
    if (tour.i <= 0) return '起点'
    const eid = histPlan.value?.chainEdges?.[tour.i - 1]
    const edge = eid ? (indexDoc.value?.edges || []).find((e) => e.id === eid) : null
    const from = tourChain.value[tour.i - 1]
    return edge ? `${from} —${edge.type || edge.family}→` : `接 ${from}`
  })

  /** 链走不长时，把原因摆出来：是"图里就这么点演化边"，而不是功能坏了。 */
  function evoGap() {
    const d = histPlan.value?.diagnostics || {}
    return `图上一共 ${d.evoAll ?? 0} 条演化边，其中 ${d.evoUsable ?? 0} 条两端都有 year`
  }

  function startTour() {
    stopPlay()
    const chain = histPlan.value?.chain || []
    if (chain.length < 2) {
      return setBanner(`找不到连续的演化链，导览走不起来——${evoGap()}。`
        + '给发展史节点补上 year，再用「演化为 / 源自 / 被激活」把它们串起来', 'error')
    }
    // 导览走的就是演化边，关着的话先打开——否则用户只看到镜头在跳，看不到"沿着什么走"
    if (!hist.演化) {
      hist.演化 = true
      renderHistory({ view: 'keep' })
    }
    tour.on = true
    tour.auto = true
    tourGo(0)
    const head = `沿演化链导览：${chain.length} 站，${chain[0]} → ${chain[chain.length - 1]}（Esc 退出）`
    // 三站以下基本讲不出故事，顺手把欠账报出来，别让人以为是功能没做好
    setBanner(chain.length < 4 ? `${head}。链这么短是因为${evoGap()}` : head,
              chain.length < 4 ? 'error' : '')
  }

  function tourGo(i) {
    const chain = histPlan.value?.chain || []
    if (!chain.length) return stopTour()
    tour.i = Math.max(0, Math.min(chain.length - 1, i))
    const box = histPlan.value.placed.get(chain[tour.i])
    if (!box) return stopTour()
    setUpto(box.year)                 // 游标跟着走：导览管节奏，游标管坐标，两边永远一致
    paintTour()
    flyTo({ cx: box.x + box.w / 2, cy: box.y + box.h / 2, zoom: TOUR_ZOOM }, 520)
    scheduleTour()
  }

  /** 重建过图之后（换时间线 / 关系族）也要重新点上，否则 class 和高亮都随着旧 cell 没了。 */
  function paintTour() {
    if (!tour.on) return
    markStop(graph.value, tourStopId.value)
    highlightEdges(graph.value, new Set(histPlan.value?.chainEdges || []), { flow: true })
  }

  function scheduleTour() {
    clearTimeout(tourTimer)
    tourTimer = null
    if (!tour.on || !tour.auto) return
    // 走到最后一站就停下，不回头重播：导览是"讲完一条线"，不是循环屏保
    if (tour.i >= tourChain.value.length - 1) { tour.auto = false; return }
    tourTimer = setTimeout(() => tourGo(tour.i + 1), Math.round(TOUR_MS / speed.value))
  }

  function toggleTourAuto() {
    tour.auto = !tour.auto
    // 停在最后一站时再点"自动走"，就是从头再讲一遍
    if (tour.auto && tour.i >= tourChain.value.length - 1) return tourGo(0)
    scheduleTour()
  }

  function stopTour() {
    clearTimeout(tourTimer)
    tourTimer = null
    tour.on = false
    tour.auto = false
    markStop(graph.value, null)
    cancelFly()
    highlightEdges(graph.value, null)
  }

  function toggleTimeline(id) {
    timelines.value = timelines.value.includes(id)
      ? timelines.value.filter((x) => x !== id)
      : [...timelines.value, id]
    renderHistory({ view: 'fit' })
  }

  function toggleHistFamily(f) {
    hist[f] = !hist[f]
    renderHistory({ view: 'keep' })
  }

  return {
    hist, histPlan, linPlan, timelines, histActiveCount, isPlaying, speed, tour,
    histChain, timelineChoices, yearRange, layeredHint, lineageChains,
    tourChain, tourStopId, tourStop, tourVia,
    histFamilies, renderHistory, renderLineage, paintTime, markHistoryContainer, setUpto,
    togglePlay, setSpeed, stopPlay, evoGap, startTour, tourGo, paintTour, scheduleTour,
    toggleTourAuto, stopTour, toggleTimeline, toggleHistFamily, histActiveIds: activeIds,
  }
}
