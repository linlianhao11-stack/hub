import api from './index'

export const listAudit = (params = {}) =>
  api.get('/admin/audit', { params }).then((r) => r.data)

export const listMetaAudit = (params = {}) =>
  api.get('/admin/audit/meta', { params }).then((r) => r.data)
