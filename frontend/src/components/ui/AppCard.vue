<template>
  <div class="app-card" :class="[paddingClass, { hoverable }]">
    <slot />
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  padding: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'compact', 'none'].includes(v)
  },
  hoverable: {
    type: Boolean,
    default: false
  }
})

const paddingClass = computed(() => `padding-${props.padding}`)
</script>

<style scoped>
.app-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  overflow: hidden;
  transition: all var(--duration-normal) var(--ease-out-expo);
}

.padding-default {
  padding: 20px;
}

.padding-compact {
  padding: 14px;
}

.padding-none {
  padding: 0;
}

.hoverable:hover {
  box-shadow: var(--sh-card-hover);
}

@media (max-width: 768px) {
  .padding-default {
    padding: 16px;
  }
  .padding-compact {
    padding: 12px;
  }
}
</style>
