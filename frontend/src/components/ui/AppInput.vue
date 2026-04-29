<template>
  <div class="app-input-wrapper">
    <label v-if="label" :for="inputId" class="label">{{ label }}</label>
    <div class="app-input-container" :class="[sizeClass, { 'has-icon': icon, 'has-error': error }]">
      <component
        v-if="icon"
        :is="iconComponent"
        class="app-input-icon"
        :size="size === 'toolbar' ? 14 : size === 'sm' ? 14 : 16"
        aria-hidden="true"
      />
      <input
        v-bind="$attrs"
        :id="inputId"
        :type="type"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="disabled"
        :aria-describedby="computedDescribedBy"
        :aria-invalid="error ? 'true' : undefined"
        class="input"
        :class="[sizeInputClass, { 'input-with-icon': icon, 'input-error': error }]"
        @input="onInput"
      />
    </div>
    <p v-if="error" :id="errorId" class="app-input-error" role="alert">{{ error }}</p>
  </div>
</template>

<script setup>
defineOptions({ inheritAttrs: false })
import { computed, useAttrs, useId } from 'vue'
import {
  Lock, User, Search, Eye, EyeOff, Mail, Phone,
  AlertCircle, CheckCircle, Info, PackageSearch,
} from 'lucide-vue-next'

// 仅包含 AppInput icon 属性实际使用的图标
const icons = {
  Lock, User, Search, Eye, EyeOff, Mail, Phone,
  AlertCircle, CheckCircle, Info, PackageSearch,
}

const props = defineProps({
  modelValue: {
    type: [String, Number],
    default: ''
  },
  label: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: ''
  },
  type: {
    type: String,
    default: 'text',
    validator: (v) => ['text', 'password', 'number', 'email', 'date', 'month'].includes(v)
  },
  icon: {
    type: String,
    default: ''
  },
  error: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'sm', 'toolbar'].includes(v)
  },
  modelModifiers: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['update:modelValue'])

const onInput = (e) => {
  let val = e.target.value
  if (props.modelModifiers.number) {
    const n = parseFloat(val)
    val = isNaN(n) ? val : n
  }
  emit('update:modelValue', val)
}

const inputId = useId()
const attrs = useAttrs()
const errorId = inputId + '-error'

const computedDescribedBy = computed(() => {
  const parts = []
  const external = attrs['aria-describedby']
  if (external) parts.push(external)
  if (props.error) parts.push(errorId)
  return parts.length > 0 ? [...new Set(parts.join(' ').split(' '))].join(' ') : undefined
})

const sizeClass = computed(() => {
  if (props.size === 'toolbar') return 'size-toolbar'
  if (props.size === 'sm') return 'size-sm'
  return 'size-default'
})

const sizeInputClass = computed(() => {
  if (props.size === 'toolbar') return 'input-toolbar'
  if (props.size === 'sm') return 'input-sm'
  return ''
})

const iconComponent = computed(() => {
  if (!props.icon) return null
  // 将 kebab-case 或 PascalCase 的图标名转换为 lucide 组件名
  const name = props.icon
    .split('-')
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join('')
  return icons[name] || null
})
</script>

<style scoped>
.app-input-wrapper {
  display: flex;
  flex-direction: column;
}

.app-input-container {
  position: relative;
  display: flex;
  align-items: center;
}

.app-input-icon {
  position: absolute;
  left: 14px;
  color: var(--text-muted);
  pointer-events: none;
  z-index: 1;
  flex-shrink: 0;
}

.size-sm .app-input-icon {
  left: 12px;
}

.input-with-icon {
  padding-left: 40px;
}

.size-sm .input-with-icon {
  padding-left: 36px;
}

/* Toolbar 紧凑密度 (32px) — 工具栏筛选专用 */
.input-toolbar {
  padding: 4px 10px;
  font-size: 12px;
  min-height: 32px;
  height: 32px;
  border-radius: var(--r-md, 6px);
}

.size-toolbar .app-input-icon {
  left: 10px;
}

.size-toolbar .input-with-icon {
  padding-left: 30px;
}

.input-error {
  border-color: var(--error) !important;
}

.input-error:focus {
  border-color: var(--error) !important;
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.15) !important;
}

.app-input-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--error);
  line-height: 1.4;
}
</style>
