<script setup>
// 欠账清单：图谱里"还没处理完"的东西。每一条都能点，点了就在画布上定位过去。
//
// 只管图谱本身的欠账（草稿 / 桥 / 重复 / stub）。"今天该复习什么、哪些老错"
// 是我脑子里的欠账，在「学习」面板，不在这里重复一遍。
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  digest: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['goto', 'close', 'refresh', 'merge', 'link', 'suggest', 'years', 'misplace', 'regroup'])

const total = () => {
  const c = props.digest?.counts
  if (!c) return 0
  // 孤点不计进总数：47 个孤点会把角标顶成一个吓人的数字，而它们是**长期欠账**，
  // 不是"今天冒出来的待办"。它有自己那一节的计数。
  return (c.drafts || 0) + (c.links || 0) + (c.duplicates || 0) + (c.stubs || 0)
    + (c.cycles || 0) + (c.bad_years || 0) + (c.misplaced || 0) + (c.issues || 0)
}
</script>

<template>
  <Drawer side="left" title="欠账清单" icon="checklist" storage-key="digest" :default-width="312"
          @close="emit('close')">
    <template #head-actions>
      <span v-if="digest" class="head-count">{{ total() }}</span>
      <button class="icon-btn ghost tiny" title="重新统计" @click="emit('refresh')">
        <Icon name="refresh" :size="14" />
      </button>
    </template>

    <template #default>
      <p v-if="!digest" class="dim">统计中…</p>
      <template v-else>
        <section v-if="digest.drafts.length" class="section" style="margin-top: 0">
          <div class="section-head">
            <Icon name="pencil" :size="13" />草稿 <span class="count">{{ digest.counts.drafts }}</span>
            <span v-if="digest.counts.stale_drafts" class="tail warn-text">
              {{ digest.counts.stale_drafts }} 个放太久
            </span>
            <button class="btn subtle tiny" :class="{ tail: !digest.counts.stale_drafts }"
                    :disabled="busy"
                    title="把落在领域大框里的草稿挪进它那一层的泳道（硬件 / 系统软件 / AI应用…）。
