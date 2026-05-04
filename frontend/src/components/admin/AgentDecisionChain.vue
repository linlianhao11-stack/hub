<template>
  <section class="hub-detail__card">
    <h3 class="hub-detail__title">对话决策记录</h3>

    <!-- 汇总指标 -->
    <div class="hub-agent__summary">
      <div class="hub-agent__summary-item">
        <span class="hub-agent__summary-label">对话轮数</span>
        <span class="hub-agent__summary-value std-num">{{ conversationLog.rounds_count ?? 0 }}</span>
      </div>
      <div class="hub-agent__summary-item">
        <span class="hub-agent__summary-label">Token 消耗</span>
        <span class="hub-agent__summary-value std-num">{{ formatNumber(conversationLog.tokens_used) }}</span>
      </div>
      <div class="hub-agent__summary-item">
        <span class="hub-agent__summary-label">估算成本</span>
        <span class="hub-agent__summary-value std-num">
          {{ conversationLog.tokens_cost_yuan != null
              ? '¥' + formatCost(conversationLog.tokens_cost_yuan)
              : '-' }}
        </span>
      </div>
      <div class="hub-agent__summary-item">
        <span class="hub-agent__summary-label">状态</span>
        <AppBadge :variant="conversationStatusVariant(conversationLog.final_status)">
          {{ conversationStatusLabel(conversationLog.final_status) }}
        </AppBadge>
      </div>
      <div class="hub-agent__summary-item">
        <span class="hub-agent__summary-label">开始时间</span>
        <span class="hub-agent__summary-value">{{ fmtDateTime(conversationLog.started_at) }}</span>
      </div>
      <div class="hub-agent__summary-item" v-if="conversationLog.ended_at">
        <span class="hub-agent__summary-label">结束时间</span>
        <span class="hub-agent__summary-value">{{ fmtDateTime(conversationLog.ended_at) }}</span>
      </div>
    </div>

    <!-- 对话错误摘要 -->
    <div v-if="conversationLog.error_summary" class="hub-detail__error">
      <strong>错误详情：</strong>{{ fmtErrorSummary(conversationLog.error_summary) }}
    </div>

    <!-- 工具调用时间线 -->
    <template v-if="toolCalls && toolCalls.length > 0">
      <div class="hub-agent__tool-header">
        工具调用（{{ toolCalls.length }} 次）
      </div>
      <div
        v-for="tc in toolCalls"
        :key="tc.id"
        class="hub-agent__tool-item"
      >
        <div class="hub-agent__tool-row">
          <span class="hub-agent__round-tag">第 {{ tc.round_idx + 1 }} 轮</span>
          <strong class="hub-agent__tool-name">{{ tc.tool_name }}</strong>
          <span class="hub-agent__duration">{{ tc.duration_ms != null ? tc.duration_ms + ' ms' : '-' }}</span>
          <span class="hub-agent__called-at" v-if="tc.called_at">
            {{ fmtRelativeTime(tc.called_at, conversationLog.started_at) }}
          </span>
          <AppBadge v-if="tc.error" variant="error">失败</AppBadge>
          <AppBadge v-else variant="success">成功</AppBadge>
        </div>
        <details class="hub-agent__tool-detail">
          <summary class="hub-agent__tool-summary">展开请求参数与返回详情</summary>
          <div class="hub-agent__kv">
            <span class="hub-agent__kv-label">请求参数：</span>
            <pre class="hub-detail__pre">{{ JSON.stringify(tc.args_json, null, 2) }}</pre>
          </div>
          <div class="hub-agent__kv">
            <span class="hub-agent__kv-label">返回结果：</span>
            <pre class="hub-detail__pre">{{ tc.result_json != null ? JSON.stringify(tc.result_json, null, 2) : '(无)' }}</pre>
          </div>
          <div v-if="tc.error" class="hub-agent__kv hub-agent__kv--error">
            <span class="hub-agent__kv-label">错误详情：</span>
            <pre class="hub-detail__pre hub-detail__pre--error">{{ tc.error }}</pre>
          </div>
        </details>
      </div>
    </template>
    <p v-else class="hub-page__hint">
      未记录到工具调用 —— 可能是直接文字回答或对话失败。
    </p>
  </section>
</template>

<script setup>
import { fmtDateTime } from '../../utils/format'
import AppBadge from '../ui/AppBadge.vue'

defineProps({
  conversationLog: {
    type: Object,
    required: true,
  },
  toolCalls: {
    type: Array,
    default: () => [],
  },
})

function formatNumber(n) {
  if (n === null || n === undefined) return '-'
  return Number(n).toLocaleString('zh-CN')
}

function formatCost(yuan) {
  if (yuan === null || yuan === undefined) return '0.0000'
  return Number(yuan).toFixed(4)
}

function conversationStatusLabel(status) {
  const labels = {
    success: '完成',
    failed_user: '用户问题导致失败',
    failed_system: '系统出错',
    fallback_to_rule: '已切换简单规则解析',
  }
  return labels[status] || status || '-'
}

function conversationStatusVariant(status) {
  const variants = {
    success: 'success',
    failed_user: 'warning',
    failed_system: 'error',
    fallback_to_rule: 'info',
  }
  return variants[status] || 'gray'
}

function fmtRelativeTime(callTime, startTime) {
  if (!callTime || !startTime) return ''
  const diffMs = new Date(callTime) - new Date(startTime)
  const seconds = (diffMs / 1000).toFixed(1)
  return `+${seconds}s`
}

function fmtErrorSummary(text) {
  if (!text) return ''
  const dict = [
    [/tiktoken encoding/i, '上下文计算异常'],
    [/timeout/i, '响应超时'],
    [/connection/i, '连接异常'],
    [/redis/i, 'Redis 故障'],
  ]
  for (const [pattern, replacement] of dict) {
    if (pattern.test(text)) return replacement
  }
  return text
}
</script>

<style scoped>
.hub-agent__summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.hub-agent__summary-item { display: flex; flex-direction: column; gap: 4px; }
.hub-agent__summary-label { font-size: 12px; color: var(--text-muted); }
.hub-agent__summary-value { font-size: 14px; font-weight: 500; color: var(--text); }

.hub-agent__tool-header {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.hub-agent__tool-item {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: var(--elevated);
}
.hub-agent__tool-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.hub-agent__round-tag {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
}
.hub-agent__tool-name {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text);
}
.hub-agent__duration {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-muted);
}
.hub-agent__called-at {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
  opacity: 0.75;
}
.hub-agent__tool-detail {
  margin-top: 8px;
}
.hub-agent__tool-summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--text-muted);
  padding: 2px 0;
  user-select: none;
}
.hub-agent__tool-summary:hover { color: var(--text); }
.hub-agent__kv {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hub-agent__kv-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
  font-family: var(--font-mono);
}
.hub-agent__kv--error .hub-agent__kv-label { color: var(--error); }

/* 从父组件借用（需确保父级定义这些 class） */
.hub-detail__card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hub-detail__title { font-size: 13px; font-weight: 600; color: var(--text); margin: 0; }
.hub-detail__error {
  background: color-mix(in srgb, var(--error) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--error);
}
.hub-detail__pre {
  background: var(--elevated);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 240px;
  overflow: auto;
}
.hub-detail__pre--error { color: var(--error); }
.hub-page__hint { font-size: 12px; color: var(--text-muted); margin: 0; padding: 12px 0; }
</style>
