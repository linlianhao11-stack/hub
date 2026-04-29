<template>
  <teleport to="body">
    <Transition name="modal">
      <div
        v-if="visible"
        class="modal-overlay"
        @click.self="onOverlayClick"
        @keydown.escape="onEscape"
      >
        <div
          ref="dialogRef"
          class="modal-content"
          :class="sizeClass"
          tabindex="-1"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="title ? titleId : undefined"
          :aria-label="title ? undefined : '对话框'"
          @click.stop
        >
          <!-- 头部 -->
          <div class="modal-header">
            <h3 :id="titleId" class="modal-title">{{ title }}</h3>
            <div class="modal-header-right">
              <slot name="headerActions" />
              <div v-if="$slots.headerActions && showClose" class="modal-header-divider"></div>
              <button
                v-if="showClose"
                type="button"
                class="modal-icon-btn"
                aria-label="关闭"
                @click="close"
              >
                <X :size="16" />
              </button>
            </div>
          </div>

          <!-- 主体 -->
          <div class="modal-body">
            <slot />
          </div>

          <!-- 底部 -->
          <div v-if="$slots.footer" class="modal-footer">
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </teleport>
</template>

<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount, useId } from 'vue'
import { X } from 'lucide-vue-next'
import { useFocusTrap } from '../../composables/useFocusTrap'

// 嵌套 modal body scroll lock 管理
let openCount = 0
let savedOverflow = ''

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  title: {
    type: String,
    default: ''
  },
  size: {
    type: String,
    default: 'md',
    validator: (v) => ['sm', 'md', 'lg', 'xl', 'fullscreen'].includes(v)
  },
  showClose: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['update:visible', 'close'])

const dialogRef = ref(null)
const titleId = useId()
const { activate: activateTrap, deactivate: deactivateTrap } = useFocusTrap(dialogRef)

const sizeClass = computed(() => `modal-size-${props.size}`)

function close() {
  emit('update:visible', false)
  emit('close')
}

function onOverlayClick() {
  close()
}

// 实例级 body lock —— hasLocked 防止 watch close + onBeforeUnmount 双减
let hasLocked = false

function lockBody() {
  if (hasLocked) return
  hasLocked = true
  if (openCount === 0) {
    savedOverflow = document.body.style.overflow
  }
  openCount++
  document.body.style.overflow = 'hidden'
}

function unlockBody() {
  if (!hasLocked) return
  hasLocked = false
  openCount--
  if (openCount <= 0) {
    openCount = 0
    document.body.style.overflow = savedOverflow
  }
}

function onEscape(e) {
  e.stopPropagation()
  close()
}

// 焦点管理：打开时聚焦 dialog，关闭时恢复之前焦点
let previousFocus = null

watch(
  () => props.visible,
  async (val) => {
    if (val) {
      previousFocus = document.activeElement
      lockBody()
      await nextTick()
      dialogRef.value?.focus()
      activateTrap()
    } else {
      deactivateTrap()
      unlockBody()
      if (previousFocus && typeof previousFocus.focus === 'function') {
        await nextTick()
        previousFocus.focus()
        previousFocus = null
      }
    }
  }
)

onBeforeUnmount(() => {
  deactivateTrap()
  unlockBody()
})
</script>

<style scoped>
.modal-content.modal-size-sm {
  max-width: 400px;
}

.modal-content.modal-size-md {
  max-width: 560px;
}

.modal-content.modal-size-lg {
  max-width: 720px;
}

.modal-content.modal-size-xl {
  max-width: 960px;
}

.modal-content.modal-size-fullscreen {
  width: 95vw;
  max-width: 95vw;
  height: 95vh;
  max-height: 95vh;
}

.modal-header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.modal-header-divider {
  width: 1px;
  height: 20px;
  background: var(--border-light);
  margin: 0 4px;
}

.modal-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--r-md);
  border: none;
  background: transparent;
  color: var(--muted-foreground);
  cursor: pointer;
  transition: color var(--duration-fast), background var(--duration-fast);
}

.modal-icon-btn:hover {
  color: var(--foreground);
  background: var(--elevated);
}

/* Transition: 背景淡入 + 内容缩放 */
.modal-enter-active {
  transition: opacity var(--duration-normal, 200ms) var(--ease-out-expo, ease);
}
.modal-enter-active .modal-content {
  transition: transform var(--duration-slow, 300ms) var(--ease-out-expo, ease),
              opacity var(--duration-normal, 200ms) var(--ease-out-expo, ease);
}
.modal-leave-active {
  transition: opacity 150ms ease;
}
.modal-leave-active .modal-content {
  transition: transform 150ms ease, opacity 150ms ease;
}
.modal-enter-from {
  opacity: 0;
}
.modal-enter-from .modal-content {
  opacity: 0;
  transform: scale(0.96);
}
.modal-leave-to {
  opacity: 0;
}
.modal-leave-to .modal-content {
  opacity: 0;
  transform: scale(0.96);
}
</style>
