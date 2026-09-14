<script setup>
// 右下角缩放簇：画布类工具的通用位置，点百分比回到 100%。
import Icon from '../ui/Icon.vue'

defineProps({ zoom: { type: Number, default: 1 }, map: { type: Boolean, default: true } })
const emit = defineEmits(['zoom-in', 'zoom-out', 'reset', 'fit', 'toggle-map'])
</script>

<template>
  <div class="float zoom">
    <button class="icon-btn" title="缩小" @click="emit('zoom-out')"><Icon name="minus" :size="16" /></button>
    <button class="zoom-val" title="回到 100%" @click="emit('reset')">{{ Math.round(zoom * 100) }}%</button>
    <button class="icon-btn" title="放大" @click="emit('zoom-in')"><Icon name="plus" :size="16" /></button>
    <span class="sep" />
    <button class="icon-btn" title="适应窗口（F）" @click="emit('fit')"><Icon name="fit" :size="16" /></button>
    <button class="icon-btn" :class="{ active: map }" :title="map ? '收起小地图' : '显示小地图'"
            @click="emit('toggle-map')"><Icon name="map" :size="16" /></button>
  </div>
</template>
