<template>
  <button
    :class="['icon-btn', variantClass, sizeClass]"
    :disabled="disabled"
    :aria-label="ariaLabel"
    v-bind="$attrs"
  >
    <component :is="iconComponent" :size="iconSize" />
  </button>
</template>

<script setup>
import { computed } from 'vue'
import {
  Plus, Trash2, Settings, Eye, Copy, X,
  Download, Upload, Printer, Edit, Filter,
  MoreHorizontal, RefreshCw, Search, Save,
  ChevronDown, ChevronRight, ChevronLeft, ChevronUp,
  Columns3, ExternalLink, Pencil, RotateCcw,
} from 'lucide-vue-next'

const icons = {
  Plus, Trash2, Settings, Eye, Copy, X,
  Download, Upload, Printer, Edit, Filter,
  MoreHorizontal, RefreshCw, Search, Save,
  ChevronDown, ChevronRight, ChevronLeft, ChevronUp,
  Columns3, ExternalLink, Pencil, RotateCcw,
}

defineOptions({ inheritAttrs: false })

const props = defineProps({
  icon: { type: String, required: true },
  variant: {
    type: String,
    default: 'ghost',
    validator: (v) => ['ghost', 'secondary', 'primary', 'danger'].includes(v)
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'sm', 'xs'].includes(v)
  },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '' }
})

const variantClass = computed(() => `icon-btn-${props.variant}`)

const sizeClass = computed(() => {
  if (props.size === 'xs') return 'icon-btn-xs'
  if (props.size === 'sm') return 'icon-btn-sm'
  return ''
})

const iconSize = computed(() => {
  if (props.size === 'xs') return 12
  if (props.size === 'sm') return 14
  return 18
})

function toPascalCase(str) {
  return str
    .replace(/(^|[-_])(\w)/g, (_, _sep, c) => c.toUpperCase())
    .replace(/^\w/, (c) => c.toUpperCase())
}

const iconComponent = computed(() => {
  const name = toPascalCase(props.icon)
  return icons[name] || null
})
</script>

<style scoped>
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--r-md, 6px);
  border: none;
  cursor: pointer;
  transition: all var(--duration-fast);
  flex-shrink: 0;
  padding: 0;
}

.icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 尺寸 */
.icon-btn-sm {
  width: 28px;
  height: 28px;
  border-radius: var(--r-sm, 4px);
}

.icon-btn-xs {
  width: 22px;
  height: 22px;
  border-radius: var(--r-sm, 4px);
}

/* Ghost — 无背景 */
.icon-btn-ghost {
  background: transparent;
  color: var(--text-secondary);
}
.icon-btn-ghost:hover:not(:disabled) {
  background: var(--elevated);
  color: var(--text);
}

/* Secondary — 浅底+边框 */
.icon-btn-secondary {
  background: var(--secondary);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.icon-btn-secondary:hover:not(:disabled) {
  background: var(--surface-hover);
  color: var(--text);
}

/* Primary — 主色底 */
.icon-btn-primary {
  background: var(--primary);
  color: var(--primary-foreground);
}
.icon-btn-primary:hover:not(:disabled) {
  background: var(--primary-hover);
}

/* Danger — subtle 红底 */
.icon-btn-danger {
  background: var(--error-subtle);
  color: var(--error-emphasis);
  border: 1px solid var(--error-subtle);
}
.icon-btn-danger:hover:not(:disabled) {
  filter: brightness(0.97);
}
</style>
