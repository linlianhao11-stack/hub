import { defineStore } from 'pinia'
import { reactive } from 'vue'

/**
 * 全局 UI store：toast 提示 / 主题切换。
 */
export const useAppStore = defineStore('hub-app', () => {
  const toast = reactive({ show: false, message: '', kind: 'success' })
  let toastTimer = null

  function showToast(message, kind = 'success') {
    toast.show = true
    toast.message = message
    toast.kind = kind
    if (toastTimer) clearTimeout(toastTimer)
    toastTimer = setTimeout(() => {
      toast.show = false
    }, 2400)
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('hub-theme', theme)
  }

  return { toast, showToast, setTheme }
})
