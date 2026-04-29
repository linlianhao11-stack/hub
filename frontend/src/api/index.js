import axios from 'axios'
import router from '../router'

/**
 * HUB axios 实例：
 * - baseURL = /hub/v1
 * - 401 跳 /login（除登录接口本身）
 */
const api = axios.create({
  baseURL: '/hub/v1',
  timeout: 30000,
  withCredentials: true,
})

let _redirecting = false

api.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status
    const url = error.config?.url || ''
    if (status === 401 && !url.includes('/admin/login')) {
      if (!_redirecting && !location.pathname.startsWith('/login')) {
        _redirecting = true
        router.replace('/login').finally(() => {
          _redirecting = false
        })
      }
    }
    return Promise.reject(error)
  },
)

/** 提取 axios 错误 detail（中文，避免暴露 code）。 */
export function pickErrorDetail(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((e) => e.msg || '输入格式不正确').join('；')
  }
  return fallback
}

export default api
