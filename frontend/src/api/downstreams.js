import api from './index'

export const listDownstreams = () => api.get('/admin/downstreams').then((r) => r.data)
export const createDownstream = (body) =>
  api.post('/admin/downstreams', body).then((r) => r.data)
export const updateDownstreamApiKey = (id, body) =>
  api.put(`/admin/downstreams/${id}/apikey`, body).then((r) => r.data)
export const testDownstream = (id) =>
  api.post(`/admin/downstreams/${id}/test-connection`).then((r) => r.data)
export const disableDownstream = (id) =>
  api.post(`/admin/downstreams/${id}/disable`).then((r) => r.data)
