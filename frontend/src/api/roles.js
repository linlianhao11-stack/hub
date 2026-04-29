import api from './index'

export const listRoles = () => api.get('/admin/hub-roles').then((r) => r.data)
