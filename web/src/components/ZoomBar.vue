<script setup>
// 右下角缩放簇：画布类工具的通用位置，点百分比回到 100%。
import Icon from '../ui/Icon.vue'

// overview：宫殿总图开没开；null = 这块画布没有总图（只有全局图有），按钮不出现
defineProps({ zoom: { type: Number, default: 1 }, map: { type: Boolean, default: true },
              overview: { type: Boolean, default: null } })
const emit = defineEmits(['zoom-in', 'zoom-out', 'reset', 'fit', 'toggle-map', 'toggle-overview'])
</script>

<template>
  <div class="float zoom">
    <button class="icon-btn" title="缩小" @click="emit('zoom-out')"><Icon name="minus" :size="16" /></button>
    <button class="zoom-val" title="回到 100%" @click="emit('reset')">{{ Math.round(zoom * 100) }}%</button>
    <button class="icon-btn" title="放大" @click="emit('zoom-in')"><Icon name="plus" :size="16" /></button>
    <span class="sep" />
    <button class="icon-btn" title="适应窗口（F）" @click="emit('fit')"><Icon name="fit" :size="16" /></button>
    <button v-if="overview !== null" class="zoom-val" :class="{ active: overview }" data-act="palace-overview"
            :title="overview ? '退出总图：回到按缩放自动折叠' : '宫殿总图：只看岛和岛之间的线（G）'"
            @click="emit('toggle-overview')">总图</button>
    <button class="icon-btn" :class="{ active: map }" :title="map ? '收起小地图' : '显示小地图'"
            @click="emit('toggle-map')"><Icon name="map" :size="16" /></button>
  </div>
</template>
