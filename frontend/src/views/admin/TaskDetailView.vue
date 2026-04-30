<template>
  <div class="hub-page">
    <div class="hub-page__head">
      <AppButton variant="ghost" size="sm" @click="router.back()">返回</AppButton>
      <h1 class="hub-page__title">任务详情</h1>
    </div>

    <div v-if="error" class="hub-page__error">{{ error }}</div>
    <div v-else-if="!detail" class="hub-page__hint">加载中…</div>
    <template v-else>
      <section class="hub-detail__card">
        <h3 class="hub-detail__title">基本信息</h3>
        <div class="hub-detail__rows">
          <div><span>Task ID</span><strong class="std-num">{{ detail.task_log.task_id }}</strong></div>
          <div><span>类型</span><strong>{{ detail.task_log.task_type }}</strong></div>
          <div><span>状态</span><strong><AppBadge :variant="statusVariant(detail.task_log.status)">{{ statusLabel(detail.task_log.status) }}</AppBadge></strong></div>
          <div><span>渠道用户</span><strong class="std-num">{{ detail.task_log.channel_userid }}</strong></div>
          <div><span>解析器</span><strong>{{ detail.task_log.intent_parser || '-' }}</strong></div>
          <div><span>置信度</span><strong class="std-num">{{ detail.task_log.intent_confidence != null ? detail.task_log.intent_confidence.toFixed(2) : '-' }}</strong></div>
          <div><span>创建时间</span><strong>{{ fmtDateTime(detail.task_log.created_at) }}</strong></div>
          <div><span>结束时间</span><strong>{{ fmtDateTime(detail.task_log.finished_at) }}</strong></div>
          <div><span>耗时</span><strong class="std-num">{{ detail.task_log.duration_ms ?? '-' }} ms</strong></div>
          <div><span>重试次数</span><strong class="std-num">{{ detail.task_log.retry_count ?? 0 }}</strong></div>
        </div>
        <div v-if="detail.task_log.error_summary" class="hub-detail__error">
          <span>错误摘要：</span>
          <code>{{ detail.task_log.error_summary }}</code>
        </div>
      </section>

      <section class="hub-detail__card">
        <h3 class="hub-detail__title">载荷</h3>
        <p v-if="!detail.payload" class="hub-page__hint">payload 已超期或不存在。</p>
        <template v-else>
          <div class="hub-detail__field">
            <span class="hub-detail__field-label">用户输入</span>
            <pre class="hub-detail__pre">{{ detail.payload.request_text }}</pre>
          </div>
          <div class="hub-detail__field">
            <span class="hub-detail__field-label">系统响应</span>
            <pre class="hub-detail__pre">{{ detail.payload.response }}</pre>
          </div>
          <div class="hub-detail__field">
            <span class="hub-detail__field-label">ERP 调用（{{ detail.payload.erp_calls.length }} 条）</span>
            <ol class="hub-detail__erp">
              <li v-for="(c, i) in detail.payload.erp_calls" :key="i">
                <code class="std-num">{{ c.method || '?' }} {{ c.path || '?' }}</code>
                <span class="hub-detail__erp-status">{{ c.status_code ?? '-' }}</span>
                <span class="hub-detail__erp-dur" v-if="c.duration_ms != null">{{ c.duration_ms }} ms</span>
              </li>
              <li v-if="!detail.payload.erp_calls.length" class="hub-detail__erp-empty">无</li>
            </ol>
          </div>
        </template>
      </section>

      <!-- Plan 6 Task 13：Agent 决策链（仅当 conversation_log 非空时显示）-->
      <section v-if="detail.conversation_log" class="hub-detail__card">
        <h3 class="hub-detail__title">Agent 决策链</h3>

        <!-- 汇总指标 -->
        <div class="hub-agent__summary">
          <div class="hub-agent__summary-item">
            <span class="hub-agent__summary-label">LLM 轮数</span>
            <span class="hub-agent__summary-value std-num">{{ detail.conversation_log.rounds_count ?? 0 }}</span>
          </div>
          <div class="hub-agent__summary-item">
            <span class="hub-agent__summary-label">Token 用量</span>
            <span class="hub-agent__summary-value std-num">{{ formatNumber(detail.conversation_log.tokens_used) }}</span>
          </div>
          <div class="hub-agent__summary-item">
            <span class="hub-agent__summary-label">估算成本</span>
            <span class="hub-agent__summary-value std-num">
              {{ detail.conversation_log.tokens_cost_yuan != null
                  ? '¥' + formatCost(detail.conversation_log.tokens_cost_yuan)
                  : '-' }}
            </span>
          </div>
          <div class="hub-agent__summary-item">
            <span class="hub-agent__summary-label">状态</span>
            <AppBadge :variant="conversationStatusVariant(detail.conversation_log.final_status)">
              {{ conversationStatusLabel(detail.conversation_log.final_status) }}
            </AppBadge>
          </div>
        </div>

        <!-- 对话错误摘要 -->
        <div v-if="detail.conversation_log.error_summary" class="hub-detail__error">
          <span>错误：</span>{{ detail.conversation_log.error_summary }}
        </div>

        <!-- 工具调用时间线 -->
        <template v-if="detail.tool_calls && detail.tool_calls.length > 0">
          <div class="hub-agent__tool-header">
            工具调用（{{ detail.tool_calls.length }} 次）
          </div>
          <div
            v-for="tc in detail.tool_calls"
            :key="tc.id"
            class="hub-agent__tool-item"
          >
            <div class="hub-agent__tool-row">
              <span class="hub-agent__round-tag">Round {{ tc.round_idx }}</span>
              <strong class="hub-agent__tool-name">{{ tc.tool_name }}</strong>
              <span class="hub-agent__duration">{{ tc.duration_ms != null ? tc.duration_ms + ' ms' : '-' }}</span>
              <AppBadge v-if="tc.error" variant="error">失败</AppBadge>
              <AppBadge v-else variant="success">成功</AppBadge>
            </div>
            <details class="hub-agent__tool-detail">
              <summary class="hub-agent__tool-summary">展开 args / result</summary>
              <div class="hub-agent__kv">
                <span class="hub-agent__kv-label">args:</span>
                <pre class="hub-detail__pre">{{ JSON.stringify(tc.args_json, null, 2) }}</pre>
              </div>
              <div class="hub-agent__kv">
                <span class="hub-agent__kv-label">result:</span>
                <pre class="hub-detail__pre">{{ tc.result_json != null ? JSON.stringify(tc.result_json, null, 2) : '(无)' }}</pre>
              </div>
              <div v-if="tc.error" class="hub-agent__kv hub-agent__kv--error">
                <span class="hub-agent__kv-label">error:</span>
                <pre class="hub-detail__pre hub-detail__pre--error">{{ tc.error }}</pre>
              </div>
            </details>
          </div>
        </template>
        <p v-else class="hub-page__hint">
          未记录到工具调用 —— 可能是 LLM 直接文字回答或对话失败。
        </p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getTaskDetail } from '../../api/tasks'
