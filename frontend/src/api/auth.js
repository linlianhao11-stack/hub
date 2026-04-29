import api from './index'

export const login = (username, password) =>
  api.post('/admin/login', { username, password }).then((r) => r.data)
export const logout = () => api.post('/admin/logout').then((r) => r.data)
export const me = () => api.get('/admin/me').then((r) => r.data)
