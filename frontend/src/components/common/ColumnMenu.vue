<template>
  <div class="inline-flex" ref="btnRef">
    <button @click.stop="toggle" class="col-selector-btn" title="列设置">
      <Columns3 :size="14" />
    </button>
    <teleport to="body">
      <div v-if="open" ref="menuRef" class="col-menu-dropdown" :style="menuStyle" @click.stop>
        <template v-for="(label, key) in labels" :key="key">
          <button v-if="key !== pinned"
            type="button"
            @click="$emit('toggle', key)"
            class="col-menu-item">
            <span class="col-menu-check">{{ visible[key] ? '\u2713' : '' }}</span>
            <span>{{ label }}</span>
          </button>
        </template>
        <div class="col-menu-divider">
          <button type="button" @click="$emit('reset')" class="col-menu-item col-menu-reset">重置列</button>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Columns3 } from 'lucide-vue-next'

defineProps({
  labels: { type: Object, required: true },
  visible: { type: Object, required: true },
  pinned: { type: String, default: '' },
})

defineEmits(['toggle', 'reset'])

const open = ref(false)
const btnRef = ref(null)
const menuRef = ref(null)
const menuStyle = ref({})

const toggle = () => {
  open.value = !open.value
}

const updatePos = async () => {
  await nextTick()
  if (!btnRef.value || !menuRef.value) return
  const r = btnRef.value.getBoundingClientRect()
  const mw = menuRef.value.offsetWidth
  const mh = menuRef.value.offsetHeight
  const vh = window.innerHeight
  const vw = window.innerWidth
  const margin = 8

  // 垂直定位：优先下方，不够则上方，都不够则贴顶+滚动
  let top
  const spaceBelow = vh - r.bottom
  const spaceAbove = r.top
  if (spaceBelow >= mh + margin) {
    top = r.bottom + 4
  } else if (spaceAbove >= mh + margin) {
    top = r.top - mh - 4
  } else {
    top = margin
  }
  // 强制不超出视口
  top = Math.max(margin, Math.min(top, vh - mh - margin))

  // 水平定位：右对齐按钮，不超出左右边界
  const left = Math.min(Math.max(margin, r.right - mw), vw - mw - margin)

  // 最大高度 = 视口 - 上下留白
  const maxH = vh - margin * 2

  menuStyle.value = { top: `${top}px`, left: `${left}px`, maxHeight: `${maxH}px` }
}

watch(open, v => { if (v) updatePos() })

const onDocClick = e => {
  if (open.value && btnRef.value && !btnRef.value.contains(e.target) && menuRef.value && !menuRef.value.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
.col-menu-dropdown {
  position: fixed;
  z-index: 50;
  background: var(--surface);
  border-radius: var(--r-lg);
  box-shadow: var(--sh-md);
  border: 1px solid var(--border);
  padding: 6px;
  min-width: 140px;
  user-select: none;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.col-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--r-sm);
  cursor: pointer;
  font-size: 13px;
  user-select: none;
  background: none;
  border: none;
  width: 100%;
  text-align: left;
  color: var(--text);
  transition: background var(--duration-fast);
}
.col-menu-item:hover {
  background: var(--elevated);
}
.col-menu-check {
  width: 16px;
  text-align: center;
  font-size: 13px;
  flex-shrink: 0;
}
.col-menu-reset {
  color: var(--text-muted);
}
.col-menu-divider {
  border-top: 1px solid var(--border);
  margin-top: 4px;
  padding-top: 4px;
}
</style>
