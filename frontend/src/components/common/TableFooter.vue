<template>
  <span class="app-footer-stats">
    {{ label || `共计 ${total} 条` }}
    <slot />
  </span>
  <div v-if="pageSize || totalPages > 1" class="table-footer-right">
    <AppSelect
      v-if="pageSize"
      :modelValue="pageSize"
      @update:modelValue="$emit('update:pageSize', Number($event))"
      size="sm"
      :options="sizeOptions"
      style="width: 7rem;"
    />
    <AppPagination
      v-if="totalPages > 1"
      :page="page"
      :total-pages="totalPages"
      :visible-pages="visiblePages"
      @update:page="$emit('update:page', $event)"
    />
  </div>
</template>

<script setup>
/**
 * TableFooter — 统一表格底栏内容
 *
 * 用于 AppTable 的 #footer slot 内部。
 * 提供统计文案 + 每页条数选择 + AppPagination 分页器。
 * 默认 slot 用于插入额外统计文案（如订单页的金额汇总）。
 * label prop 可自定义左侧文案（默认 "共计 N 条"）。
 *
 * 用法：
 *   <AppTable>
 *     <template #footer>
 *       <TableFooter :total="total" :page="page" :page-size="pageSize"
 *         :total-pages="totalPages" :visible-pages="visiblePages"
 *         @update:page="goToPage" @update:pageSize="setPageSize">
 *         <template v-if="summary">&nbsp;&middot;&nbsp; &yen;{{ fmt(summary) }}</template>
 *       </TableFooter>
 *     </template>
 *   </AppTable>
 */
import { computed } from 'vue'
import AppPagination from '../ui/AppPagination.vue'
import AppSelect from '../ui/AppSelect.vue'

const DEFAULT_OPTIONS = [
  { value: 50, label: '50 条/页' },
  { value: 200, label: '200 条/页' },
  { value: 1000, label: '1000 条/页' },
  { value: 5000, label: '5000 条/页' },
  { value: 10000, label: '10000 条/页' },
]

const props = defineProps({
  total: { type: Number, required: true },
  label: { type: String, default: '' },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 0 },
  pageSizeOptions: { type: Array, default: null },
  totalPages: { type: Number, default: 1 },
  visiblePages: { type: Array, default: () => [] },
})

defineEmits(['update:page', 'update:pageSize'])

const sizeOptions = computed(() => props.pageSizeOptions || DEFAULT_OPTIONS)
</script>

<style scoped>
.table-footer-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
