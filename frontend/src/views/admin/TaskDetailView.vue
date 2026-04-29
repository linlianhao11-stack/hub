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
</style>
