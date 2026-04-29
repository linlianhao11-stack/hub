<template>
  <button
    :class="[
      'btn',
      variantClass,
      sizeClass,
      { 'w-full': block, 'loading': loading }
    ]"
    :disabled="disabled || loading"
    v-bind="$attrs"
  >
    <!-- 加载中旋转图标 -->
    <span v-if="loading" class="animate-spin inline-flex items-center justify-center">
      <LoaderIcon :size="iconSize" />
    </span>
    <!-- 前置图标 -->
    <component
      v-else-if="iconComponent"
      :is="iconComponent"
      :size="iconSize"
      class="shrink-0"
    />
    <slot />
  </button>
</template>

<script setup>
import { computed } from 'vue'
import {
  Loader2 as LoaderIcon,
  Plus, Lock, User, Package,
  Search, Download, Upload, Trash2,
  ChevronDown, ChevronRight, ChevronLeft,
  RotateCcw, ArrowLeftRight, X, Check,
  Edit, Eye, EyeOff, Copy, ExternalLink,
  AlertTriangle, Info, Settings, MoreHorizontal,
  Filter, RefreshCw, Save, Send, Printer,
  FileText, FilePlus, FolderOpen, Key, Zap, MessageSquare, BookOpen,
} from 'lucide-vue-next'

// 仅包含应用中实际使用的图标，避免 import * 导致整个 lucide 库进入 chunk
const icons = {
  Plus, Lock, User, Package,
  Search, Download, Upload, Trash2,
  ChevronDown, ChevronRight, ChevronLeft,
  RotateCcw, ArrowLeftRight, X, Check,
  Edit, Eye, EyeOff, Copy, ExternalLink,
  AlertTriangle, Info, Settings, MoreHorizontal,
  Filter, RefreshCw, Save, Send, Printer,
  FileText, FilePlus, FolderOpen, Key, Zap, MessageSquare, BookOpen,
}

defineOptions({ inheritAttrs: false })

const props = defineProps({
  variant: {
    type: String,
    default: 'primary',
    validator: (v) => ['primary', 'secondary', 'ghost', 'danger', 'success', 'text', 'link'].includes(v)
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'sm', 'xs'].includes(v)
  },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  icon: { type: String, default: '' },
  block: { type: Boolean, default: false }
})

const variantClass = computed(() => {
  const map = {
    primary: 'btn-primary',
    secondary: 'btn-secondary',
    ghost: 'btn-ghost',
    danger: 'btn-danger',
    success: 'btn-success',
    text: 'btn-text',
    link: 'btn-link',
  }
  return map[props.variant] || 'btn-primary'
})

const sizeClass = computed(() => {
  if (props.size === 'xs') return 'btn-xs'
  if (props.size === 'sm') return 'btn-sm'
  return ''
})

const iconSize = computed(() => {
  if (props.size === 'xs') return 12
  return props.size === 'sm' ? 16 : 18
})

/**
 * 将 kebab-case 或 camelCase 的图标名称转为 PascalCase
 * 例如: 'arrow-left' -> 'ArrowLeft', 'arrowLeft' -> 'ArrowLeft'
 */
function toPascalCase(str) {
  return str
    .replace(/(^|[-_])(\w)/g, (_, _sep, c) => c.toUpperCase())
    .replace(/^\w/, (c) => c.toUpperCase())
}

const iconComponent = computed(() => {
  if (!props.icon) return null
  const name = toPascalCase(props.icon)
  return icons[name] || null
})
</script>

<style scoped>
.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
  border: none;
}
.btn-ghost:hover {
  background: var(--elevated);
  color: var(--text);
  filter: none;
}

/* Success — subtle 模式，与 danger 对称 */
.btn-success {
  background: var(--success-subtle);
  color: var(--success-emphasis);
  border: 1px solid var(--success-subtle);
}
.btn-success:hover {
  filter: brightness(0.97);
}

/* Text — 无背景轻操作 */
.btn-text {
  background: transparent;
  color: var(--text-secondary);
  border: none;
  padding-left: 8px;
  padding-right: 8px;
}
.btn-text:hover {
  color: var(--text);
  background: var(--elevated);
  filter: none;
}

/* Link — 带下划线的文字链接 */
.btn-link {
  background: transparent;
  color: var(--primary);
  border: none;
  text-decoration: underline;
  padding-left: 4px;
  padding-right: 4px;
}
.btn-link:hover {
  color: var(--primary-hover);
  filter: none;
}
</style>
