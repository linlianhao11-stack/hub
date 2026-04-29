import api from './index'

export const getDashboard = () => api.get('/admin/dashboard').then((r) => r.data)
export const getHealth = () => api.get('/health').then((r) => r.data)
