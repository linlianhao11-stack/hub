<template>
  <!-- 隐藏的测量容器：渲染所有 tab 用于获取宽度 -->
  <div
    ref="measureRef"
    class="overflow-tabs-measure"
    aria-hidden="true"
  >
    <button
      v-for="tab in tabs"
      :key="'m-' + tab.key"
      ref="measureTabRefs"
      class="overflow-tab"
      type="button"
    >
      {{ tab.label }}
    </button>
  </div>

  <!-- 主容器 -->
  <div
    ref="containerRef"
    class="overflow-tabs"
    role="tablist"
  >
    <!-- 可见的 tab -->
    <button
      v-for="tab in visibleTabs"
      :key="tab.key"
      :class="['overflow-tab', { active: modelValue === tab.key }]"
      type="button"
      role="tab"
      :aria-selected="modelValue === tab.key"
      @click="$emit('update:modelValue', tab.key)"
    >
      {{ tab.label }}
    </button>

    <!-- 更多按钮 + 下拉菜单容器 -->
    <div v-if="allOverflowTabs.length > 0" class="overflow-more-wrapper">
      <button
        ref="moreBtnRef"
        :class="['overflow-tab', 'overflow-more-btn', { active: isSelectedInOverflow }]"
        type="button"
        :aria-expanded="menuOpen"
        @click="toggleMenu"
      >
        {{ moreBtnLabel }}
      </button>

      <div
        v-if="menuOpen"
        class="overflow-more-menu"
        role="menu"
      >
        <template v-for="tab in allOverflowTabs" :key="'o-' + tab.key">
          <div v-if="tab.divider" class="overflow-more-divider" />
          <button
            v-else
            :class="['overflow-more-item', { active: modelValue === tab.key }]"
            type="button"
            role="menuitem"
            @click="selectOverflowTab(tab.key)"
          >
            {{ tab.label }}
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  tabs: {
    type: Array,
    required: true
    // [{ key: string, label: string }]
  },
  modelValue: {
    type: String,
    default: ''
  },
  minVisible: {
    type: Number,
    default: 5
  },
  maxVisible: {
    type: Number,
    default: Infinity
  },
  extraTabs: {
    type: Array,
    default: () => []
    // 永远放在"更多"下拉里的 tab（如寄售仓），用分隔线与溢出 tab 隔开
  }
})

const emit = defineEmits(['update:modelValue'])

// DOM 引用
const containerRef = ref(null)
const measureRef = ref(null)
const measureTabRefs = ref([])
const moreBtnRef = ref(null)

// 状态
const menuOpen = ref(false)
const tabWidths = ref([])       // 每个 tab 的缓存宽度
const visibleCount = ref(0)     // 当前可见 tab 数量

// "更多"按钮的预估宽度
const MORE_BTN_WIDTH = 72

// 计算可见和溢出的 tab 列表
const effectiveVisible = computed(() => Math.min(visibleCount.value, props.maxVisible))
const visibleTabs = computed(() => props.tabs.slice(0, effectiveVisible.value))
const overflowTabs = computed(() => props.tabs.slice(effectiveVisible.value))

// 合并溢出 + extraTabs
const allOverflowTabs = computed(() => {
  const items = [...overflowTabs.value]
  if (props.extraTabs.length > 0) {
    if (items.length > 0) items.push({ key: '__divider__', label: '', divider: true })
    items.push(...props.extraTabs)
  }
  return items
})

// 当前选中 tab 是否在溢出菜单中
const isSelectedInOverflow = computed(() =>
  allOverflowTabs.value.some(t => t.key === props.modelValue && !t.divider)
)

// "更多"按钮的标签文本
const moreBtnLabel = computed(() => {
  if (isSelectedInOverflow.value) {
    const selected = allOverflowTabs.value.find(t => t.key === props.modelValue)
    return `更多 (${selected?.label}) ▾`
  }
  return '更多 ▾'
})

/**
 * 测量所有 tab 的宽度并缓存
 */
function measureTabs() {
  const els = measureTabRefs.value
  if (!els || els.length === 0) return

  tabWidths.value = els.map(el => {
    const rect = el.getBoundingClientRect()
    return rect.width
  })
}

/**
 * 根据容器宽度计算可显示的 tab 数量
 */
