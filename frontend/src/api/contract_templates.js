/**
 * 合同模板管理 API（Plan 6 Task 11）
 * baseURL = /hub/v1（由 api 实例统一加前缀）
 */
import api from './index'

export const contractTemplatesApi = {
  /** 列表查询（支持按 template_type / is_active 筛选）。 */
  list({ template_type, is_active, limit = 100, offset = 0 } = {}) {
    const params = { limit, offset }
    if (template_type !== undefined && template_type !== null && template_type !== '') {
      params.template_type = template_type
    }
    if (is_active !== undefined && is_active !== null) {
      params.is_active = is_active
    }
    return api.get('/admin/contract-templates', { params })
  },

  /** 上传 docx 模板文件（multipart/form-data）。 */
  upload({ name, template_type, description, file }) {
    const formData = new FormData()
    formData.append('name', name)
    formData.append('template_type', template_type)
    formData.append('description', description || '')
    formData.append('file', file)
    return api.post('/admin/contract-templates', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** 获取模板已解析的占位符列表。 */
  getPlaceholders(id) {
    return api.get(`/admin/contract-templates/${id}/placeholders`)
  },

  /** 更新模板元信息（name / template_type / description，不重传文件）。 */
  update(id, payload) {
    return api.put(`/admin/contract-templates/${id}`, payload)
  },

  /** 停用模板（is_active → false）。 */
  disable(id) {
    return api.post(`/admin/contract-templates/${id}/disable`)
  },

  /** 启用模板（is_active → true）。 */
  enable(id) {
    return api.post(`/admin/contract-templates/${id}/enable`)
  },
}
