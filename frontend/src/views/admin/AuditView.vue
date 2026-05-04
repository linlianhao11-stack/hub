<template>
  <div class="hub-page">
    <h1 class="hub-page__title">审计日志</h1>

    <div class="hub-tabs">
      <button :class="{ 'is-active': tab === 'main' }" @click="tab = 'main'">操作审计</button>
      <button v-if="hasMeta" :class="{ 'is-active': tab === 'meta' }" @click="tab = 'meta'">Meta 审计</button>
    </div>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <AppInput v-model.number="filter.actor_id" size="toolbar" type="number" placeholder="操作人 ID" />
      <AppInput v-if="tab === 'main'" v-model="filter.action" size="toolbar" placeholder="操作类型（如 创建、删除、修改）" />
      <AppInput v-if="tab === 'main'" v-model="filter.target_type" size="toolbar" placeholder="对象类型（如 渠道、下游系统）" />
      <AppSelect v-model.number="filter.since_hours" size="toolbar" :options="hoursOptions" />
      <AppButton variant="secondary" size="sm" @click="onSearch">查询</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="无审计记录">
        <template #header>
          <tr v-if="tab === 'main'">
            <th class="app-th">时间</th>
            <th class="app-th">操作人</th>
            <th class="app-th">动作</th>
            <th class="app-th">对象类型</th>
            <th class="app-th">对象 ID</th>
            <th class="app-th">详情</th>
            <th class="app-th">IP</th>
          </tr>
          <tr v-else>
            <th class="app-th">时间</th>
            <th class="app-th">操作人</th>
            <th class="app-th">查看的任务 ID</th>
            <th class="app-th">IP</th>
          </tr>
        </template>
        <tr v-for="row in items" :key="row.id">
          <td class="app-td text-muted">{{ fmtDateTime(row.created_at || row.viewed_at) }}</td>
          <td class="app-td">{{ row.actor_name || '?' }} <span class="text-muted">#{{ row.actor_id }}</span></td>
          <template v-if="tab === 'main'">
            <td class="app-td">{{ actionLabel(row.action) }}</td>
            <td class="app-td">{{ targetTypeLabel(row.target_type) }}</td>
            <td class="app-td std-num">{{ row.target_id }}</td>
            <td class="app-td hub-truncate"><code class="hub-detail-json">{{ formatDetail(row.detail) }}</code></td>
            <td class="app-td text-muted std-num">{{ row.ip || '-' }}</td>
          </template>
          <template v-else>
            <td class="app-td std-num">{{ row.viewed_task_id }}</td>
            <td class="app-td text-muted std-num">{{ row.ip || '-' }}</td>
          </template>
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { listAudit, listMetaAudit } from '../../api/audit'
import { pickErrorDetail } from '../../api'
import { fmtDateTime } from '../../utils/format'
import { useAuthStore } from '../../stores/auth'
import { usePagination } from '../../composables/usePagination'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppPagination from '../../components/ui/AppPagination.vue'

const auth = useAuthStore()
const tab = ref('main')
const items = ref([])
const totalCount = ref(0)
const error = ref('')

const filter = reactive({
  actor_id: null,
  action: '',
  target_type: '',
  since_hours: 168,
})

const hoursOptions = [
  { value: 24, label: '近 24 小时' },
  { value: 168, label: '近 7 天' },
  { value: 720, label: '近 30 天' },
]

const ACTION_LABEL = {
  create_downstream: '创建下游系统',
  update_downstream_apikey: '更新下游 ApiKey',
  disable_downstream: '停用下游',
  create_channel: '创建渠道',
  update_channel: '更新渠道',
  disable_channel: '停用渠道',
  create_ai_provider: '创建 AI 提供商',
  set_active_ai_provider: '切换 AI active',
  update_system_config: '修改系统配置',
  assign_roles: '分配角色',
  update_downstream_identity: '关联 ERP 账号',
  force_unbind: '强制解绑',
}
function actionLabel(a) {
  return ACTION_LABEL[a] || a
}

const TARGET_TYPE_LABEL = {
  downstream_system: '下游系统',
  channel_app: '渠道应用',
  ai_provider: 'AI 提供商',
  system_config: '系统配置',
  hub_user: '用户',
  hub_role: '角色',
  downstream_identity: 'ERP 账号关联',
  channel_user_binding: '渠道用户绑定',
}
function targetTypeLabel(t) {
  return TARGET_TYPE_LABEL[t] || t
}

const hasMeta = computed(() => auth.hasPerm('platform.audit.system_read'))

function formatDetail(d) {
  if (d == null) return ''
  try {
    return JSON.stringify(d)
  } catch {
    return String(d)
  }
}

const { page, pageSize, totalPages, visiblePages, reset } = usePagination({
  total: () => totalCount.value,
  pageSize: 20,
})

async function load() {
  error.value = ''
  try {
    const params = { page: page.value, page_size: pageSize.value, since_hours: filter.since_hours }
    if (filter.actor_id) params.actor_id = filter.actor_id
    if (tab.value === 'main') {
      if (filter.action) params.action = filter.action
      if (filter.target_type) params.target_type = filter.target_type
      const data = await listAudit(params)
      items.value = data.items || []
      totalCount.value = data.total || 0
    } else {
      const data = await listMetaAudit(params)
      items.value = data.items || []
      totalCount.value = data.total || 0
    }
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function onSearch() { reset(); load() }
function onPageChange(p) { page.value = p; load() }

watch(tab, () => { reset(); load() })
onMounted(load)
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
}
.hub-tabs button {
  background: transparent;
  border: 0;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.hub-tabs button:hover { color: var(--text); }
.hub-tabs .is-active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 500; }
.hub-toolbar { display: flex; gap: 8px; flex-wrap: wrap; }
.hub-truncate {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hub-detail-json {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}
</style>
