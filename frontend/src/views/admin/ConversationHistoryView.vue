<template>
  <div class="hub-page">
    <h1 class="hub-page__title">历史会话</h1>
    <p class="hub-page__hint">查看历史钉钉入站消息任务。点击「详情」会触发 meta 审计。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <AppInput v-model="filter.channel_userid" size="toolbar" placeholder="渠道用户 ID" />
      <AppInput v-model="filter.keyword" size="toolbar" icon="search" placeholder="搜索错误关键字…" />
      <AppSelect v-model="filter.status" size="toolbar" :options="statusOptions" />
      <AppSelect v-model.number="filter.since_hours" size="toolbar" :options="hoursOptions" />
      <AppButton variant="secondary" size="sm" @click="onSearch">查询</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="无数据">
        <template #header>
          <tr>
            <th class="app-th">时间</th>
            <th class="app-th">渠道用户</th>
            <th class="app-th">解析器</th>
            <th class="app-th text-right">置信度</th>
            <th class="app-th">状态</th>
            <th class="app-th">错误摘要</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="t in items" :key="t.task_id">
          <td class="app-td text-muted">{{ fmtDateTime(t.created_at) }}</td>
          <td class="app-td std-num">{{ t.channel_userid }}</td>
          <td class="app-td">{{ parserLabel(t.intent_parser) }}</td>
          <td class="app-td text-right std-num">{{ confidenceLabel(t.intent_confidence) }}</td>
          <td class="app-td"><AppBadge :variant="statusVariant(t.status)">{{ statusLabel(t.status) }}</AppBadge></td>
          <td class="app-td text-muted hub-truncate">{{ t.error_summary || '—' }}</td>
          <td class="app-td text-right">
            <AppButton variant="primary" size="xs" @click="openDetail(t)">详情</AppButton>
          </td>
        </tr>
        <template #footer>
          <span class="app-footer-stats">共 {{ totalCount }} 条</span>
          <AppPagination
            v-if="totalPages > 1"
            :page="page"
            :total-pages="totalPages"
            :visible-pages="visiblePages"
            @update:page="onPageChange"
          />
        </template>
      </AppTable>
    </AppCard>

    <AppModal :visible="!!detail" :title="detail ? `会话详情：${shortId(detail.task_log?.task_id)}` : ''" size="lg" @update:visible="(v) => { if (!v) detail = null }">
      <div v-if="detailError" class="hub-page__error">{{ detailError }}</div>
      <div v-else-if="!detail" class="hub-page__hint">加载中…</div>
      <div v-else class="hub-detail">
        <div class="hub-detail__rows">
          <div><span>状态</span><strong><AppBadge :variant="statusVariant(detail.task_log.status)">{{ statusLabel(detail.task_log.status) }}</AppBadge></strong></div>
          <div><span>渠道用户</span><strong class="std-num">{{ detail.task_log.channel_userid }}</strong></div>
          <div><span>耗时</span><strong class="std-num">{{ detail.task_log.duration_ms ?? '-' }} ms</strong></div>
          <div><span>创建</span><strong>{{ fmtDateTime(detail.task_log.created_at) }}</strong></div>
        </div>
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
        </template>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="detail = null">关闭</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { listConversationHistory, getConversationDetail } from '../../api/conversation'
import { pickErrorDetail } from '../../api'
import { fmtDateTime, statusLabel, statusVariant, parserLabel, confidenceLabel } from '../../utils/format'
import { usePagination } from '../../composables/usePagination'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppPagination from '../../components/ui/AppPagination.vue'

const items = ref([])
const totalCount = ref(0)
const error = ref('')
const detail = ref(null)
const detailError = ref('')

const filter = reactive({
  channel_userid: '',
  keyword: '',
  status: '',
  since_hours: 24,
})

const statusOptions = [
  { value: '', label: '所有状态' },
  { value: 'success', label: '成功' },
  { value: 'failed_user', label: '用户失败' },
  { value: 'failed_system_final', label: '系统失败' },
  { value: 'awaiting_approval', label: '等待审批' },
]
const hoursOptions = [
  { value: 1, label: '近 1 小时' },
  { value: 24, label: '近 24 小时' },
  { value: 168, label: '近 7 天' },
  { value: 720, label: '近 30 天' },
]

const { page, pageSize, totalPages, visiblePages, reset } = usePagination({
  total: () => totalCount.value,
  pageSize: 20,
})

function shortId(id) {
  return id && id.length > 8 ? `${id.slice(0, 8)}…` : (id || '')
}

async function load() {
  try {
    const params = { page: page.value, page_size: pageSize.value, since_hours: filter.since_hours }
    if (filter.channel_userid) params.channel_userid = filter.channel_userid
    if (filter.status) params.status = filter.status
    if (filter.keyword) params.keyword = filter.keyword
    const data = await listConversationHistory(params)
    items.value = data.items || []
    totalCount.value = data.total || 0
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function onSearch() { reset(); load() }
function onPageChange(p) { page.value = p; load() }

async function openDetail(t) {
  detail.value = { task_log: t, payload: null, _loading: true }
  detailError.value = ''
  try {
    detail.value = await getConversationDetail(t.task_id)
  } catch (e) {
    detailError.value = pickErrorDetail(e, '加载详情失败')
  }
}

onMounted(load)
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__hint { font-size: 12px; color: var(--text-muted); margin: 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hub-truncate {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hub-detail { display: flex; flex-direction: column; gap: 12px; }
.hub-detail__rows { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 16px; font-size: 13px; }
.hub-detail__rows > div { display: flex; gap: 6px; align-items: center; }
.hub-detail__rows > div span { color: var(--text-muted); min-width: 60px; }
.hub-detail__field { display: flex; flex-direction: column; gap: 6px; }
.hub-detail__field-label { font-size: 12px; color: var(--text-muted); }
.hub-detail__pre {
  background: var(--elevated);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 240px;
  overflow: auto;
}
</style>
