import api from './index'

/**
 * 实时会话 SSE：返回原生 EventSource。
 * 调用方负责 onmessage / onerror / close。
 */
export function openConversationLive() {
  return new EventSource('/hub/v1/admin/conversation/live', { withCredentials: true })
}

export const listConversationHistory = (params = {}) =>
  api.get('/admin/conversation/history', { params }).then((r) => r.data)

export const getConversationDetail = (taskId) =>
  api.get(`/admin/conversation/history/${taskId}`).then((r) => r.data)
