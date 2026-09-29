<script setup>
/**
 * 一条待审边（导入时模型没把握、连到已有节点的那些）。欠账面板和检查器共用这一行。
 *
 * 能在卡上**改类型、换方向**再采纳：模型常常是"该连，但类型选错了 / 方向反了"，
 * 驳回之后再自己去连一遍，等于把它说对的那一半也扔了。
 *
 * 状态由服务端判：ok 可以采纳；linked = md 里后来已经连上了，只给「清掉」；
 * missing = 某一端不在图里了，只给「驳回」。
 */
import { computed, ref } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  item: { type: Object, required: true },
  relationTypes: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  rejected: { type: Boolean, default: false },   // 已驳回那一栏：只给「撤销」
})
const emit = defineEmits(['decide', 'goto', 'open-article'])

const relation = ref(props.item.relation)
const swap = ref(false)
const from = computed(() => (swap.value ? props.item.target_name : props.item.source_name))
const to = computed(() => (swap.value ? props.item.source_name : props.item.target_name))
const fromId = computed(() => (swap.value ? props.item.target : props.item.source))
const toId = computed(() => (swap.value ? props.item.source : props.item.target))
const edited = computed(() => swap.value || relation.value !== props.item.relation)

function adopt() {
  emit('decide', [{ id: props.item.id, action: 'adopt',
                    ...(relation.value !== props.item.relation ? { relation: relation.value } : {}),
                    ...(swap.value ? { swap: true } : {}) }])
}
const decide = (action) => emit('decide', [{ id: props.item.id, action }])
</script>

<template>
  <li class="pend-row" :class="[item.state, { rejected }]" :data-pending="item.id">
    <div class="pend-edge">
      <span class="link" :title="`${fromId}：${swap ? item.target_desc : item.source_desc}`"
            @click="emit('goto', fromId)">{{ from }}</span>
      <select v-if="!rejected && item.state === 'ok'" v-model="relation" class="pend-type" :disabled="busy"
              title="类型选错了就在这儿改，改完再采纳">
        <optgroup v-for="g in relationTypes" :key="g.family" :label="g.family">
          <option v-for="t in g.types" :key="t" :value="t">{{ t }}</option>
        </optgroup>
      </select>
      <span v-else class="pend-type-ro">—{{ item.relation }}→</span>
      <span class="link" :title="`${toId}：${swap ? item.source_desc : item.target_desc}`"
            @click="emit('goto', toId)">{{ to }}</span>
      <button v-if="!rejected && item.state === 'ok'" class="icon-btn ghost tiny" :class="{ on: swap }"
              title="方向反了：换成从右往左" :disabled="busy" @click="swap = !swap">
        <Icon name="rotate" :size="12" />
      </button>
    </div>
    <div class="pend-meta dim">
      <span class="chip" :class="item.confidence >= 0.6 ? 'm-due' : 'm-unbuilt'"
            title="模型自己给的置信度：低于 0.8 的边不直接写，进待审">{{ Math.round(item.confidence * 100) }}%</span>
      <span v-if="item.state === 'linked'" class="chip m-mastered" title="md 里后来已经有这条了">已经连上了</span>
      <span v-else-if="item.state === 'missing'" class="chip m-drop" title="某一端节点被删了或改名了">端点不在了</span>
      <span v-if="edited" class="chip m-due">改过</span>
      <a v-if="item.article" href="#" :title="`读原文：${item.article}`"
         @click.prevent="emit('open-article', { path: item.article })">
        <Icon name="book" :size="11" />《{{ item.origin?.source }}》</a>
      <span v-else-if="item.origin?.source">《{{ item.origin.source }}》</span>
    </div>
    <p v-if="item.note" class="pend-note dim">{{ item.note }}</p>
    <div class="pend-acts">
      <template v-if="rejected">
        <button class="btn subtle tiny" :disabled="busy" @click="decide('restore')">
          <Icon name="undo" :size="12" />撤销驳回</button>
      </template>
      <template v-else>
        <button v-if="item.state === 'ok'" class="btn primary tiny" :disabled="busy" data-act="adopt" @click="adopt">
          <Icon name="check" :size="12" />{{ edited ? '改完采纳' : '采纳' }}</button>
        <button v-if="item.state === 'linked'" class="btn tiny" :disabled="busy" data-act="clear"
                @click="decide('clear')">清掉</button>
        <button class="btn subtle tiny" :disabled="busy" data-act="reject"
                title="驳回会留一笔：以后别的文章再推出同一条，不再进待审" @click="decide('reject')">
          <Icon name="x" :size="12" />驳回</button>
      </template>
    </div>
  </li>
</template>
