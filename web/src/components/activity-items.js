/**
 * 左侧活动栏的工具清单：**哪个工具窗口属于哪个作用域、哪个视图、哪一段**。
 *
 * 单独成文件是因为这张表有两个消费者：活动栏自己（画哪些按钮），
 * 以及 App.vue 切视图时的"原来开着的面板还该不该留"。两边各写一套硬编码规则，
 * 迟早会对不上——比如谱系视图现在一个工具都不给，面板却还挂在那儿，而关它的按钮已经没了。
 *
 * `scopes` = 在哪个作用域下出现（project = 选着某个项目时，global = 选着「🌐 全局」时）；
 * `modes`  = 在哪几个视图下有意义（贴图要有画布，时间线只属于历史）。
 *            **谱系视图一个都不给**：它是一张只读的演化树，看的时候不该有别的事在旁边招手；
 *            整条栏跟着收起来，画布多 72px。历史视图同理只留看得懂这张图要用的两个。
 * `group`  = 这个工具是干什么用的，决定它落在哪一段（见 GROUPS）。
 */
export const ITEMS = [
  { id: 'plans', icon: 'checklist', name: '清单', group: 'do', scopes: ['project'],
    tip: '这个项目的学习计划 / 面试方案 / 领域地图', modes: ['chat', 'project', 'structure'] },
  { id: 'study', icon: 'rotate', name: '今日', group: 'do', gold: true, badge: 'due', scopes: ['project', 'global'],
    tip: '今天该建什么、该复习什么（复习是全局的，建设按当前项目过滤）',
    modes: ['chat', 'project', 'structure'] },
  { id: 'import', icon: 'file', name: '导入', group: 'do', scopes: ['project', 'global'],
    tip: '把一篇笔记拆成知识点导进图谱（项目下先认领清单里没建的点）', modes: ['chat', 'project', 'structure'] },
  // 原文是整个库的东西（不分项目），所以两个作用域都给；对话里边聊边翻原文也常见
  { id: 'articles', icon: 'book', name: '原文', group: 'do', scopes: ['project', 'global'],
    tip: '读原文：长文原样存在原文目录里，看它拆出了哪些点；还没拆的在这儿一键拆',
    modes: ['chat', 'project', 'structure'] },
  { id: 'assets', icon: 'image', name: '素材', group: 'do', scopes: ['project', 'global'],
    tip: '往当前这块画布上贴图、加便签', modes: ['project', 'structure'] },

  // 「项目」跟项目内的「清单」是两件事，图标别再共用 checklist——
  // 同一个图标一会儿叫「清单」一会儿叫「项目」，等于这个图标不再有辨识度。
  { id: 'plans', icon: 'grid', name: '项目', group: 'org', scopes: ['global'], key: 'plans-global',
    tip: '所有项目：新建、切换、改配置', modes: ['chat', 'project', 'structure'] },
  { id: 'inbox', icon: 'inbox', name: 'Inbox', group: 'org', badge: 'inbox', scopes: ['global'],
    tip: '待上全局图的知识点', modes: ['project', 'structure'] },
  { id: 'digest', icon: 'layers', name: '欠账', group: 'org', scopes: ['global'],
    tip: '草稿 / 桥 / 连边建议 / 重复 / stub（整张图的）', modes: ['chat', 'project', 'structure'] },

  // 对比在 compare 模式下也留着：进了某个组还能直接跳去另一个组，
  // 不用先退回全局图再进来一次
  { id: 'compare', icon: 'table', name: '对比', group: 'see', scopes: ['global'],
    tip: '横向对比组：一组技术按同几个维度摆成一张表',
    modes: ['chat', 'project', 'structure', 'compare'] },
  { id: 'stats', icon: 'chart', name: '参数量', group: 'see', scopes: ['global'],
    tip: '填了 params 的知识点：怎么涨上来的、谁更大', modes: ['structure', 'history'] },
  { id: 'calendar', icon: 'clock', name: '日历', group: 'see', scopes: ['global'],
    tip: '每天建了多少、复习了多少（全是算出来的）', modes: ['chat', 'project', 'structure'] },
  { id: 'timeline', icon: 'timeline', name: '时间线', group: 'see', scopes: ['global'],
    tip: '历史视图的泳道与过滤', modes: ['history'] },
]

// 三段，顺序固定：**现在动手做**的、**整理存量**的、**回头看数**的。
// 固定顺序是为了跨视图不跳位——全局图 8 个、历史 2 个、谱系 0 个（整条收起），
// 每段自己少几项，但段与段的先后不变，肌肉记忆才立得住。
export const GROUPS = ['do', 'org', 'see']

/** 当前该按哪套工具算：**作用域跟着视图走，不只跟着项目选择走**。
 *
 * 顶栏已经把视图分成了项目级（对话 / 项目图）和全局级（全局图 / 历史 / 谱系）。
 * 站在全局图或历史视图上时，就算选着某个项目，你要的也是整张图的工具
 * （Inbox / 欠账 / 时间线）——按项目过滤会把它们藏起来，
 * 于是"历史视图里调不出时间线面板"。
 */
export function scopeOf(mode, project) {
  const projectView = mode === 'chat' || mode === 'project'
  return projectView && project ? 'project' : 'global'
}

/** 这个视图下该出现的工具，按 GROUPS 切成段（空段直接丢掉，不留空分隔线）。 */
export function railGroups(mode, project) {
  const scope = scopeOf(mode, project)
  const items = ITEMS.filter((it) => it.scopes.includes(scope) && it.modes.includes(mode))
  return GROUPS
    .map((group) => ({ scope, group, items: items.filter((it) => it.group === group) }))
    .filter((g) => g.items.length)
}

/** 这个面板在这个视图下还开得出来吗（切视图时用来关掉已经没有入口的面板）。 */
export function panelOk(id, mode, project) {
  if (!id) return true
  const scope = scopeOf(mode, project)
  return ITEMS.some((it) => it.id === id && it.scopes.includes(scope) && it.modes.includes(mode))
}
