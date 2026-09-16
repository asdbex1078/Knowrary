<script setup>
/**
 * 晨间简报：当天第一次打开时弹一次，回答"今天干什么、大概多久"。
 *
 * 数据全部来自今日清单（`/api/coach/today`），**一个新接口都不加、一次 LLM 都不调**——
 * 缺的从来不是算不算得出来，而是**开场就看见**。不然要先点开面板才知道今天有事。
 *
 * 一天只弹一次（localStorage 记当天日期），可以关掉，也能直接从上面动手。
 */
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  today: { type: Object, default: null },
})
const emit = defineEmits(['close', 'start', 'quiz'])

const KIND_NAME = { wrong: '错题', due: '待复习', unbuilt: '要新建', shell: '写正文', inbox: '上画布' }

const counts = computed(() => props.today?.counts || {})
const build = computed(() => (props.today?.items || []).filter((i) => ['unbuilt', 'shell'].includes(i.kind)))
const review = computed(() => (props.today?.items || []).filter((i) => ['due', 'wrong'].includes(i.kind)))
const hours = computed(() => props.today?.estimate_hours || 0)
const empty = computed(() => !(props.today?.items || []).length)
</script>

<template>
  <div class="brief-mask" @click.self="emit('close')">
    <div class="brief">
      <div class="brief-head">
        <Icon name="sun" :size="16" />
        <b>今天</b>
        <span class="dim">{{ today?.generated_at }}</span>
        <button class="icon-btn ghost tiny" style="margin-left: auto" title="知道了" @click="emit('close')">
          <Icon name="x" :size="14" />
        </button>
      </div>

      <div v-if="empty" class="dim" style="line-height: 1.8">
        今天没有到期的复习，计划里的点也都建出来了。<br>
        想往前走就去项目面板加几个点，或者直接开一段对话。
      </div>

      <template v-else>
        <div class="brief-nums">
          <span v-for="(n, k) in counts" :key="k" class="chip" :class="k === 'unbuilt' ? 'm-unbuilt' : 'm-due'">
            {{ KIND_NAME[k] || k }} {{ n }}
          </span>
          <span v-if="hours" class="dim">预计 {{ hours }} 小时</span>
        </div>

        <div v-if="build.length" class="brief-line">
          <span class="lb">建设</span>
          <span class="path">
            <template v-for="(it, i) in build" :key="it.id">
              <a class="link" @click="emit('start', it)">{{ it.name }}</a><span
                 v-if="i < build.length - 1" class="dim"> → </span>
            </template>
          </span>
        </div>

        <div v-if="review.length" class="brief-line">
          <span class="lb">复习</span>
          <span class="path">{{ review.map((r) => r.name).join('、') }}</span>
        </div>

        <div class="brief-acts">
          <button v-if="review.length" class="btn primary" @click="emit('quiz', review.map((r) => r.id))">
            <Icon name="play" :size="14" />开始测验（{{ review.length }}）
          </button>
          <button v-if="build.length" class="btn" @click="emit('start', build[0])">
            <Icon name="plus" :size="14" />先建「{{ build[0].name }}」
          </button>
          <button class="btn subtle" @click="emit('close')">待会儿</button>
        </div>
      </template>
    </div>
  </div>
</template>
