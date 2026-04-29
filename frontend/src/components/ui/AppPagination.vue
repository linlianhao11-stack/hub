<template>
  <div v-if="totalPages > 1" class="app-pagination">
    <button
      type="button"
      class="app-page-btn"
      :disabled="page <= 1"
      @click="page > 1 && $emit('update:page', page - 1)"
    >&#8249;</button>
    <template v-for="(p, i) in visiblePages" :key="i">
      <span v-if="p === '…'" class="app-page-ellipsis">…</span>
      <button
        v-else
        type="button"
        class="app-page-btn"
        :class="{ 'app-page-btn--active': p === page }"
        @click="p !== page && $emit('update:page', p)"
      >{{ p }}</button>
    </template>
    <button
      type="button"
      class="app-page-btn"
      :disabled="page >= totalPages"
      @click="page < totalPages && $emit('update:page', page + 1)"
    >&#8250;</button>
  </div>
</template>

<script setup>
defineProps({
  page: { type: Number, required: true },
  totalPages: { type: Number, required: true },
  visiblePages: { type: Array, required: true },
})
defineEmits(['update:page'])
</script>

<style scoped>
.app-pagination {
  display: flex;
  align-items: center;
  gap: 4px;
}
.app-page-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--duration-fast);
  white-space: nowrap;
}
.app-page-btn:hover:not(:disabled) {
  background: var(--elevated);
  border-color: var(--border-strong);
}
.app-page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.app-page-btn--active {
  background: var(--primary);
  color: var(--primary-foreground);
  border-color: var(--primary);
  font-weight: 500;
}
.app-page-btn--active:hover {
  background: var(--primary-hover);
}
.app-page-ellipsis {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--text-muted);
}
</style>
