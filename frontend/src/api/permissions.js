import api from './index'

export const listPermissions = () => api.get('/admin/hub-permissions').then((r) => r.data)
