<template>
  <span :class="['badge', colorClass]">{{ label }}</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: {
    type: String,
    default: 'gray',
    validator: (v) => ['success', 'warning', 'error', 'info', 'purple', 'orange', 'gray'].includes(v)
  },
  label: {
    type: String,
    default: ''
  }
})

const colorClass = computed(() => {
  const map = {
    success: 'badge-green',
    warning: 'badge-yellow',
    error: 'badge-red',
    info: 'badge-blue',
    purple: 'badge-purple',
    orange: 'badge-orange',
    gray: 'badge-gray'
  }
  return map[props.variant] || 'badge-gray'
})
</script>
