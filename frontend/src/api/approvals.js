/**
 * 审批 inbox API（Plan 6 Task 12）
 * baseURL = /hub/v1（由 api 实例统一加前缀）
 * 三类：voucher（凭证）/ price（调价）/ stock（库存调整）
 */
import api from './index'

export const approvalsApi = {
  // ===== 凭证 =====
  listVoucher({ status = 'pending', limit = 20, offset = 0 } = {}, axiosConfig = {}) {
    return api.get('/admin/approvals/voucher', {
      params: { status, limit, offset },
      ...axiosConfig,
    })
  },
  batchApproveVoucher(draft_ids) {
    return api.post('/admin/approvals/voucher/batch-approve', { draft_ids })
  },
  batchRejectVoucher(draft_ids, reason) {
    return api.post('/admin/approvals/voucher/batch-reject', { draft_ids, reason })
  },

  // ===== 调价 =====
  listPrice({ status = 'pending', limit = 20, offset = 0 } = {}, axiosConfig = {}) {
    return api.get('/admin/approvals/price', {
      params: { status, limit, offset },
      ...axiosConfig,
    })
  },
  batchApprovePrice(request_ids) {
    return api.post('/admin/approvals/price/batch-approve', { request_ids })
  },
  batchRejectPrice(request_ids, reason) {
    return api.post('/admin/approvals/price/batch-reject', { request_ids, reason })
  },

  // ===== 库存调整 =====
  listStock({ status = 'pending', limit = 20, offset = 0 } = {}, axiosConfig = {}) {
    return api.get('/admin/approvals/stock', {
      params: { status, limit, offset },
      ...axiosConfig,
    })
  },
  batchApproveStock(request_ids) {
    return api.post('/admin/approvals/stock/batch-approve', { request_ids })
  },
  batchRejectStock(request_ids, reason) {
    return api.post('/admin/approvals/stock/batch-reject', { request_ids, reason })
  },
}
