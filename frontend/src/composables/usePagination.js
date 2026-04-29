import { computed, ref } from 'vue'

/**
 * 简单分页 helper：生成 visiblePages 列表，配合 AppPagination 使用。
 *
 * @param {Object} opts
 * @param {() => number} opts.totalRef    数据总条数 ref/computed 取值
 * @param {() => number} [opts.pageSize]  默认 20
 */
export function usePagination(opts = {}) {
  const page = ref(1)
  const pageSize = ref(opts.pageSize ?? 20)

  const total = computed(() => (typeof opts.total === 'function' ? opts.total() : 0))
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

  const visiblePages = computed(() => {
    const t = totalPages.value
    const p = page.value
    if (t <= 7) return Array.from({ length: t }, (_, i) => i + 1)
    const arr = []
    arr.push(1)
    if (p > 3) arr.push('…')
    const start = Math.max(2, p - 1)
    const end = Math.min(t - 1, p + 1)
    for (let i = start; i <= end; i++) arr.push(i)
    if (p < t - 2) arr.push('…')
    arr.push(t)
    return arr
  })

  function reset() {
    page.value = 1
  }

  return { page, pageSize, total, totalPages, visiblePages, reset }
}
