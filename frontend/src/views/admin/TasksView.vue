<template>
  <div class="hub-page">
    <h1 class="hub-page__title">任务列表</h1>
    <p class="hub-page__hint">所有 HUB 入站消息生成的任务。点击行进入详情。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <AppInput v-model="filter.user_id" size="toolbar" placeholder="渠道用户 ID" />
      <AppSelect v-model="filter.task_type" size="toolbar" placeholder="所有类型" :options="typeOptions" />
      <AppSelect v-model="filter.status" size="toolbar" placeholder="所有状态" :options="statusOptions" />
      <AppSelect v-model.number="filter.since_hours" size="toolbar" :options="hoursOptions" />
      <AppButton variant="secondary" size="sm" @click="onSearch">查询</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="无任务">
        <template #header>
          <tr>
            <th class="app-th">任务 ID</th>
            <th class="app-th">类型</th>
            <th class="app-th">渠道用户</th>
            <th class="app-th">状态</th>
            <th class="app-th">解析器</th>
            <th class="app-th text-right">置信度</th>
            <th class="app-th text-right">耗时 ms</th>
            <th class="app-th">创建时间</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="t in items" :key="t.task_id">
          <td class="app-td std-num">{{ shortId(t.task_id) }}</td>
          <td class="app-td">{{ taskTypeLabel(t.task_type) }}</td>
          <td class="app-td std-num">{{ t.channel_userid }}</td>
          <td class="app-td"><AppBadge :variant="statusVariant(t.status)">{{ statusLabel(t.status) }}</AppBadge></td>
          <td class="app-td text-muted">{{ t.intent_parser || '-' }}</td>
          <td class="app-td text-right std-num">{{ t.intent_confidence != null ? t.intent_confidence.toFixed(2) : '-' }}</td>
          <td class="app-td text-right std-num">{{ t.duration_ms ?? '-' }}</td>
          <td class="app-td text-muted">{{ fmtDateTime(t.created_at) }}</td>
          <td class="app-td text-right">
            <AppButton variant="primary" size="xs" @click="goDetail(t)">详情</AppButton>
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
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listTasks } from '../../api/tasks'
import { pickErrorDetail } from '../../api'
import { fmtDateTime, statusLabel, statusVariant } from '../../utils/format'
import { usePagination } from '../../composables/usePagination'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppPagination from '../../components/ui/AppPagination.vue'

const router = useRouter()
const items = ref([])
const totalCount = ref(0)
const error = ref('')

const filter = reactive({
  user_id: '',
  task_type: '',
  status: '',
  since_hours: 24,
})

const typeOptions = [
  { value: '', label: '所有类型' },
  { value: 'dingtalk_inbound', label: '钉钉入站' },
  { value: 'dingtalk_outbound', label: '钉钉外发' },
]
const statusOptions = [
  { value: '', label: '所有状态' },
  { value: 'pending', label: '排队中' },
  { value: 'running', label: '运行中' },
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

const TASK_TYPE_LABEL = {
  dingtalk_inbound: '钉钉入站',
  dingtalk_outbound: '钉钉外发',
}
function taskTypeLabel(t) {
  return TASK_TYPE_LABEL[t] || t
}

function shortId(id) {
  if (!id) return '-'
  return id.length > 8 ? `${id.slice(0, 8)}…` : id
}

const { page, pageSize, totalPages, visiblePages, reset } = usePagination({
  total: () => totalCount.value,
  pageSize: 20,
})

async function load() {
  try {
    const params = { page: page.value, page_size: pageSize.value, since_hours: filter.since_hours }
    if (filter.user_id) params.user_id = filter.user_id
    if (filter.task_type) params.task_type = filter.task_type
    if (filter.status) params.status = filter.status
    const data = await listTasks(params)
    items.value = data.items || []
    totalCount.value = data.total || 0
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function onSearch() { reset(); load() }
function onPageChange(p) { page.value = p; load() }

function goDetail(t) {
  router.push(`/admin/tasks/${t.task_id}`)
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
</style>
