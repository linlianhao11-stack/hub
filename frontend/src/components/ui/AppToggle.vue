<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    :class="['app-toggle', size === 'sm' ? 'app-toggle--sm' : '', modelValue ? 'app-toggle--on' : '', disabled ? 'app-toggle--disabled' : '']"
    @click="!disabled && $emit('update:modelValue', !modelValue)"
  >
    <span class="app-toggle-thumb" />
  </button>
</template>

<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  size: { type: String, default: 'default', validator: v => ['default', 'sm'].includes(v) },
})
defineEmits(['update:modelValue'])
</script>

<style scoped>
.app-toggle {
  position: relative;
  width: 40px;
  height: 22px;
  border-radius: 11px;
  border: none;
  padding: 0;
  cursor: pointer;
  background: var(--border-strong, var(--line-strong, #c4c4c4));
  transition: background var(--duration-normal, 0.2s) var(--ease-out-expo, ease);
  flex-shrink: 0;
}
.app-toggle:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.app-toggle--on {
  background: var(--success, #22c55e);
}
.app-toggle--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.app-toggle-thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--surface, #fff);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
  transition: transform var(--duration-normal, 0.2s) var(--ease-out-expo, ease);
}
.app-toggle--on .app-toggle-thumb {
  transform: translateX(18px);
}

/* sm size */
.app-toggle--sm {
  width: 34px;
  height: 18px;
  border-radius: 9px;
}
.app-toggle--sm .app-toggle-thumb {
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
}
.app-toggle--sm.app-toggle--on .app-toggle-thumb {
  transform: translateX(16px);
}
</style>
