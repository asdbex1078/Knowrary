<script setup>
// 历史视图的时间线选择：按 layout 分组切出独立的时间线，可多选叠加。
// 原来是顶栏下面一整条 chips，分组一多就换行把画布压矮。
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

defineProps({
  options: { type: Array, default: () => [] },
  selected: { type: Array, default: () => [] },
  families: { type: Object, required: true },
})
const emit = defineEmits(['close', 'toggle', 'select-all', 'toggle-family'])

const FAMS = ['演化', '依赖', '对照']
</script>

<template>
  <Drawer side="left" title="时间线" icon="timeline" storage-key="timeline" :default-width="284"
          @close="emit('close')">
    <template #default>
      <p class="dim" style="font-size: 12.5px; line-height: 1.6">
        按分组切出独立的一条时间线（JVM 史、LLM 史各自成线），可多选叠加。
      </p>

      <div class="section" style="margin-top: 14px">
        <div class="section-head">分组</div>
        <ul class="tl-list">
          <li>
            <button class="tl-btn" :class="{ on: !selected.length }" @click="emit('select-all')">
              <Icon name="layers" :size="14" />全部
            </button>
          </li>
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