import { pickErrorDetail } from '../../api'
import { fmtDateTime, statusLabel, statusVariant } from '../../utils/format'
import AppButton from '../../components/ui/AppButton.vue'
import AppBadge from '../../components/ui/AppBadge.vue'

const route = useRoute()
const router = useRouter()
const detail = ref(null)
const error = ref('')

async function load() {
  try {
    detail.value = await getTaskDetail(route.params.taskId)
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

onMounted(load)

// ── Plan 6 Task 13：Agent 决策链辅助函数 ──────────────────────────────────

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
    failed_user: '失败（用户层）',
    failed_system: '失败（系统）',
    fallback_to_rule: '已降级规则解析',
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
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__head { display: flex; align-items: center; gap: 12px; }
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__hint { font-size: 12px; color: var(--text-muted); margin: 0; padding: 12px 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
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
.hub-detail__rows {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 16px;
  font-size: 13px;
}
.hub-detail__rows > div { display: flex; gap: 6px; align-items: center; }
.hub-detail__rows > div span { color: var(--text-muted); min-width: 80px; }
.hub-detail__error {
  background: color-mix(in srgb, var(--error) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--error);
}
.hub-detail__field { display: flex; flex-direction: column; gap: 6px; }
.hub-detail__field-label { font-size: 12px; color: var(--text-muted); }
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
.hub-detail__erp {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hub-detail__erp li {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  background: var(--elevated);
  border-radius: 6px;
  padding: 6px 10px;
}
.hub-detail__erp-status {
  font-family: var(--font-mono);
  color: var(--text-muted);
  min-width: 36px;
}
.hub-detail__erp-dur { color: var(--text-muted); font-family: var(--font-mono); }
.hub-detail__erp-empty {
  color: var(--text-muted);
  background: transparent !important;
  font-style: italic;
}
.hub-detail__pre--error { color: var(--error); }

/* ── Plan 6 Task 13：Agent 决策链 ──────────────────────────────────── */
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
</style>
