/** 统一时间格式化（本地时区，YYYY-MM-DD HH:mm）。 */
export function fmtDateTime(value) {
  if (!value) return '-'
  const d = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 短日期 YYYY-MM-DD。 */
export function fmtDate(value) {
  if (!value) return '-'
  const d = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 状态枚举 → 中文标签。 */
const STATUS_LABEL = {
  // 通用
  active: '运行中',
  disabled: '已停用',
  pending: '排队中',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  done: '完成',
  // 绑定
  revoked: '已解绑',
  // 任务
  awaiting_approval: '等待人工审批',
  approved: '已审批',
  rejected: '已拒绝',
  expired: '已过期',
  rate_limited: '限流',
  unknown: '未知',
}

export function statusLabel(value) {
  if (!value) return '-'
  return STATUS_LABEL[value] || value
}

/** 状态枚举 → AppBadge variant。 */
const STATUS_VARIANT = {
  active: 'success',
  succeeded: 'success',
  approved: 'success',
  done: 'success',
  pending: 'warning',
  awaiting_approval: 'warning',
  running: 'info',
  failed: 'error',
  rejected: 'error',
  rate_limited: 'error',
  expired: 'gray',
  cancelled: 'gray',
  revoked: 'gray',
  disabled: 'gray',
}

export function statusVariant(value) {
  return STATUS_VARIANT[value] || 'gray'
}

/** 渠道类型中文。 */
export function channelLabel(channel) {
  return { dingtalk: '钉钉', wechat_mp: '微信公众号' }[channel] || channel
}

/** 下游类型中文。 */
export function downstreamLabel(t) {
  return { erp: 'ERP 系统', wms: '仓储 WMS', crm: '客户 CRM' }[t] || t
}
