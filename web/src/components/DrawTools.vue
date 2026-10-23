<script setup>
// 专题画布的编辑卡（2026-10-13）：选中图形或连线时右边出来，改字、悬停说明、样式。
// 放图形在左边的图形栏（拖进来），连线 / 换形状 / 删除在右键菜单里。
//
// 改动都即时落到画布上（父组件写 layout 补丁，300ms 节流写盘），所以这里没有「保存」按钮——
// 和拖动一样，写进去的就是画布本身。
import { computed, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import { SHAPE_COLORS, SHAPE_KINDS } from '../canvas/drawing.js'
import { PALETTE } from '../canvas/shapes.js'

const props = defineProps({
  shape: { type: Object, default: null },     // { sid, kind, text, tip, style, node, nodeName }
  link: { type: Object, default: null },      // { lid, text, style }
})
const emit = defineEmits(['patch-shape', 'delete-shape', 'patch-link', 'delete-link'])
const swatch = (key) => PALETTE.find((c) => c.key === key)?.line || 'transparent'
const kindName = computed(() => SHAPE_KINDS.find((k) => k.kind === props.shape?.kind)?.name || '')

// 打字时别每个字都重画：停 250ms 再发。**按「哪个对象的哪个字段」各攒各的**——
// 共用一个计时器的话，文字框还没发出去就去改悬停说明，前一个字段会被吞掉。
// 换选中对象时立刻发掉，不能丢。
const queued = new Map()      // `${事件}|${id}|${字段}` → [事件, id, 补丁]
let timer = null
function flushTyping() {
  clearTimeout(timer)
  timer = null
  for (const [event, id, patch] of queued.values()) emit(event, id, patch)
  queued.clear()
}
function later(event, id, key, value) {
  queued.set(`${event}|${id}|${key}`, [event, id, { [key]: value }])
  clearTimeout(timer)
  timer = setTimeout(flushTyping, 250)
}
watch(() => [props.shape?.sid, props.link?.lid], flushTyping)

function shapeField(key, value) {
  later('patch-shape', props.shape.sid, key, value)
}
function shapeStyle(key, value) {
  emit('patch-shape', props.shape.sid, { style: { ...(props.shape.style || {}), [key]: value } })
}
function linkField(key, value) {
  later('patch-link', props.link.lid, key, value)
}

</script>

<template>
  <div v-if="shape" class="draw-card" data-shape-card>
    <div class="draw-card-head">
      <b>{{ kindName }}</b>
      <span v-if="shape.node" class="dim">· 已升级为「{{ shape.nodeName || shape.node }}」</span>
      <button class="icon-btn ghost tiny" title="删掉（连同它套着的小框和连着它的线）" data-act="delete-shape"
              @click="emit('delete-shape', shape.sid)"><Icon name="trash" :size="13" /></button>
    </div>
    <label class="fld"><span>文字</span>
      <textarea :value="shape.text" data-shape-text rows="2" :placeholder="shape.nodeName || ''"
                @input="shapeField('text', $event.target.value)" /></label>
    <label class="fld"><span>悬停说明</span>
      <textarea :value="shape.tip" data-shape-tip rows="3" placeholder="鼠标放上去时显示"
                @input="shapeField('tip', $event.target.value)" /></label>
    <div class="draw-row">
      <span class="dim">颜色</span>
      <button v-for="c in SHAPE_COLORS" :key="c || 'auto'" class="draw-swatch"
              :class="{ on: (shape.style?.fill || null) === c }" :style="{ background: c ? swatch(c) : 'transparent' }"
              :title="c || '跟主题走'" :data-color="c || 'auto'" @click="shapeStyle('fill', c)" />
    </div>
    <div class="draw-row">
      <span class="dim">字号</span>
      <input type="number" min="9" max="40" :value="shape.style?.size || ''" placeholder="默认" class="draw-size"
             @change="shapeStyle('size', Number($event.target.value) || null)" />
      <label class="draw-bold"><input type="checkbox" :checked="!!shape.style?.bold"
             @change="shapeStyle('bold', $event.target.checked || null)" />粗体</label>
    </div>
  </div>

  <div v-else-if="link" class="draw-card" data-link-card>
    <div class="draw-card-head">
      <b>连线</b>
      <button class="icon-btn ghost tiny" title="删掉这条线" data-act="delete-link"
              @click="emit('delete-link', link.lid)"><Icon name="trash" :size="13" /></button>
    </div>
    <label class="fld"><span>线上的字</span>
      <input :value="link.text" data-link-text placeholder="比如：读写、演化为" @input="linkField('text', $event.target.value)" />
    </label>
    <div class="draw-row">
      <span class="dim">颜色</span>
      <button v-for="c in SHAPE_COLORS" :key="c || 'auto'" class="draw-swatch"
              :class="{ on: (link.style?.stroke || null) === c }" :style="{ background: c ? swatch(c) : 'transparent' }"
              :title="c || '跟主题走'" @click="emit('patch-link', link.lid, { style: { ...(link.style || {}), stroke: c } })" />
    </div>
    <p class="dim" style="font-size: 11.5px; margin: 6px 0 0">两端都是知识点的线，以后可以写成 md 里的关系（下一步）。</p>
  </div>
</template>
