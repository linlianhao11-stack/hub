import api from './index'

export const listTasks = (params = {}) =>
  api.get('/admin/tasks', { params }).then((r) => r.data)
export const getTaskDetail = (taskId) =>
  api.get(`/admin/tasks/${taskId}`).then((r) => r.data)
