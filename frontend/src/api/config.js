import api from './index'

export const getConfig = (key) => api.get(`/admin/config/${key}`).then((r) => r.data)
export const setConfig = (key, value) =>
  api.put(`/admin/config/${key}`, { value }).then((r) => r.data)