只动草稿，只往已有的泳道里挪；节点没填 layer 的先去检查器里补上"
                    @click="emit('regroup')">
              <Icon name="layers" :size="13" />按层归位
            </button>
          </div>
          <ul>
            <li v-for="d in digest.drafts" :key="d.id" class="edge-row">
              <span class="to link" :class="{ 'warn-text': d.stale }" @click="emit('goto', d.id)">{{ d.id }}</span>
              <span class="yr">{{ d.days ?? '?' }} 天</span>
            </li>
          </ul>
        </section>

        <section v-if="digest.bridges.length" class="section">
          <div class="section-head">
            <Icon name="link" :size="13" />跨分组的桥 <span class="count">{{ digest.counts.bridges }}</span>
          </div>
          <p class="dim" style="font-size: 11.5px; margin-bottom: 6px">连接两个领域的关系，通常最值得整理。</p>
          <ul>
            <li v-for="b in digest.bridges" :key="`${b.from}->${b.to}`" class="edge-row">
              <span class="to">{{ b.from_name }} → {{ b.to_name }}</span>
              <span class="yr">{{ b.count }} 条</span>
            </li>
          </ul>
        </section>

        <!-- 连边建议：名字摆明了有关系、图上却没连的那些对。
             这些**原来是"重复候选"**——中文复合词天生共享中心语（内存 / 堆内存 / 栈内存
             字面重合度 0.8），相似度只能说"这俩像"，说不出像在哪。而像在哪正是答案：
             含着对方 ⇒ 上下位，掐掉公共词缀还各剩一点 ⇒ 同级兄弟。都不是重复，是缺边。 -->
        <section v-if="digest.links?.length" class="section">
          <div class="section-head">
            <Icon name="link" :size="13" />连边建议 <span class="count">{{ digest.counts.links }}</span>
          </div>
          <p class="dim" style="font-size: 11.5px; margin-bottom: 6px">
            名字看着有关系、图上却没连。孤点排在前面——它们连一条就从孤岛回到图里。
          </p>
          <ul>
            <li v-for="h in digest.links" :key="`${h.source}|${h.target}`" class="card"
                style="padding: 8px 10px">
              <div>
                <span class="link" @click="emit('goto', h.source)">{{ h.source }}</span>
                <span class="dim"> {{ h.relation }} → </span>
                <span class="link" @click="emit('goto', h.target)">{{ h.target }}</span>
                <span v-if="h.lonely" class="tag warn" style="margin-left: 6px"
                      :title="h.lonely === 2 ? '两端都还没有任何边' : '有一端还没有任何边'">
                  {{ h.lonely === 2 ? '两端都是孤点' : '有一端是孤点' }}
                </span>
              </div>
              <div class="dim" style="font-size: 11.5px">{{ h.reason }}</div>
              <button class="btn subtle tiny" style="margin-top: 6px"
                      title="打开关系对话框，类型和目标已经填好——方向和类型仍然由你定"
                      @click="emit('link', { source: h.source, target: h.target, relation: h.relation })">
                <Icon name="link" :size="12" />连边
              </button>
            </li>
          </ul>
        </section>

        <!-- 孤点：一条关系都没有的已建节点。**这张图最大的一笔欠账**——整个产品都建在
             边上，实盘却有六成节点度为 0。上面的连边建议只认得出名字有线索的那些，
             `eBPF`、`乐观锁` 这种名字上看不出亲戚的一条都提不出来，那正是要问 AI 的部分。 -->
        <section v-if="digest.lonely?.length" class="section">
          <div class="section-head">
            <Icon name="warn" :size="13" />孤点 <span class="count">{{ digest.counts.lonely }}</span>
          </div>
          <p class="dim" style="font-size: 11.5px; margin-bottom: 6px">
            一条关系都没有。「问 AI」会定位过去并在右侧检查器里给出连边建议——
            <b>一次一个点</b>，不批量：47 个点跑一轮就是 47 次模型调用。
          </p>
          <ul>
            <li v-for="n in digest.lonely" :key="n.id" class="card" style="padding: 8px 10px">
              <div>
                <span class="link" @click="emit('goto', n.id)">{{ n.name }}</span>
                <span v-if="n.field" class="dim" style="font-size: 11px"> · {{ n.field }}</span>
              </div>
              <div v-if="n.desc" class="dim" style="font-size: 11.5px">{{ n.desc }}</div>
              <button class="btn subtle tiny" style="margin-top: 6px" :disabled="busy"
                      title="定位到它，并让模型看着图里的候选节点提几条关系"
                      @click="emit('suggest', n.id)">
                <Icon name="rotate" :size="12" />问 AI 连什么
              </button>
            </li>
          </ul>
        </section>

        <section v-if="digest.duplicates.length" class="section">
          <div class="section-head">
            <Icon name="layers" :size="13" />重复候选 <span class="count">{{ digest.counts.duplicates }}</span>
          </div>
          <ul>
            <li v-for="x in digest.duplicates" :key="`${x.a}|${x.b}`" class="card" style="padding: 8px 10px">
              <div>
                <span class="link" @click="emit('goto', x.a)">{{ x.a }}</span>
                <span class="dim"> / </span>
                <span class="link" @click="emit('goto', x.b)">{{ x.b }}</span>
              </div>
              <div class="dim" style="font-size: 11.5px">{{ x.reason }}</div>
              <button class="btn subtle tiny" style="margin-top: 6px"
                      title="并成一张：关系、引用、正文、复习记录都迁过去"
                      @click="emit('merge', { keep: x.a, drop: x.b })">
                <Icon name="layers" :size="12" />合并
              </button>
            </li>
          </ul>
        </section>

        <section v-if="digest.stubs.length" class="section">
          <div class="section-head">
            <Icon name="file" :size="13" />待补的 stub <span class="count">{{ digest.counts.stubs }}</span>
          </div>
          <ul>
            <li v-for="id in digest.stubs" :key="id" class="edge-row">
              <span class="to link" @click="emit('goto', id)">{{ id }}</span>
            </li>
          </ul>
        </section>

        <!-- 出错流水：工具失败 / 写回被拒 / LLM 挂了，原来各自闪一下就没了，
             "这东西为什么老出问题"没地方回答。攒起来才看得出反复出现的那一类。 -->
        <section v-if="digest.issues?.count" class="section">
          <div class="section-head">
            <Icon name="warn" :size="13" />最近出的错
            <span class="count">{{ digest.issues.count }}</span>
            <span class="dim" style="font-size: 10.5px; margin-left: auto">
              {{ Object.entries(digest.issues.by_kind).map(([k, n]) => `${k} ${n}`).join(' · ') }}
            </span>
          </div>
          <ul>
            <li v-for="(it, i) in digest.issues.recent" :key="`i${i}`" class="card warn-text"
                :title="it.ts">
              <b>{{ it.kind }}{{ it.where ? ` · ${it.where}` : '' }}</b>：{{ it.message }}
            </li>
          </ul>
          <p class="dim" style="font-size: 10.5px; line-height: 1.6">
            全量在 <code>.knowrary/issues.jsonl</code>（只留最近 500 条）。
            同一类反复出现，多半是工具本身有问题，不是手滑。
          </p>
        </section>

        <!-- 缺 year：和「年份可疑」分开——那个是算得出来的矛盾（两端倒挂），
             这个只是没填。没填不是错，但它是历史视图的开关：一个节点没有 year
             就根本不出现在时间轴上，而"时间轴上少了谁"最不容易看出来。 -->
        <section v-if="digest.counts?.no_year" class="section">
          <div class="section-head">
            <Icon name="clock" :size="13" />缺 year <span class="count">{{ digest.counts.no_year }}</span>
          </div>
          <p class="dim" style="font-size: 11.5px; margin-bottom: 6px">
            没填 year 的节点不进历史视图。「让 AI 补一轮」是<b>一次调用</b>问完一批，
            不是一个一个问（那样是 {{ digest.counts.no_year }} 次）。逐条勾选后才写回。
          </p>
          <button class="btn subtle tiny" :disabled="busy" @click="emit('years')">
            <Icon name="clock" :size="12" />让 AI 补一轮
          </button>
          <ul style="margin-top: 6px">
            <li v-for="id in digest.no_year" :key="id" class="edge-row">
              <span class="to link" @click="emit('goto', id)">{{ id }}</span>
            </li>
          </ul>
        </section>

        <!-- 域不符：和「年份可疑」同一类——算得出来的矛盾，不依赖任何外部知识。
             分组 id 的生成规则就是 g-<field>--<layer>，所以"该归哪个域"是机械可算的。
             会走散是因为 field 在 md、group 在 layout.json，改 md 不动画布——那条分界
             是对的（否则手工摆位会被一次改 frontmatter 冲掉），代价是两边能悄悄不一致。 -->
        <section v-if="digest.misplaced?.length" class="section">
          <div class="section-head">
            <Icon name="warn" :size="13" />域不符 <span class="count">{{ digest.counts.misplaced }}</span>
          </div>
          <p class="dim" style="font-size: 11.5px; margin-bottom: 6px">
            md 里的 <code>field</code> 和它在画布上待的域对不上。改 field 不会自动挪画布——
            <b>这是故意的</b>，否则你手工摆的位置会被一次改 frontmatter 冲掉。
          </p>
          <ul>
            <li v-for="m in digest.misplaced" :key="m.id" class="card" style="padding: 8px 10px">
              <div>
                <span class="link" @click="emit('goto', m.id)">{{ m.id }}</span>
                <span class="dim" style="font-size: 11.5px">
                  field={{ m.field }}，却摆在「{{ m.group_name }}」
                </span>
              </div>
              <div class="dim" style="font-size: 11.5px">
                该去 {{ m.field }}{{ m.layer ? `/${m.layer}` : '' }}
                <b v-if="!m.want_exists" class="warn-text">（那条道还没建）</b>
              </div>
              <button class="btn subtle tiny" style="margin-top: 6px" :disabled="busy"
                      :title="m.want_exists ? '挪进那条道' : '现开一条道，再把它挪进去（只往下长，不动已有的框）'"
                      @click="emit('misplace', m)">
                <Icon name="layers" :size="12" />{{ m.want_exists ? '挪过去' : '开一条道并挪过去' }}
              </button>
            </li>
          </ul>
        </section>

        <!-- 年份可疑：唯一不依赖外部知识的年份矫正——不问"1997 对不对"，
             只问"这条演化线自己自洽吗"。口述的年份没人能核，倒挂能算出来。 -->
        <section v-if="digest.bad_years?.length" class="section">
          <div class="section-head">
            <Icon name="clock" :size="13" />年份可疑
            <span class="count">{{ digest.counts.bad_years }}</span>
          </div>
          <ul>
            <li v-for="(m, i) in digest.bad_years" :key="`y${i}`" class="card warn-text">{{ m }}</li>
          </ul>
        </section>

        <section v-if="digest.cycles.length" class="section">
          <div class="section-head warn-text">
            <Icon name="warn" :size="13" />方向矛盾 <span class="count">{{ digest.counts.cycles }}</span>
          </div>
          <ul>
            <li v-for="(m, i) in digest.cycles" :key="i" class="card warn-text">{{ m }}</li>
          </ul>
        </section>

        <div v-if="!total()" class="empty">
          <Icon name="check" :size="30" :width="1.3" />
          <span class="t">没有欠账</span>
          <span class="s">草稿、连边建议、重复和 stub 都清空了。</span>
        </div>

        <p class="dim" style="font-size: 11px; margin-top: 16px">统计于 {{ digest.generated_at }}</p>
      </template>
    </template>
  </Drawer>
</template>
