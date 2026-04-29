<template>
  <div class="app-textarea-wrapper">
    <label v-if="label" :for="textareaId" class="label">{{ label }}</label>
    <textarea
      v-bind="$attrs"
      :id="textareaId"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :rows="rows"
      :aria-describedby="computedDescribedBy"
      :aria-invalid="error ? 'true' : undefined"
      class="app-textarea"
      :class="[textareaSizeClass, { 'app-textarea-error': error }]"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <p v-if="error" :id="errorId" class="app-textarea-error-msg" role="alert">{{ error }}</p>
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
  placeholder: {
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
  rows: {
    type: [Number, String],
    default: 3
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'sm', 'toolbar'].includes(v)
  }
})

defineEmits(['update:modelValue'])

const textareaId = useId()
const attrs = useAttrs()
const errorId = textareaId + '-error'

const computedDescribedBy = computed(() => {
  const parts = []
  const external = attrs['aria-describedby']
  if (external) parts.push(external)
  if (props.error) parts.push(errorId)
  return parts.length > 0 ? [...new Set(parts.join(' ').split(' '))].join(' ') : undefined
})

const textareaSizeClass = computed(() => {
  if (props.size === 'toolbar') return 'app-textarea-toolbar'
  if (props.size === 'sm') return 'app-textarea-sm'
  return ''
})
</script>

<style scoped>
.app-textarea-wrapper {
  display: flex;
  flex-direction: column;
}

.app-textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-md, 6px);
  outline: none;
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  font-family: inherit;
  resize: vertical;
  line-height: 1.5;
  transition: border-color var(--duration-normal), box-shadow var(--duration-normal);
}

.app-textarea::placeholder {
  color: var(--text-muted);
}

.app-textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-ring);
}

.app-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: var(--elevated);
}

/* SM 密度 */
.app-textarea-sm {
  padding: 6px 10px;
  font-size: 12px;
}

/* Toolbar 紧凑密度 */
.app-textarea-toolbar {
  padding: 6px 10px;
  font-size: 12px;
}

.app-textarea-error {
  border-color: var(--error) !important;
}

.app-textarea-error:focus {
  border-color: var(--error) !important;
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.15) !important;
}

.app-textarea-error-msg {
  margin-top: 6px;
  font-size: 12px;
  color: var(--error);
  line-height: 1.4;
}

@media (max-width: 768px) {
  .app-textarea {
    padding: 12px 16px;
    font-size: 16px;
  }
}
</style>
