<script setup>
// 快捷键与操作说明。原来是常驻画布底部的一长行小字，一直占着高度。
import Icon from '../ui/Icon.vue'

defineProps({ mode: { type: String, default: 'structure' } })
const emit = defineEmits(['close'])

const CANVAS = [
  ['两指滑动 / 滚轮', '平移画布'],
  ['⌘ + 滚轮 / 触控板捏合', '缩放'],
  ['拖空白', '平移画布'],
  ['⇧ + 拖空白', '框选'],
  ['右键节点', '建关系 / 只看邻居 / 移出画布'],
  ['右键域', '加总览文档、新建子簇、折叠、钉住展开、重命名'],
  ['右键空白', '新建知识点、新建簇、贴便签、贴图'],
  ['点一个域', '浮出工具条：总览文档 / 写内容 / 建关系 / 新建知识点'],
  ['右键边', '删掉这条关系（写回 md）'],
  ['拖节点进分组框', '改归属，松手 300ms 后自动保存'],
  ['拖簇卡片', '整个域连同里面的节点一起搬'],
  ['悬停 / 选中节点', '高亮它牵着的边'],
  ['点簇卡片', '放大进那个域'],
  ['点「跨组 n 束」', '展开这对分组之间的明细'],
  ['双击边', '清掉手工拐点'],
  ['左下角小地图', '点 / 拖直接跳到图的任意角落'],
]
const KEYS = [
  ['/', '聚焦搜索框'],
  ['⌘L', '给选中的知识点建立关系'],
  ['⌘Z / Ctrl+Z', '撤销布局改动'],
  ['⇧⌘Z / Ctrl+Y', '重做'],
  ['F', '适应窗口'],
  ['1 / 2', '切结构 / 历史视图'],
  ['I / D', '开合 Inbox / 欠账'],
  ['Esc', '收菜单、退关系框、退只看邻居、退聚焦'],
  ['?', '打开这个说明'],
]
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <header class="drawer-head">
        <Icon name="help" :size="15" class="head-icon" />
        <h2>操作与快捷键</h2>
        <button class="icon-btn ghost" @click="emit('close')"><Icon name="x" :size="15" /></button>
      </header>
      <div class="modal-body scroll-thin">
        <div class="section" style="margin-top: 0">
          <div class="section-head">画布</div>
          <dl class="keys">
            <template v-for="[k, v] in CANVAS" :key="k">
              <dt><kbd class="key">{{ k }}</kbd></dt><dd>{{ v }}</dd>
            </template>
          </dl>
        </div>
        <div class="section">
          <div class="section-head">键盘</div>
          <dl class="keys">
            <template v-for="[k, v] in KEYS" :key="k">
              <dt><kbd class="key">{{ k }}</kbd></dt><dd>{{ v }}</dd>
            </template>
          </dl>
        </div>
        <p v-if="mode === 'history'" class="dim" style="font-size: 12px; margin-top: 16px; line-height: 1.6">
          历史视图：X 轴是年份，Y 轴是泳道；金色流动虚线是「被激活」的跨代关系；拖底部滑块按年回放。
          这个视图只是浏览，不会改结构布局。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; z-index: 200;
  background: rgba(12, 20, 30, .34);
  backdrop-filter: blur(3px);
  display: grid; place-items: center;
  animation: mask-in .15s ease;
}
@keyframes mask-in { from { opacity: 0 } to { opacity: 1 } }
.modal {
  width: min(520px, 92vw); max-height: 80vh;
  display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-lg); box-shadow: var(--sh-3);
  animation: modal-in .18s cubic-bezier(.22, .61, .36, 1);
}
@keyframes modal-in { from { opacity: 0; transform: translateY(8px) scale(.98) } to { opacity: 1 } }
.modal-body { padding: 16px 18px 20px; overflow: auto; }
.modal .drawer-head > .icon-btn:last-child { margin-left: auto; }
</style>
