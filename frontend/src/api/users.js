import api from './index'

export const listHubUsers = (params = {}) =>
  api.get('/admin/hub-users', { params }).then((r) => r.data)
export const getHubUser = (id) => api.get(`/admin/hub-users/${id}`).then((r) => r.data)
export const assignRoles = (id, roleIds) =>
  api.put(`/admin/hub-users/${id}/roles`, { role_ids: roleIds }).then((r) => r.data)
export const updateDownstreamIdentity = (id, body) =>
  api.put(`/admin/hub-users/${id}/downstream-identity`, body).then((r) => r.data)
export const forceUnbind = (id, channelType) =>
  api
    .post(`/admin/hub-users/${id}/force-unbind`, null, { params: { channel_type: channelType } })
    .then((r) => r.data)
