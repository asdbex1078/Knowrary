<script setup>
/**
 * 抽成模型谱系的预览里一个型号：勾不勾、要写哪几格、和目录冲突没改的、数字对不上原文的。
 *
 * 可疑的格子（数字在原文里没原样出现）review 判过之后，结论挂在那一格下面；
 * 判成「写错了」且给了按原文该写的值，就给一键「改成 …」——改的是这份计划，不是文件。
 */
import { computed } from 'vue'
import { summaryOf } from '../modelsPlan.js'

const props = defineProps({
  model: { type: Object, required: true },
  checks: { type: Array, default: () => [] },     // review 对这个型号的结论
})
const emit = defineEmits(['toggle', 'fix'])

const writable = computed(() => Object.keys(props.model.fields || {}).length > 0)
const rest = computed(() => Object.entries(props.model.fields || {})
  .filter(([k]) => !['发布', '参数', '激活', '上下文'].includes(k)))
const SAY = { ok: '原文确实这么说', wrong: '写错了', unsure: '拿不准' }
const CHIP = { ok: 'm-mastered', wrong: 'm-due', unsure: 'm-unbuilt' }

function checkOf(key) { return props.checks.find((c) => c.key === key) }
</script>

<template>
  <li class="model-plan" :class="{ off: !model.pick }" :data-plan="model.name">
    <label class="model-plan-head">
      <input type="checkbox" :checked="model.pick" :disabled="!writable" data-act="pick" @change="emit('toggle')">
      <b>{{ model.name }}</b>
      <span class="chip" :class="model.status === 'new' ? 'm-mastered' : 'm-due'">
        {{ model.status === 'new' ? '新增' : writable ? `补 ${Object.keys(model.fields).length} 格` : '没东西可补' }}</span>
      <span class="dim">{{ summaryOf(model.fields) }}</span>
    </label>
    <ul v-if="rest.length" class="model-plan-fields dim">
      <li v-for="[k, v] in rest" :key="k"><span class="k">{{ k }}</span>{{ v }}</li>
    </ul>
    <p v-for="c in model.conflicts" :key="'c' + c.key" class="model-plan-warn">
      ⚠ {{ c.key }}：文章写「{{ c.article }}」，目录里是「{{ c.catalog }}」（没改，要改去家族文件里改）</p>
    <div v-for="s in model.suspicious" :key="'s' + s.key" class="model-plan-warn" :data-suspect="s.key">
      ⚠ {{ s.key }}「{{ model.fields[s.key] ?? s.value }}」：{{ s.why }}
      <template v-if="checkOf(s.key)">
        <span class="chip" :class="CHIP[checkOf(s.key).verdict]">{{ SAY[checkOf(s.key).verdict] }}</span>
        <span class="dim">{{ checkOf(s.key).why }}<template v-if="checkOf(s.key).quote">——「{{ checkOf(s.key).quote }}」</template></span>
        <button v-if="checkOf(s.key).verdict === 'wrong' && checkOf(s.key).suggest && model.fields[s.key] !== checkOf(s.key).suggest"
                class="btn tiny" data-act="fix" @click="emit('fix', { key: s.key, value: checkOf(s.key).suggest })">
          改成「{{ checkOf(s.key).suggest }}」</button>
      </template>
    </div>
  </li>
</template>
