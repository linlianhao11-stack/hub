import api from './index'

export const listChannels = () => api.get('/admin/channels').then((r) => r.data)
export const createChannel = (body) =>
  api.post('/admin/channels', body).then((r) => r.data)
export const updateChannel = (id, body) =>
  api.put(`/admin/channels/${id}`, body).then((r) => r.data)
export const disableChannel = (id) =>
  api.post(`/admin/channels/${id}/disable`).then((r) => r.data)
