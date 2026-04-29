<template>
  <div
    class="segmented-control"
    :class="[sizeClass, variantClass]"
    role="radiogroup"
  >
    <div
      v-if="variant === 'indicator'"
      class="segmented-indicator"
      :style="indicatorStyle"
    />
    <button
      v-for="(option, index) in options"
      :key="option.key"
      :ref="(el) => setItemRef(el, index)"
      type="button"
      role="radio"
      :aria-checked="modelValue === option.key"
      :class="['segmented-item', { active: modelValue === option.key }]"
      @click="$emit('update:modelValue', option.key)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'

const props = defineProps({
  options: {
    type: Array,
    required: true
    // [{ key: string, label: string }]
  },
  modelValue: {
    type: [String, Number],
    required: true
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'sm', 'xs'].includes(v)
  },
  variant: {
    type: String,
    default: 'indicator',
    validator: (v) => ['indicator', 'pill'].includes(v)
  }
})

const sizeClass = computed(() => {
  if (props.size === 'sm') return 'segmented-sm'
  if (props.size === 'xs') return 'segmented-xs'
  return ''
})

const variantClass = computed(() => {
  return props.variant === 'pill' ? 'segmented-pill' : ''
})

defineEmits(['update:modelValue'])

const itemRefs = ref([])

function setItemRef(el, index) {
  if (el) {
    itemRefs.value[index] = el
  }
}

const indicatorStyle = computed(() => {
  const activeIndex = props.options.findIndex(o => o.key === props.modelValue)
  if (activeIndex < 0 || !itemRefs.value[activeIndex]) {
    return { opacity: 0 }
  }
  const el = itemRefs.value[activeIndex]
  return {
    width: `${el.offsetWidth}px`,
    transform: `translateX(${el.offsetLeft - 3}px)`,
    opacity: 1
  }
})

/* 确保首次渲染后计算指示器位置 */
onMounted(() => {
  nextTick()
})

/* options 变更时重新触发 */
watch(() => props.options, () => {
  nextTick()
})
</script>

<style scoped>
.segmented-control {
  position: relative;
  display: inline-flex;
  align-items: center;
  background: var(--elevated);
  border-radius: var(--radius-lg);
  padding: 3px;
  height: 40px;
  gap: 0;
}

.segmented-indicator {
  position: absolute;
  top: 3px;
  left: 0;
  height: calc(100% - 6px);
  background: var(--surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  transition: transform var(--duration-normal) var(--ease-out-expo),
              width var(--duration-normal) var(--ease-out-expo);
  pointer-events: none;
  z-index: 0;
}

.segmented-item {
  position: relative;
  z-index: 1;
  padding: 0 16px;
  height: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  background: none;
  border: none;
  cursor: pointer;
  white-space: nowrap;
  transition: color var(--duration-fast);
  letter-spacing: -0.01em;
  border-radius: var(--radius-md);
  user-select: none;
  -webkit-user-select: none;
}

.segmented-item:hover {
  color: var(--text-secondary);
}

.segmented-item.active {
  color: var(--text);
}

/* === Size variants === */
.segmented-sm {
  height: 32px;
}
.segmented-sm .segmented-item {
  padding: 0 12px;
  font-size: 12px;
}

.segmented-xs {
  height: 26px;
  border-radius: 9999px;
  padding: 2px;
}
.segmented-xs .segmented-indicator {
  top: 2px;
  height: calc(100% - 4px);
  border-radius: 9999px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08), 0 0 0 0.5px rgba(0,0,0,.04);
}
.segmented-xs .segmented-item {
  padding: 0 10px;
  font-size: 12px;
  border-radius: 9999px;
}

/* === Pill variant — 无容器背景、无滑块指示器 === */
.segmented-pill {
  background: transparent;
  padding: 0;
  gap: 4px;
  height: auto;
}

.segmented-pill .segmented-item {
  padding: 4px 12px;
  border-radius: var(--radius-md);
  transition: color var(--duration-fast), background var(--duration-fast);
}

.segmented-pill .segmented-item:hover {
  color: var(--text);
  background: var(--elevated);
}

.segmented-pill .segmented-item.active {
  color: var(--primary);
  background: var(--primary-muted);
  font-weight: 600;
}

/* Pill + size 组合 */
.segmented-pill.segmented-xs .segmented-item {
  padding: 2px 10px;
  font-size: 12px;
}

.segmented-pill.segmented-sm .segmented-item {
  padding: 4px 12px;
  font-size: 13px;
}
</style>
