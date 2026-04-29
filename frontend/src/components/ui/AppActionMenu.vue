<template>
  <div class="action-menu" ref="containerRef">
    <div @click.stop="toggle">
      <slot name="trigger">
        <AppButton variant="secondary" size="xs">···</AppButton>
      </slot>
    </div>
    <teleport to="body">
      <div v-if="open" class="action-menu-dropdown" :style="dropdownStyle" @click.stop>
        <template v-for="(item, i) in items" :key="i">
          <div v-if="item.separator" class="action-menu-sep" />
          <button
            v-else
            :disabled="item.disabled"
            :class="['action-menu-item', { 'action-menu-item-danger': item.danger }]"
            @click="handleClick(item)"
          >
            <component v-if="item.icon && getIcon(item.icon)" :is="getIcon(item.icon)" :size="16" class="action-menu-item-icon" />
            {{ item.label }}
          </button>
        </template>
      </div>
    </teleport>
    <teleport to="body">
      <div v-if="open" class="action-menu-backdrop" @click="close" />
    </teleport>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import AppButton from './AppButton.vue'
import {
  Download, Upload, Printer, Copy, Trash2,
  Eye, Edit, ExternalLink, RefreshCw, X,
  FileText, Send, Save, Pencil,
} from 'lucide-vue-next'

const iconMap = {
  Download, Upload, Printer, Copy, Trash2,
  Eye, Edit, ExternalLink, RefreshCw, X,
  FileText, Send, Save, Pencil,
}

defineProps({
  items: {
    type: Array,
    default: () => [],
    // 每项: { label, onClick?, icon?, danger?, disabled?, separator? }
  }
})

const open = ref(false)
const containerRef = ref(null)
const dropdownStyle = ref({})

function toPascalCase(str) {
  return str
    .replace(/(^|[-_])(\w)/g, (_, _sep, c) => c.toUpperCase())
    .replace(/^\w/, (c) => c.toUpperCase())
}

function getIcon(name) {
  if (!name) return null
  return iconMap[toPascalCase(name)] || null
}

async function toggle() {
  if (open.value) {
    close()
    return
  }
  open.value = true
  await nextTick()
  positionDropdown()
}

function close() {
  open.value = false
}

function positionDropdown() {
  if (!containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  dropdownStyle.value = {
    position: 'fixed',
    top: `${rect.bottom + 4}px`,
    right: `${window.innerWidth - rect.right}px`,
    zIndex: 9999,
  }
}

function handleClick(item) {
  if (item.disabled) return
  if (item.onClick) item.onClick()
  close()
}
</script>

<style scoped>
.action-menu {
  position: relative;
  display: inline-flex;
}

.action-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9998;
}

.action-menu-dropdown {
  min-width: 160px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg, 8px);
  padding: 4px 0;
  box-shadow: var(--sh-md);
}

.action-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text);
  background: none;
  border: none;
  cursor: pointer;
  text-align: left;
  transition: background var(--duration-fast);
}

.action-menu-item:hover:not(:disabled) {
  background: var(--elevated);
}

.action-menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-menu-item-icon {
  color: var(--text-secondary);
  flex-shrink: 0;
}

.action-menu-item-danger {
  color: var(--error);
}
.action-menu-item-danger .action-menu-item-icon {
  color: var(--error);
}
.action-menu-item-danger:hover:not(:disabled) {
  background: var(--error-subtle);
}

.action-menu-sep {
  height: 1px;
  background: var(--border);
  margin: 4px 0;
}
</style>
