<template>
  <div class="searchable-select" ref="wrapperRef">
    <button type="button" class="input input-sm flex items-center cursor-pointer text-left" @click="toggle" :class="{ 'text-muted': !selectedLabel }">
      <span class="flex-1 truncate">{{ selectedLabel || placeholder }}</span>
      <span v-if="modelValue" class="ss-clear" @click.stop="clear">&times;</span>
      <span v-else class="ss-caret">&#9662;</span>
    </button>
    <teleport to="body">
      <div v-if="open" class="ss-dropdown" :style="dropdownStyle" ref="dropdownRef">
        <input
          ref="searchInputRef"
          v-model="searchText"
          class="input input-sm w-full mb-1"
          :placeholder="searchPlaceholder"
          @keydown.esc="open = false"
        />
        <div class="ss-options">
          <button
            type="button"
            v-for="opt in filtered"
            :key="opt.id"
            class="ss-option"
            :class="{ 'ss-option-active': opt.id == modelValue }"
            @click="select(opt)"
          >
            <div class="truncate">{{ opt.label }}</div>
            <div v-if="opt.sublabel" class="ss-sublabel">{{ opt.sublabel }}</div>
          </button>
          <div v-if="!filtered.length" class="ss-empty">无匹配项</div>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'

const props = defineProps({
  options: { type: Array, default: () => [] },
  modelValue: { type: [String, Number], default: '' },
  placeholder: { type: String, default: '请选择' },
  searchPlaceholder: { type: String, default: '搜索...' }
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const searchText = ref('')
const wrapperRef = ref(null)
const searchInputRef = ref(null)
const dropdownRef = ref(null)
const dropdownStyle = ref({})

const selectedLabel = computed(() => {
  const opt = props.options.find(o => o.id == props.modelValue)
  return opt ? opt.label : ''
})

const filtered = computed(() => {
  if (!searchText.value) return props.options
  const kw = searchText.value.toLowerCase()
  return props.options.filter(o =>
    (o.label || '').toLowerCase().includes(kw) ||
    (o.sublabel || '').toLowerCase().includes(kw)
  )
})

/** 计算下拉面板位置（固定定位，脱离 overflow 容器） */
const updatePosition = () => {
  if (!wrapperRef.value) return
  const rect = wrapperRef.value.getBoundingClientRect()
  dropdownStyle.value = {
    position: 'fixed',
    top: rect.bottom + 2 + 'px',
    left: rect.left + 'px',
    width: rect.width + 'px',
    zIndex: 9999
  }
}

const toggle = () => {
  open.value = !open.value
  if (open.value) {
    searchText.value = ''
    updatePosition()
    nextTick(() => searchInputRef.value?.focus())
  }
}

const select = (opt) => {
  emit('update:modelValue', opt.id)
  open.value = false
}

const clear = () => {
  emit('update:modelValue', '')
}

const onClickOutside = (e) => {
  if (!wrapperRef.value?.contains(e.target) && !dropdownRef.value?.contains(e.target)) {
    open.value = false
  }
}

/** 仅在下拉打开时注册全局监听器，关闭时移除 */
watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener('click', onClickOutside)
    window.addEventListener('scroll', updatePosition, true)
    window.addEventListener('resize', updatePosition)
  } else {
    document.removeEventListener('click', onClickOutside)
    window.removeEventListener('scroll', updatePosition, true)
    window.removeEventListener('resize', updatePosition)
  }
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
  window.removeEventListener('scroll', updatePosition, true)
  window.removeEventListener('resize', updatePosition)
})
</script>

<style scoped>
.searchable-select {
  position: relative;
}
.ss-clear {
  margin-left: 4px;
  color: var(--text-muted);
  cursor: pointer;
  line-height: 1;
}
.ss-clear:hover {
  color: var(--text);
}
.ss-caret {
  margin-left: 4px;
  color: var(--text-muted);
  font-size: 10px;
}
</style>

<style>
/* 下拉面板通过 teleport 渲染到 body，不能用 scoped */
.ss-dropdown {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 6px;
  box-shadow: var(--sh-md);
}
.ss-options {
  max-height: 200px;
  overflow-y: auto;
}
.ss-option {
  display: block;
  width: 100%;
  text-align: left;
  padding: 6px 10px;
  border-radius: var(--r-sm);
  cursor: pointer;
  font-size: 13px;
  background: none;
  border: none;
  color: var(--text);
  transition: background var(--duration-fast);
}
.ss-option:hover {
  background: var(--elevated);
}
.ss-option-active {
  background: var(--primary-muted);
}
.ss-sublabel {
  font-size: 12px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ss-empty {
  padding: 12px 12px;
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
}
</style>