function recalculate() {
  if (!containerRef.value || tabWidths.value.length === 0) return

  const containerWidth = containerRef.value.getBoundingClientRect().width
  const totalTabs = props.tabs.length

  // 先尝试全部放下（不需要"更多"按钮）
  const gap = 4 // gap 值与 CSS 保持一致
  let totalWidth = 0
  let allFit = true

  for (let i = 0; i < totalTabs; i++) {
    totalWidth += tabWidths.value[i] + (i > 0 ? gap : 0)
    if (totalWidth > containerWidth) {
      allFit = false
      break
    }
  }

  if (allFit && totalTabs <= props.maxVisible && props.extraTabs.length === 0) {
    visibleCount.value = totalTabs
    return
  }

  // 需要"更多"按钮，减去其宽度
  const available = containerWidth - MORE_BTN_WIDTH - gap
  let accumulated = 0
  let count = 0

  for (let i = 0; i < totalTabs; i++) {
    const w = tabWidths.value[i] + (i > 0 ? gap : 0)
    if (accumulated + w > available) break
    accumulated += w
    count++
  }

  // minVisible 是软约束，maxVisible 是硬上限
  const clamped = Math.min(count, props.maxVisible)
  visibleCount.value = Math.max(clamped, Math.min(props.minVisible, clamped))
}

// 切换下拉菜单
function toggleMenu() {
  menuOpen.value = !menuOpen.value
}

// 从溢出菜单选择 tab
function selectOverflowTab(key) {
  emit('update:modelValue', key)
  menuOpen.value = false
}

// 点击外部关闭菜单
function handleClickOutside(e) {
  if (!menuOpen.value) return
  if (moreBtnRef.value?.contains(e.target)) return
  const menu = containerRef.value?.querySelector('.overflow-more-menu')
  if (menu?.contains(e.target)) return
  menuOpen.value = false
}

// 监听菜单开关，动态绑定/解绑点击外部事件
watch(menuOpen, (open) => {
  if (open) {
    document.addEventListener('mousedown', handleClickOutside)
  } else {
    document.removeEventListener('mousedown', handleClickOutside)
  }
})

// ResizeObserver 实例
let resizeObserver = null

onMounted(async () => {
  await nextTick()
  measureTabs()
  recalculate()

  // 监听容器尺寸变化
  if (containerRef.value) {
    resizeObserver = new ResizeObserver(() => {
      recalculate()
    })
    resizeObserver.observe(containerRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  document.removeEventListener('mousedown', handleClickOutside)
})

// 深度监听 tabs / extraTabs 变化时重新测量
watch(
  [() => props.tabs, () => props.extraTabs],
  async () => {
    await nextTick()
    measureTabs()
    recalculate()
  },
  { deep: true }
)
</script>

<style scoped>
/* 隐藏的测量容器 */
.overflow-tabs-measure {
  visibility: hidden;
  position: absolute;
  height: 0;
  overflow: hidden;
  display: flex;
  gap: 4px;
}

/* 主容器 */
.overflow-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  position: relative;
  flex: 1;
  min-width: 0;
}

/* Tab 按钮通用样式 — 轻量 pill，接近文字链接风格 */
.overflow-tab {
  height: 26px;
  padding: 0 8px;
  border-radius: var(--r-sm, 4px);
  font-size: 13px;
  font-weight: 400;
  white-space: nowrap;
  border: none;
  cursor: pointer;
  background: transparent;
  color: var(--muted-foreground);
  font-family: inherit;
  transition: all var(--duration-fast);
}

.overflow-tab:hover {
  background: var(--elevated);
}

/* 选中态 — 轻量高亮 */
.overflow-tab.active {
  background: var(--primary-muted);
  color: var(--primary);
  font-weight: 500;
}

/* "更多"按钮选中态覆盖（溢出中有选中项时） */
.overflow-more-btn.active {
  background: transparent;
  color: var(--primary);
  font-weight: 600;
}

.overflow-more-btn.active:hover {
  background: var(--elevated);
}

/* 更多按钮 + 下拉的定位容器 */
.overflow-more-wrapper {
  position: relative;
}

/* 下拉菜单 */
.overflow-more-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  min-width: 140px;
  width: max-content;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--sh-md);
  padding: 4px;
  z-index: 50;
}

/* 下拉菜单项 */
.overflow-more-item {
  display: block;
  width: 100%;
  padding: 8px 12px;
  font-size: 13px;
  text-align: left;
  border: none;
  border-radius: var(--r-sm);
  cursor: pointer;
  background: transparent;
  color: var(--text);
  font-family: inherit;
}

.overflow-more-item:hover {
  background: var(--elevated);
}

.overflow-more-item.active {
  color: var(--primary);
  font-weight: 500;
}

.overflow-more-divider {
  height: 1px;
  margin: 4px 8px;
  background: var(--border-light);
}
</style>
