import api from './index'

export const getAiDefaults = () => api.get('/admin/ai-providers/defaults').then((r) => r.data)
export const listAi = () => api.get('/admin/ai-providers').then((r) => r.data)
export const createAi = (body) => api.post('/admin/ai-providers', body).then((r) => r.data)
export const testAiChat = (id) => api.post(`/admin/ai-providers/${id}/test-chat`).then((r) => r.data)
export const setAiActive = (id) => api.post(`/admin/ai-providers/${id}/set-active`).then((r) => r.data)
