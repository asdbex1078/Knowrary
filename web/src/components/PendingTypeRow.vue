<script setup>
/**
 * 一个还没登记的关系类型：导入时模型提的，或者 md 里已经在用却没登记的。欠账面板里一行。
 *
 * 登记前能改族、填互逆类型、设无方向——模型一般只给名字和族，很少想到互逆
 * （「衍生出」↔「衍生自」）。填了互逆就两个一起登记，反向那个归一到正向。
 * md 里在用的那种没有「驳回」：边已经写在 md 里了，驳回也改变不了它是弱关联。
 */
import { ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  item: { type: Object, required: true },
  families: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  rejected: { type: Boolean, default: false },
})
const emit = defineEmits(['decide', 'open-article'])

const family = ref(props.families.includes(props.item.family) ? props.item.family : '')
const inverse = ref('')
const symmetric = ref(false)

function register() {
  emit('decide', { name: props.item.name, action: 'register', family: family.value,
                   inverse: symmetric.value ? '' : inverse.value.trim(), symmetric: symmetric.value })
}
</script>

<template>
  <li class="pend-row pend-type-row" :class="{ rejected }" :data-type="item.name">
    <div class="pend-edge">
      <b>{{ item.name }}</b>
      <span v-if="item.kind === 'md'" class="chip m-due" title="md 里已经在用，但关系类型表里没有：现在被当成「弱关联」画">
        md 里在用 {{ item.uses }} 条</span>
      <span v-else class="chip m-unbuilt">模型提议</span>
    </div>
    <p v-if="item.kind === 'md' && item.samples?.length" class="pend-note dim">{{ item.samples.join('；') }}</p>
    <p v-if="item.why" class="pend-note dim">为什么要新类型：{{ item.why }}</p>
    <div v-if="item.origin?.source" class="pend-meta dim">
      <a v-if="item.article" href="#" :title="`读原文：${item.article}`"
         @click.prevent="emit('open-article', { path: item.article })"><Icon name="book" :size="11" />《{{ item.origin.source }}》</a>
      <span v-else>《{{ item.origin.source }}》</span>
    </div>
    <div v-if="!rejected" class="pend-type-form">
      <label>族
        <select v-model="family" :disabled="busy" data-field="family">
          <option value="" disabled>选一个</option>
          <option v-for="f in families" :key="f" :value="f">{{ f }}</option>
        </select>
      </label>
      <label title="反方向叫什么（比如「衍生出」的互逆是「衍生自」）；填了就一起登记。不填也行">互逆
        <input v-model="inverse" type="text" :disabled="busy || symmetric" placeholder="可不填" data-field="inverse">
      </label>
      <label class="pend-sym" title="无方向：A 对 B 和 B 对 A 是一回事（像「对比」）">
        <input v-model="symmetric" type="checkbox" :disabled="busy" data-field="symmetric">无方向
      </label>
    </div>
    <div class="pend-acts">
      <template v-if="rejected">
        <button class="btn subtle tiny" :disabled="busy" @click="emit('decide', { name: item.name, action: 'restore' })">
          <Icon name="undo" :size="12" />撤销驳回</button>
      </template>
      <template v-else>
        <button class="btn primary tiny" :disabled="busy || !family" data-act="register"
                title="写进库根的 relation-types.json（写前备份）" @click="register">
          <Icon name="check" :size="12" />登记</button>
        <button v-if="item.kind !== 'md'" class="btn subtle tiny" :disabled="busy" data-act="reject"
                title="驳回会留一笔：以后别的文章再提同一个，不再进待审"
                @click="emit('decide', { name: item.name, action: 'reject' })">
          <Icon name="x" :size="12" />驳回</button>
      </template>
    </div>
  </li>
</template>
