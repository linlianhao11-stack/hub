/**
 * 焦点陷阱 composable
 * 用于 AppModal 等需要限制 Tab 焦点范围的场景
 */

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not(:disabled)',
  'input:not(:disabled):not([type="hidden"])',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

/**
 * 判断元素是否可见（有布局盒且未被隐藏）
 */
function isVisible(el) {
  // 无布局盒（display:none 等）— 但 fixed 定位元素的 offsetParent 也为 null，需排除
  if (el.offsetParent === null) {
    const style = getComputedStyle(el)
    if (style.display === 'none' || style.position !== 'fixed') {
      return false
    }
  }
  // 显式隐藏
  const style = getComputedStyle(el)
  if (style.visibility === 'hidden') {
    return false
  }
  return true
}

/**
 * 获取容器内所有可聚焦且可见的元素
 */
function getFocusableElements(container) {
  const candidates = container.querySelectorAll(FOCUSABLE_SELECTOR)
  return Array.from(candidates).filter(isVisible)
}

/**
 * @param {import('vue').Ref<HTMLElement|null>} containerRef - 容器元素 ref
 * @returns {{ activate: () => void, deactivate: () => void }}
 */
export function useFocusTrap(containerRef) {
  let handler = null

  function onKeyDown(e) {
    if (e.key !== 'Tab') return

    const container = containerRef.value
    if (!container) return

    const focusable = getFocusableElements(container)

    // 无可聚焦元素时，阻止 Tab 跳出，焦点留在容器
    if (focusable.length === 0) {
      e.preventDefault()
      container.focus()
      return
    }

    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const index = focusable.indexOf(document.activeElement)

    // 当前焦点不在可聚焦列表中（如容器本身 tabindex="-1"），
    // 拦截并跳到首/末元素，防止焦点逃出
    if (index === -1) {
      e.preventDefault()
      ;(e.shiftKey ? last : first).focus()
      return
    }

    if (e.shiftKey) {
      // Shift+Tab：到第一个时跳到最后一个
      if (document.activeElement === first) {
        e.preventDefault()
        last.focus()
      }
    } else {
      // Tab：到最后一个时跳到第一个
      if (document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  function activate() {
    const container = containerRef.value
    if (!container) return
    handler = onKeyDown
    container.addEventListener('keydown', handler)
  }

  function deactivate() {
    const container = containerRef.value
    if (!container || !handler) return
    container.removeEventListener('keydown', handler)
    handler = null
  }

  return { activate, deactivate }
}
