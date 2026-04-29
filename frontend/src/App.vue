<template>
  <router-view />
  <Transition name="hub-toast">
    <div v-if="appStore.toast.show" class="hub-toast" :class="`hub-toast--${appStore.toast.kind}`">
      {{ appStore.toast.message }}
    </div>
  </Transition>
</template>

<script setup>
import { useAppStore } from './stores/app'

const appStore = useAppStore()
</script>

<style scoped>
.hub-toast {
  position: fixed;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 9999;
  padding: 10px 18px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  box-shadow: var(--sh-lg);
  min-width: 240px;
  max-width: 480px;
  text-align: center;
}
.hub-toast--success { background: var(--primary); color: var(--on-primary); border-color: var(--primary); }
.hub-toast--error { background: var(--error); color: #fff; border-color: var(--error); }

.hub-toast-enter-active,
.hub-toast-leave-active {
  transition: opacity 200ms ease, transform 200ms ease;
}
.hub-toast-enter-from,
.hub-toast-leave-to {
  opacity: 0;
  transform: translate(-50%, -8px);
}
</style>
