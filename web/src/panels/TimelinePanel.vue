<script setup>
// 历史视图的时间线选择：按 layout 分组切出独立的时间线，可多选叠加。
// 原来是顶栏下面一整条 chips，分组一多就换行把画布压矮。
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'
import { BY_LAYER as TL_BY_LAYER, BY_SCHOOL as TL_BY_SCHOOL,
         BY_DOMAIN as TL_BY_DOMAIN } from '../canvas/timeline.js'

defineProps({
  options: { type: Array, default: () => [] },
  selected: { type: Array, default: () => [] },
  families: { type: Object, required: true },
  trunk: { type: Boolean, default: false },
  chain: { type: Array, default: null },      // 主干道模式下算出来的那条链
  layered: { type: String, default: '' },     // 「12/22 已分层」这类提示
})
const emit = defineEmits(['close', 'toggle', 'select-all', 'toggle-family', 'toggle-trunk'])

const FAMS = ['演化', '依赖', '对照']
const BY_LAYER = TL_BY_LAYER
const BY_SCHOOL = TL_BY_SCHOOL
const BY_DOMAIN = TL_BY_DOMAIN
</script>

<template>
  <Drawer side="left" title="时间线" icon="timeline" storage-key="timeline" :default-width="284"
          @close="emit('close')">
    <template #default>
      <p class="dim" style="font-size: 12.5px; line-height: 1.6">
        按分组切出独立的一条时间线（JVM 史、LLM 史各自成线），可多选叠加。
      </p>

      <label class="switch-row" style="margin: 12px -9px 0">
        <input type="checkbox" :checked="trunk" @change="emit('toggle-trunk')" />
        <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
        <span class="label">主干道
          <span class="sub">把最长的一条演化链拉成水平主轴，旁支挂上下。<br>
            看「谁接谁」用它；看「谁和谁是一类」用下面的泳道。</span>
        </span>
      </label>
      <p v-if="trunk && chain?.length" class="dim tl-chain">
        主干 {{ chain.length }} 站：{{ chain.join(' → ') }}
      </p>
      <p v-else-if="trunk" class="warn-text" style="font-size: 11.5px; line-height: 1.6">
        当前范围里找不到连续的演化链（需要两端都有 year 的「演化」边），已退回泳道。
      </p>

      <div class="section" style="margin-top: 14px">
        <div class="section-head">泳道按什么分</div>
        <ul class="tl-list">
          <li>
            <button class="tl-btn" :class="{ on: !selected.length }" @click="emit('select-all')">
              <Icon name="layers" :size="14" />按领域（field）
            </button>
          </li>
          <li>
            <!-- 抽象层是和主题正交的另一个维度：结构图按主题分组，历史图按层分泳道，各管各的 -->
            <button class="tl-btn" :class="{ on: selected.includes(BY_LAYER) }"
                    title="理论 / 硬件 / 体系结构 / 汇编接口 / 系统软件 / 高级语言 / AI应用（节点 frontmatter 里的 layer）"
                    @click="emit('toggle', BY_LAYER)">
              <Icon :name="selected.includes(BY_LAYER) ? 'check' : 'timeline'" :size="14" />
              按抽象层<span class="dim" style="margin-left: auto; font-size: 10.5px">{{ layered }}</span>
            </button>
          </li>
          <li>
            <!-- 按流派：左侧那列标题就是流派名 + 年份区间，道自己就是那条时间带。
                 **一个点可以同时属于两派**（现代Intel微架构：前端 CISC、后端 RISC 式 μops），
                 第二派起画成空心虚线的影子——重叠因此看得见，而不是被迫二选一藏起来。 -->
            <button class="tl-btn" :class="{ on: selected.includes(BY_SCHOOL) }"
                    title="按流派分泳道，早出现的在上面；同属两派的点在两条道各出现一次（第二份是空心影子，不带边）"
                    @click="emit('toggle', BY_SCHOOL)">
              <Icon :name="selected.includes(BY_SCHOOL) ? 'check' : 'timeline'" :size="14" />
              按流派
            </button>
          </li>
          <li>
            <!-- 领域线和流派是**两个正交的维度**：AlexNet 在主张这一维属于连接主义、
                 在领域这一维属于 CV。所以是两档泳道，不是一档里的两种颜色。 -->
            <button class="tl-btn" :class="{ on: selected.includes(BY_DOMAIN) }"
                    title="按任务领域分泳道（NLP / CV / ASR）；Transformer 之后三条线会在同一年出现同一个点"
                    @click="emit('toggle', BY_DOMAIN)">
              <Icon :name="selected.includes(BY_DOMAIN) ? 'check' : 'timeline'" :size="14" />
              按领域线
            </button>
          </li>
          <li v-if="options.length" class="tl-sub">按分组（选一层当泳道）</li>
          <li v-for="opt in options" :key="opt.id">
            <button class="tl-btn" :class="{ on: selected.includes(opt.id) }"
                    :style="{ paddingLeft: `${10 + opt.depth * 12}px` }" @click="emit('toggle', opt.id)">
              <Icon :name="selected.includes(opt.id) ? 'check' : 'timeline'" :size="14" />
              {{ opt.name }}
            </button>
          </li>
        </ul>
      </div>

      <div class="section">
        <div class="section-head">显示的关系族</div>
        <label v-for="f in FAMS" :key="f" class="switch-row">
          <input type="checkbox" :checked="families[f]" @change="emit('toggle-family', f)" />
          <span class="check"><Icon name="check" :size="11" :width="2.6" /></span>
          <span class="label">{{ f }}</span>
        </label>
      </div>

      <p class="dim" style="font-size: 11.5px; line-height: 1.6; margin-top: 14px">
        历史视图只是浏览：X 轴锁在年份上，坐标不会写回布局。
      </p>
    </template>
  </Drawer>
</template>
