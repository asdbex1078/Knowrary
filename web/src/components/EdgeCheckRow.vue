<script setup>
/**
 * 一条已有边的校验结论（review 角色给的）。检查器和欠账面板共用。
 *
 * 按结论给一键修：方向反了 →「换方向」、类型不对 →「改成 X」、不成立 →「删掉」，都走写回层、写前备份；
 * 觉得它看走眼了就「忽略」，这条边没改过就不再报。拿不准的只给「忽略」。
 */
import Icon from '../ui/Icon.vue'

defineProps({
  item: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['fix', 'goto'])

const SAY = { reverse: '方向反了', retype: '类型不对', drop: '这条关系不成立', unsure: '拿不准', ok: '没问题' }
const CHIP = { reverse: 'm-due', retype: 'm-due', drop: 'm-drop', unsure: 'm-unbuilt', ok: 'm-mastered' }
</script>

<template>
  <li class="pend-row edge-check-row" :class="item.verdict" :data-check="item.id">
    <div class="pend-edge">
      <span class="link" @click="emit('goto', item.source)">{{ item.source_name }}</span>
      <span class="pend-type-ro">—{{ item.type }}→</span>
      <span class="link" @click="emit('goto', item.target)">{{ item.target_name }}</span>
    </div>
    <div class="pend-meta dim">
      <span class="chip" :class="CHIP[item.verdict]">{{ SAY[item.verdict] || item.verdict }}
        <template v-if="item.verdict === 'retype' && item.suggest">→ {{ item.suggest }}</template></span>
      <span v-if="item.model" :title="item.at">{{ item.model }}</span>
    </div>
    <p v-if="item.why" class="pend-note dim">依据：{{ item.why }}</p>
    <div class="pend-acts">
      <button v-if="item.verdict === 'reverse'" class="btn primary tiny" :disabled="busy" data-act="reverse"
              title="删掉 md 里原来那一行，在另一头写一行反方向的" @click="emit('fix', { id: item.id, action: 'reverse' })">
        <Icon name="rotate" :size="12" />换方向</button>
      <button v-if="item.verdict === 'retype'" class="btn primary tiny" :disabled="busy" data-act="retype"
              @click="emit('fix', { id: item.id, action: 'retype', relation: item.suggest })">
        <Icon name="check" :size="12" />改成「{{ item.suggest }}」</button>
      <button v-if="item.verdict === 'drop'" class="btn tiny danger" :disabled="busy" data-act="drop"
              title="从 md 里删掉这一行（写前备份）" @click="emit('fix', { id: item.id, action: 'drop' })">
        <Icon name="trash" :size="12" />删掉</button>
      <button class="btn subtle tiny" :disabled="busy" data-act="ignore"
              title="它看走眼了：不改，这条边没动过就不再报" @click="emit('fix', { id: item.id, action: 'ignore' })">忽略</button>
    </div>
  </li>
</template>
