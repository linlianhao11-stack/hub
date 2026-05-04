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
  success: '成功',                           // task_log.final_status / Plan 6 ChainAgent
  failed: '失败',
  failed_user: '用户问题导致失败',             // task_log.final_status
  failed_system: '系统出错',
  failed_system_final: '系统出错',
  fallback_to_rule: '已切换简单规则解析',      // ChainAgent 异常降级 RuleParser
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
  success: 'success',                        // ChainAgent 成功 → 绿色徽章
  approved: 'success',
  done: 'success',
  pending: 'warning',
  awaiting_approval: 'warning',
  fallback_to_rule: 'warning',               // 降级了但用户仍拿到结果——黄色提醒
  running: 'info',
  failed: 'error',
  failed_user: 'error',                      // 用户输入有问题
  failed_system: 'error',
  failed_system_final: 'error',              // 系统失败
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

/** 解析器枚举 → 中文标签（agent / rule / null）。 */
const PARSER_LABEL = {
  agent: 'AI Agent',
  rule: '规则匹配',
  llm: 'LLM',
}

export function parserLabel(value) {
  if (!value) return '—'
  return PARSER_LABEL[value] || value
}

/** 置信度展示：null 时显示 "—"（agent 路径无 discrete confidence），否则保留 2 位小数。 */
export function confidenceLabel(value) {
  if (value == null) return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return '—'
  return n.toFixed(2)
}

/** 渠道类型中文。 */
export function channelLabel(channel) {
  return { dingtalk: '钉钉', wechat_mp: '微信公众号' }[channel] || channel
}

/** 下游类型中文。 */
export function downstreamLabel(t) {
  return { erp: 'ERP 系统', wms: '仓储 WMS', crm: '客户 CRM' }[t] || t
}
