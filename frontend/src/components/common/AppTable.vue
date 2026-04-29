<template>
  <div class="app-table-root">
    <!-- Mobile card view -->
    <div v-if="$slots.mobile" class="md:hidden space-y-2">
      <slot name="mobile" />
      <div v-if="empty" class="app-table-empty">{{ emptyText }}</div>
    </div>

    <!-- Desktop table view -->
    <div :class="[card ? 'app-table-card' : 'app-table-bare', $slots.mobile ? 'hidden md:block' : '']">
      <div class="app-table-scroll">
        <table :class="['app-table', sticky ? 'app-table-sticky' : '']">
          <thead>
            <slot name="header">
              <tr>
                <th
                  v-for="col in columns"
                  :key="col.key"
                  scope="col"
                  class="app-th"
                  :class="[
                    col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left',
                    col.sortable ? 'app-th--sortable' : '',
                    col.class || ''
                  ]"
                  :style="col.width ? { width: col.width } : {}"
                  :aria-sort="col.sortable && sortKey === col.key ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined"
                >
                  <button
                    v-if="col.sortable"
                    type="button"
                    class="app-th-sort-btn"
                    :class="col.align === 'right' ? 'app-th-sort-btn--right' : col.align === 'center' ? 'app-th-sort-btn--center' : ''"
                    @click="toggleSort(col.key)"
                  >
                    {{ col.label }}
                    <span v-if="sortKey === col.key" class="app-sort-icon" aria-hidden="true">
                      {{ sortOrder === 'asc' ? '↑' : '↓' }}
                    </span>
                  </button>
                  <template v-else>{{ col.label }}</template>
                </th>
                <slot name="header-extra" />
              </tr>
            </slot>
          </thead>
          <tbody>
            <slot />
          </tbody>
        </table>
        <div v-if="empty && !$slots.mobile" class="app-table-empty">{{ emptyText }}</div>
      </div>
      <div v-if="$slots.footer" class="app-table-footer">
        <slot name="footer" />
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  columns: {
    type: Array,
    default: () => []
    // 每项: { key: string, label: string, align?: 'left'|'right'|'center', sortable?: boolean, width?: string, class?: string }
    // 使用 #header slot 时可省略
  },
  sortKey: {
    type: String,
    default: ''
  },
  sortOrder: {
    type: String,
    default: 'asc'
  },
  empty: {
    type: Boolean,
    default: false
  },
  emptyText: {
    type: String,
    default: '暂无数据'
  },
  card: {
    type: Boolean,
    default: true
  },
  sticky: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['sort'])

function toggleSort(key) {
  if (props.sortKey === key) {
    emit('sort', { key, order: props.sortOrder === 'asc' ? 'desc' : 'asc' })
  } else {
    emit('sort', { key, order: 'asc' })
  }
}
</script>

<!-- 非 scoped — 穿透 slot 内容（tr/td 由消费方传入，属于父组件作用域） -->
<style>
.app-table-card tbody tr,
.app-table-bare tbody tr {
  height: 30px;
  transition: background var(--duration-fast);
}
.app-table-card tbody tr:hover,
.app-table-bare tbody tr:hover {
  background: var(--elevated);
}
.app-table-card tbody tr:not(:last-child) td,
.app-table-bare tbody tr:not(:last-child) td {
  border-bottom: 1px solid var(--border-light);
}
.app-table-card .app-td,
.app-table-bare .app-td {
  padding: 0 8px;
  height: 30px;
  vertical-align: middle;
  white-space: nowrap;
  font-size: 12px;
}
.app-table-card .app-th,
.app-table-bare .app-th {
  height: 28px;
  padding: 0 8px;
  font-size: 11px;
  font-weight: 500;
  color: var(--muted-foreground);
  white-space: nowrap;
  border-bottom: 1px solid var(--border-light);
}
.app-table-card .app-th--sortable,
.app-table-bare .app-th--sortable {
  padding: 0;
}
.app-table-card .app-th--sortable:hover,
.app-table-bare .app-th--sortable:hover {
  color: var(--primary);
}
.app-table-card .app-sort-icon,
.app-table-bare .app-sort-icon {
  color: var(--primary);
  margin-left: 2px;
}
.app-table-card .app-th-sort-btn,
.app-table-bare .app-th-sort-btn {
  all: unset;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: 100%;
  height: 100%;
  padding: 0 8px;
  font: inherit;
  color: inherit;
  cursor: pointer;
  user-select: none;
}
.app-table-card .app-th-sort-btn--right,
.app-table-bare .app-th-sort-btn--right {
  justify-content: flex-end;
}
.app-table-card .app-th-sort-btn--center,
.app-table-bare .app-th-sort-btn--center {
  justify-content: center;
}
.app-table-card .app-th-sort-btn:focus-visible,
.app-table-bare .app-th-sort-btn:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: -2px;
  border-radius: 2px;
}
/* 统一 footer 统计文本 — 替代每页私有 *-footer-stats */
.app-table-card .app-footer-stats,
.app-table-bare .app-footer-stats {
  font-size: 12px;
  color: var(--muted-foreground);
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
}
/* sticky header — 仅当消费方传 sticky prop 时生效 */
/* > thead 直接子级选择器确保只命中当前 table 的表头，不穿透到嵌套子表 */
.app-table-sticky > thead .app-th {
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--elevated);
}
/* 统一展开行单元格 — colspan 展开区域 */
.app-table-card .app-expand-cell,
.app-table-bare .app-expand-cell {
  background: color-mix(in srgb, var(--elevated) 50%, transparent);
  padding: 12px 24px;
}
/* 统一汇总行 — 期初/本期合计/期末/tfoot 合计 */
.app-table-card .app-summary-row td,
.app-table-bare .app-summary-row td {
  background: var(--canvas);
  font-weight: 600;
}
</style>

<!-- scoped — 组件自身元素 -->
<style scoped>
.app-table-root {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.app-table-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.app-table-bare {
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}
.app-table-scroll {
  overflow: auto;
  -webkit-overflow-scrolling: touch;
  flex: 1;
  min-height: 0;
}
.app-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.app-table-empty {
  padding: 32px;
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
}
.app-table-footer {
  border-top: 1px solid var(--border-light);
  padding: 10px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
</style>
