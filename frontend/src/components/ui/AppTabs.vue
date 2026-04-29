<template>
  <div class="tabs-container flex border-b border-line overflow-x-auto">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      :class="['tab', { active: modelValue === tab.key }]"
      type="button"
      @click="$emit('update:modelValue', tab.key)"
    >
      <span>{{ tab.label }}</span>
      <span
        v-if="tab.count != null"
        class="tab-count"
      >{{ tab.count }}</span>
    </button>
  </div>
</template>

<script setup>
defineProps({
  tabs: {
    type: Array,
    required: true
    // [{ key: string, label: string, count?: number }]
  },
  modelValue: {
    type: String,
    required: true
  }
})

defineEmits(['update:modelValue'])
</script>

<style scoped>
.tabs-container {
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.tabs-container::-webkit-scrollbar {
  display: none;
}
.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  margin-left: 6px;
  border-radius: 9px;
  background: var(--elevated);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
}
.tab.active .tab-count {
  background: var(--primary-muted);
  color: var(--primary);
}
</style>
