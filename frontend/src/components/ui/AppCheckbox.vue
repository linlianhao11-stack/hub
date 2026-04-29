<template>
  <input
    ref="inputRef"
    type="checkbox"
    :class="['app-checkbox', sizeClass]"
    :checked="isChecked"
    :disabled="disabled"
    @change="handleChange"
  />
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'

const props = defineProps({
  modelValue: {
    type: [Boolean, Array],
    default: false,
  },
  value: {
    default: undefined,
  },
  indeterminate: {
    type: Boolean,
    default: false,
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'sm'].includes(v),
  },
  disabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue'])

const inputRef = ref(null)

const isArrayMode = computed(() => Array.isArray(props.modelValue))

const isChecked = computed(() => {
  if (isArrayMode.value) {
    return props.modelValue.some((item) =>
      typeof item === 'object' ? JSON.stringify(item) === JSON.stringify(props.value) : item === props.value
    )
  }
  return !!props.modelValue
})

const sizeClass = computed(() => props.size === 'sm' ? 'app-checkbox--sm' : '')

function handleChange(e) {
  if (isArrayMode.value) {
    const checked = e.target.checked
    const arr = [...props.modelValue]
    if (checked) {
      arr.push(props.value)
    } else {
      const idx = arr.findIndex((item) =>
        typeof item === 'object' ? JSON.stringify(item) === JSON.stringify(props.value) : item === props.value
      )
      if (idx !== -1) arr.splice(idx, 1)
    }
    emit('update:modelValue', arr)
  } else {
    emit('update:modelValue', e.target.checked)
  }
}

// indeterminate 是 DOM property，必须通过 ref 同步
watch(() => props.indeterminate, (val) => {
  if (inputRef.value) inputRef.value.indeterminate = val
})

onMounted(() => {
  if (inputRef.value) inputRef.value.indeterminate = props.indeterminate
})
</script>

<style scoped>
.app-checkbox {
  width: 16px;
  height: 16px;
  border-radius: var(--r-sm);
  border: 1.5px solid var(--border);
  background: var(--surface);
  appearance: none;
  cursor: pointer;
  position: relative;
  flex-shrink: 0;
  transition: background var(--duration-fast), border-color var(--duration-fast);
}

.app-checkbox--sm {
  width: 14px;
  height: 14px;
}

.app-checkbox:hover:not(:disabled) {
  border-color: var(--primary);
}

.app-checkbox:checked {
  background: var(--primary);
  border-color: var(--primary);
}

.app-checkbox:checked::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 45%;
  width: 4px;
  height: 8px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: translate(-50%, -50%) rotate(45deg);
}

.app-checkbox--sm:checked::after {
  width: 3px;
  height: 7px;
  border-width: 0 1.5px 1.5px 0;
}

.app-checkbox:indeterminate {
  background: var(--primary);
  border-color: var(--primary);
}

.app-checkbox:indeterminate::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 8px;
  height: 2px;
  background: white;
  transform: translate(-50%, -50%);
}

.app-checkbox--sm:indeterminate::after {
  width: 7px;
}

.app-checkbox:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}

.app-checkbox:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
