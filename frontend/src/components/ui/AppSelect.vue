<template>
  <div class="app-select-wrapper">
    <label v-if="label" :for="selectId" class="label">{{ label }}</label>
    <select
      v-bind="$attrs"
      :id="selectId"
      :value="modelValue"
      :disabled="disabled"
      :aria-describedby="computedDescribedBy"
      :aria-invalid="error ? 'true' : undefined"
      class="input app-select"
      :class="[selectSizeClass, { 'app-select-error': error }]"
      @change="$emit('update:modelValue', $event.target.value)"
    >
      <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
      <option
        v-for="opt in options"
        :key="opt.value"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
    <p v-if="error" :id="errorId" class="app-select-error-msg" role="alert">{{ error }}</p>
  </div>
</template>

<script setup>
defineOptions({ inheritAttrs: false })
import { computed, useAttrs, useId } from 'vue'

const props = defineProps({
  modelValue: {
    type: [String, Number],
    default: ''
  },
  label: {
    type: String,
    default: ''
  },
  options: {
    type: Array,
    default: () => []
  },
  placeholder: {
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
  error: {
    type: String,
    default: ''
  }
})

defineEmits(['update:modelValue'])

const selectId = useId()
const attrs = useAttrs()
const errorId = selectId + '-error'

const computedDescribedBy = computed(() => {
  const parts = []
  const external = attrs['aria-describedby']
  if (external) parts.push(external)
  if (props.error) parts.push(errorId)
  return parts.length > 0 ? [...new Set(parts.join(' ').split(' '))].join(' ') : undefined
})

const selectSizeClass = computed(() => {
  if (props.size === 'toolbar') return 'app-select-toolbar'
  if (props.size === 'sm') return 'app-select-sm'
  return ''
})
</script>

<style scoped>
.app-select {
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3E%3Cpath stroke='%2386868b' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3E%3C/svg%3E");
  background-position: right 8px center;
  background-repeat: no-repeat;
  background-size: 16px;
  padding-right: 28px;
}

/* SM 密度 (34px) */
.app-select-sm {
  padding: 4px 28px 4px 10px;
  font-size: 12px;
  min-height: 34px;
  height: 34px;
  line-height: 1.4;
  border-radius: var(--r-md, 6px);
}

/* Toolbar 紧凑密度 (32px) — 工具栏筛选专用 */
.app-select-toolbar {
  padding: 4px 28px 4px 10px;
  font-size: 12px;
  min-height: 32px;
  height: 32px;
  line-height: 1.4;
  border-radius: var(--r-md, 6px);
}

.app-select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.app-select-error {
  border-color: var(--error) !important;
}

.app-select-error:focus {
  border-color: var(--error) !important;
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.15) !important;
}

.app-select-error-msg {
  margin-top: 6px;
  font-size: 12px;
  color: var(--error);
  line-height: 1.4;
}

@media (max-width: 768px) {
  .app-select {
    min-height: 48px;
    font-size: 16px;
  }
}
</style>
