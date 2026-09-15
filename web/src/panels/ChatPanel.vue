<script setup>
/**
 * 对话式教练：聊着学，学完一键入库。
 *
 * **聊天是入口，图是产物。** 这一屏解决的是启动成本——想学一个点，原来要在
 * 计划 / 今日 / 建节点 / 写正文 / 出题几个面板之间跳，现在一句话就够。
 * 但知识仍然只住在 md 里：模型给的是**变更卡**，看过 diff 点「写入」才落盘（4.4）。
 *
 * 会话状态在前端（每次把整段对话发过去），服务端不持有——刷新页面不会丢一半上下文。
 */
import { computed, nextTick, ref, watch } from 'vue'
import Drawer from '../ui/Drawer.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  messages: { type: Array, default: () => [] },   // [{ role, content, tools?, cards?, streaming? }]
})
const emit = defineEmits(['send', 'stop', 'apply', 'goto', 'clear', 'close'])

// 开场白：懒人入口的关键是**不用想第一句说什么**
const STARTERS = [
  { t: '今天学什么', q: '看一眼我的今日清单和计划，告诉我今天该动手的是哪几件，按顺序说。' },
  { t: '考考我', q: '从我到期该复习的节点里挑几个考我，一次一题，我答完你再对答案。' },
  { t: '讲个概念', q: '我想搞懂：' },
  { t: '这段学完了', q: '刚才聊的东西帮我整理成知识点，提一张入库的变更卡给我看。' },
]

const text = ref('')
const box = ref(null)

const empty = computed(() => !props.messages.length)

/** 新内容进来就贴着底部。人正往回翻时不要抢滚动。 */
watch(() => props.messages.map((m) => m.content).join('|'), async () => {
  await nextTick()
  const el = box.value
  if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 260) el.scrollTop = el.scrollHeight
})

function send(q) {
  const body = (q ?? text.value).trim()
  if (!body || props.busy) return
  emit('send', body)
  text.value = ''
}

function starter(s) {
  if (s.q.endsWith('：')) { text.value = s.q; return }   // 要我补一句的，只填进输入框
  send(s.q)
}

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
}
</script>

<template>
  <Drawer side="left" title="聊天" icon="network" storage-key="chat" :default-width="420"
          @close="emit('close')">
    <template #head-actions>
      <button v-if="messages.length" class="icon-btn ghost tiny" title="清空这段对话（已经留档在 .knowrary/chat/）"
              @click="emit('clear')">
        <Icon name="trash" :size="14" />
      </button>
    </template>

    <template #default>
      <!-- 消息区自己滚，输入框钉在底下：聊到第 20 条还要往下翻才能打字是不能接受的 -->
      <div class="chat-wrap">
      <div ref="box" class="chat-box">
        <div v-if="empty" class="empty">
          <Icon name="network" :size="30" :width="1.3" />
          <span class="t">聊着学</span>
          <span class="s">问概念、让我讲、被我考；学完了说一声，我把知识点整理成变更卡，
            你看过 diff 点一下才写进图谱。</span>
        </div>

        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div v-if="m.content" class="bubble">{{ m.content }}<span v-if="m.streaming" class="caret">▍</span></div>
          <div v-else-if="m.streaming" class="bubble dim">想一下…</div>

          <!-- 调了哪些工具：摊开给人看，别让它像黑箱 -->
          <div v-for="(t, j) in (m.tools || [])" :key="`t${j}`" class="tool-line" :title="t.summary">
            <Icon name="search" :size="12" />{{ t.label }}
          </div>

          <!-- 变更卡：**这里是唯一能写 md 的地方，且必须人点** -->
          <div v-for="(c, j) in (m.cards || [])" :key="`c${j}`" class="change-card">
            <div class="cc-head">
              <Icon name="file" :size="13" />提议写入 {{ c.files.length }} 个文件
              <span v-if="c.applied" class="chip m-mastered">已写入</span>
            </div>
            <pre v-for="f in c.files" :key="f.path" class="cc-diff"><b>{{ f.path }}</b>
{{ f.diff || '（新文件）' }}</pre>
            <div v-if="!c.applied" class="cc-acts">
              <button class="btn primary tiny" :disabled="busy" @click="emit('apply', { card: c, i, j })">
                <Icon name="check" :size="13" />写入
              </button>
              <span class="dim" style="font-size: 11px">写前自动备份到 .knowrary/backup/</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="empty" class="starters">
        <button v-for="s in STARTERS" :key="s.t" class="btn subtle tiny" @click="starter(s)">{{ s.t }}</button>
      </div>

      <div class="chat-input">
        <textarea v-model="text" rows="2" placeholder="问点什么…（回车发送，Shift+回车换行）"
                  @keydown="onKey" />
        <button v-if="busy" class="icon-btn ghost" title="停" @click="emit('stop')">
          <Icon name="pause" :size="15" />
        </button>
        <button v-else class="icon-btn primary" :disabled="!text.trim()" title="发送（回车）" @click="send()">
          <Icon name="arrowRight" :size="15" />
        </button>
      </div>
      </div>
    </template>
  </Drawer>
</template>
