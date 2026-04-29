<template>
  <div class="search-bar">
    <Search class="search-bar-icon" :size="18" aria-hidden="true" />
    <input
      type="text"
      :value="modelValue"
      :placeholder="placeholder || '搜索...'"
      class="search-bar-input"
      @input="$emit('update:modelValue', $event.target.value)"
    />
  </div>
</template>

<script setup>
import { Search } from 'lucide-vue-next'

defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: ''
  }
})

defineEmits(['update:modelValue'])
</script>

<style scoped>
.search-bar {
  position: relative;
  display: flex;
  align-items: center;
}

.search-bar-icon {
  position: absolute;
  left: 14px;
  color: var(--text-muted);
  pointer-events: none;
  flex-shrink: 0;
}

.search-bar-input {
  width: 100%;
  height: 44px;
  padding: 10px 14px 10px 42px;
  background: var(--elevated);
  border: none;
  border-radius: var(--r-md);
  font-size: 14px;
  color: var(--text);
  outline: none;
  transition: all var(--duration-normal);
  letter-spacing: -0.01em;
}

.search-bar-input::placeholder {
  color: var(--text-muted);
}

.search-bar-input:focus {
  box-shadow: 0 0 0 2px var(--primary-ring);
}

@media (max-width: 768px) {
  .search-bar-input {
    font-size: 16px;
  }
}
</style>
