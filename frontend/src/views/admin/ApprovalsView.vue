<!--
  Plan 6 Task 12 — 审批 inbox。
  三 tab（凭证 / 调价 / 库存调整）+ 批量勾选 + 详情弹窗 + 批量通过/拒绝。
-->
<template>
  <div class="hub-page">
    <!-- 页头 -->
    <div class="hub-page__header">
      <h1 class="hub-page__title">待审批</h1>
      <div class="toolbar-actions">
        <AppSelect
          v-model="statusFilter"
          size="toolbar"
          :options="statusOptions"
          @update:modelValue="onStatusChange"
        />
        <AppButton variant="secondary" size="sm" @click="loadCurrentTab">
          <RefreshCw :size="14" class="btn-icon" /> 刷新
        </AppButton>
      </div>
    </div>

    <div v-if="pageError" class="hub-page__error">{{ pageError }}</div>

    <!-- Tab 栏 -->
    <div class="tabs-bar" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        role="tab"
        :aria-selected="currentTab === tab.key"
        :class="['tab-btn', { active: currentTab === tab.key }]"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 批量操作栏（有选中才出现） -->
    <div v-if="selectedIds.length > 0" class="batch-toolbar">
      <span class="batch-info">已选 <strong>{{ selectedIds.length }}</strong> 条</span>
      <AppButton variant="primary" size="sm" :loading="approving" @click="handleBatchApprove">
        批量通过
      </AppButton>
      <AppButton variant="danger" size="sm" @click="openRejectModal">
        批量拒绝
      </AppButton>
      <AppButton variant="ghost" size="sm" @click="selectedIds = []">
        取消选择
      </AppButton>
    </div>

    <!-- 数据表 -->
    <AppCard padding="none">
      <AppTable
        :card="false"
        sticky
        :empty="!loading && currentRows.length === 0"
        empty-text="暂无审批记录"
      >
        <template #header>
          <tr>
            <th class="app-th" style="width: 40px; padding-left: 14px;">
              <input
                type="checkbox"
                :checked="allSelected"
                :indeterminate="someSelected"
                :disabled="currentRows.length === 0"
                @change="toggleSelectAll"
                aria-label="全选"
              />
            </th>
            <template v-for="col in currentColumns" :key="col.key">
              <th class="app-th" :class="col.numeric ? 'text-right' : ''">{{ col.label }}</th>
            </template>
            <th class="app-th text-right" style="width: 70px;">操作</th>
          </tr>
        </template>

        <!-- 加载骨架 -->
        <tr v-if="loading">
          <td :colspan="currentColumns.length + 2" class="app-td text-center" style="color: var(--text-muted); padding: 24px 0;">
            加载中…
          </td>
        </tr>

        <!-- 数据行 -->
        <tr v-for="row in currentRows" :key="row.id">
          <td class="app-td" style="padding-left: 14px;">
            <input
              type="checkbox"
              :value="row.id"
              v-model="selectedIds"
              :aria-label="`选择记录 ${row.id}`"
            />
          </td>
          <td
            v-for="col in currentColumns"
            :key="col.key"
            class="app-td"
            :class="col.numeric ? 'std-num text-right' : ''"
          >
            <template v-if="col.key === 'status'">
              <span :class="['status-badge', `status-${row.status}`]">{{ statusLabel(row.status) }}</span>
            </template>
            <template v-else>{{ formatCell(row, col) }}</template>
          </td>
          <td class="app-td text-right">
            <AppButton variant="ghost" size="xs" @click="showDetail(row)">详情</AppButton>
          </td>
        </tr>

        <template #footer>
          <span class="app-footer-stats">共 {{ currentTotal }} 条</span>
        </template>
      </AppTable>
    </AppCard>

    <!-- 详情弹窗 -->
    <AppModal
      :visible="!!detailRow"
      :title="detailTitle"
      size="lg"
      @update:visible="(v) => { if (!v) detailRow = null }"
    >
      <div v-if="detailRow" class="detail-content">
        <table class="detail-table">
          <tbody>
            <tr v-for="(val, key) in detailFields" :key="key">
              <td class="detail-key">{{ key }}</td>
              <td class="detail-val">
                <template v-if="typeof val === 'object' && val !== null">
                  <pre class="detail-json">{{ JSON.stringify(val, null, 2) }}</pre>
                </template>
                <template v-else>{{ val ?? '-' }}</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="detailRow = null">关闭</AppButton>
      </template>
    </AppModal>

    <!-- 批量拒绝 reason 输入弹窗 -->
    <AppModal
      :visible="showRejectModal"
      title="批量拒绝"
      size="sm"
      @update:visible="(v) => { if (!v) closeRejectModal() }"
    >
      <form id="reject-form" @submit.prevent="handleBatchReject" class="modal-form">
        <div class="form-field">
          <label class="form-label" for="reject-reason">
            拒绝原因 <span class="required">*</span>
          </label>
          <AppTextarea
            id="reject-reason"
            v-model="rejectReason"
            placeholder="请填写拒绝原因（最多 500 字）"
            :rows="3"
            maxlength="500"
            required
          />
        </div>
        <div v-if="rejectError" class="form-error">{{ rejectError }}</div>
      </form>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="closeRejectModal">取消</AppButton>
        <AppButton
          variant="danger"
          size="sm"
          :loading="rejecting"
          :disabled="!rejectReason.trim()"
          type="submit"
          form="reject-form"
        >
          确认拒绝
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { approvalsApi } from '../../api/approvals'
import { pickErrorDetail } from '../../api'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppTextarea from '../../components/ui/AppTextarea.vue'

// ────────────────────────────────────────
// Tab 定义
// ────────────────────────────────────────

const tabs = [
  { key: 'voucher', label: '凭证' },
  { key: 'price', label: '调价' },
  { key: 'stock', label: '库存调整' },
]

// ────────────────────────────────────────
// 状态
// ────────────────────────────────────────

const currentTab = ref('voucher')
const statusFilter = ref('pending')
const loading = ref(false)
const pageError = ref('')

const voucherRows = ref([])
const voucherTotal = ref(0)
const priceRows = ref([])
const priceTotal = ref(0)
const stockRows = ref([])
const stockTotal = ref(0)

const selectedIds = ref([])
const detailRow = ref(null)

const showRejectModal = ref(false)
const rejectReason = ref('')
const rejectError = ref('')
const rejecting = ref(false)
const approving = ref(false)

// ────────────────────────────────────────
// 选项
// ────────────────────────────────────────

const statusOptions = [
  { value: 'pending', label: '待审批' },
  { value: 'created', label: '已创建（凭证已落 ERP）' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
]

const STATUS_LABEL = {
  pending: '待审批',
  creating: '处理中',
  created: '已创建',
  approved: '已通过',
  rejected: '已拒绝',
}

function statusLabel(s) {
  return STATUS_LABEL[s] || s
}

// ────────────────────────────────────────
// 列定义
// ────────────────────────────────────────

const voucherColumns = [
  { key: 'id', label: 'ID', numeric: true },
  { key: 'requester_hub_user_id', label: '申请人 ID', numeric: true },
  { key: 'rule_matched', label: '凭证类型' },
  { key: 'amount', label: '总金额', numeric: true,
    accessor: (r) => r.voucher_data?.total_amount ?? '-' },
  { key: 'status', label: '状态' },
  { key: 'created_at', label: '创建时间', accessor: fmtDateTime },
]

const priceColumns = [
  { key: 'id', label: 'ID', numeric: true },
  { key: 'requester_hub_user_id', label: '申请人 ID', numeric: true },
  { key: 'customer_id', label: '客户 ID', numeric: true },
  { key: 'product_id', label: '商品 ID', numeric: true },
  { key: 'current_price', label: '当前价', numeric: true },
  { key: 'new_price', label: '新价格', numeric: true },
  { key: 'discount_pct', label: '折扣',
    accessor: (r) => r.discount_pct != null ? (r.discount_pct * 100).toFixed(2) + '%' : '-' },
  { key: 'status', label: '状态' },
  { key: 'created_at', label: '创建时间', accessor: fmtDateTime },
]

const stockColumns = [
  { key: 'id', label: 'ID', numeric: true },
  { key: 'requester_hub_user_id', label: '申请人 ID', numeric: true },
  { key: 'product_id', label: '商品 ID', numeric: true },
  { key: 'warehouse_id', label: '仓库 ID', numeric: true },
  { key: 'adjustment_qty', label: '调整数量', numeric: true },
  { key: 'reason', label: '原因' },
  { key: 'status', label: '状态' },
  { key: 'created_at', label: '创建时间', accessor: fmtDateTime },
]

// ────────────────────────────────────────
// Computed
// ────────────────────────────────────────

const currentColumns = computed(() => {
  if (currentTab.value === 'voucher') return voucherColumns
  if (currentTab.value === 'price') return priceColumns
  return stockColumns
})

const currentRows = computed(() => {
  if (currentTab.value === 'voucher') return voucherRows.value
  if (currentTab.value === 'price') return priceRows.value
  return stockRows.value
})

const currentTotal = computed(() => {
  if (currentTab.value === 'voucher') return voucherTotal.value
  if (currentTab.value === 'price') return priceTotal.value
  return stockTotal.value
})

const allSelected = computed(() =>
  currentRows.value.length > 0 &&
  selectedIds.value.length === currentRows.value.length
)

const someSelected = computed(() =>
  selectedIds.value.length > 0 &&
  selectedIds.value.length < currentRows.value.length
)

const detailTitle = computed(() => {
  if (!detailRow.value) return ''
  const tabLabel = tabs.find((t) => t.key === currentTab.value)?.label || ''
  return `${tabLabel} 详情 #${detailRow.value.id}`
})

const detailFields = computed(() => {
  if (!detailRow.value) return {}
  const row = detailRow.value
  if (currentTab.value === 'voucher') {
    return {
      'ID': row.id,
      '申请人 ID': row.requester_hub_user_id,
      '凭证类型': row.rule_matched ?? '-',
      '状态': statusLabel(row.status),
      '凭证数据': row.voucher_data,
      'ERP 凭证 ID': row.erp_voucher_id ?? '-',
      '拒绝原因': row.rejection_reason ?? '-',
      '处理开始时间': row.creating_started_at ? fmtDateTimeStr(row.creating_started_at) : '-',
      '创建时间': fmtDateTimeStr(row.created_at),
    }
  }
  if (currentTab.value === 'price') {
    return {
      'ID': row.id,
      '申请人 ID': row.requester_hub_user_id,
      '客户 ID': row.customer_id,
      '商品 ID': row.product_id,
      '当前价格': row.current_price ?? '-',
      '申请新价格': row.new_price ?? '-',
      '折扣比例': row.discount_pct != null ? (row.discount_pct * 100).toFixed(2) + '%' : '-',
      '申请原因': row.reason ?? '-',
      '状态': statusLabel(row.status),
      '创建时间': fmtDateTimeStr(row.created_at),
    }
  }
  // stock
  return {
    'ID': row.id,
    '申请人 ID': row.requester_hub_user_id,
    '商品 ID': row.product_id,
    '仓库 ID': row.warehouse_id,
    '调整数量': row.adjustment_qty ?? '-',
    '申请原因': row.reason ?? '-',
    '状态': statusLabel(row.status),
    '创建时间': fmtDateTimeStr(row.created_at),
  }
})

// ────────────────────────────────────────
// 辅助函数
// ────────────────────────────────────────

function fmtDateTime(row) {
  return fmtDateTimeStr(row.created_at)
}

function fmtDateTimeStr(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function formatCell(row, col) {
  if (col.accessor) {
    const result = col.accessor(row)
    return result ?? '-'
  }
  const val = row[col.key]
  return val ?? '-'
}

// ────────────────────────────────────────
// 数据加载
// ────────────────────────────────────────

async function loadCurrentTab() {
  loading.value = true
  pageError.value = ''
  selectedIds.value = []
  try {
    const params = { status: statusFilter.value }
    if (currentTab.value === 'voucher') {
      const { data } = await approvalsApi.listVoucher(params)
      voucherRows.value = data.items || []
      voucherTotal.value = data.total || 0
    } else if (currentTab.value === 'price') {
      const { data } = await approvalsApi.listPrice(params)
      priceRows.value = data.items || []
      priceTotal.value = data.total || 0
    } else {
      const { data } = await approvalsApi.listStock(params)
      stockRows.value = data.items || []
      stockTotal.value = data.total || 0
    }
  } catch (e) {
    pageError.value = pickErrorDetail(e, '加载失败，请刷新重试')
  } finally {
    loading.value = false
  }
}

function onStatusChange() {
  loadCurrentTab()
}

async function switchTab(key) {
  currentTab.value = key
  await loadCurrentTab()
}

// ────────────────────────────────────────
// 勾选
// ────────────────────────────────────────

function toggleSelectAll(e) {
  if (e.target.checked) {
    selectedIds.value = currentRows.value.map((r) => r.id)
  } else {
    selectedIds.value = []
  }
}

// ────────────────────────────────────────
// 详情
// ────────────────────────────────────────

function showDetail(row) {
  detailRow.value = row
}

// ────────────────────────────────────────
// 批量通过
// ────────────────────────────────────────

async function handleBatchApprove() {
  if (selectedIds.value.length === 0) return
  if (!confirm(`确认通过这 ${selectedIds.value.length} 条记录？`)) return
  approving.value = true
  try {
    let data
    if (currentTab.value === 'voucher') {
      const resp = await approvalsApi.batchApproveVoucher(selectedIds.value)
      data = resp.data
    } else if (currentTab.value === 'price') {
      const resp = await approvalsApi.batchApprovePrice(selectedIds.value)
      data = resp.data
    } else {
      const resp = await approvalsApi.batchApproveStock(selectedIds.value)
      data = resp.data
    }

    let msg = `已通过 ${data.approved_count} 条`
    const inProgressCount = data.in_progress?.length || 0
    if (inProgressCount > 0) {
      msg += `；其中 ${inProgressCount} 条正在被另一会话处理，请稍后刷新`
    }
    const failedCount =
      (data.creation_failed?.length || 0) +
      (data.approve_failed?.length || 0) +
      (data.failed?.length || 0)
    if (failedCount > 0) {
      msg += `；${failedCount} 条处理失败（请查看详情或联系管理员）`
    }
    alert(msg)
    selectedIds.value = []
    await loadCurrentTab()
  } catch (e) {
    alert('批量通过失败：' + pickErrorDetail(e, '请求失败'))
  } finally {
    approving.value = false
  }
}

// ────────────────────────────────────────
// 批量拒绝
// ────────────────────────────────────────

function openRejectModal() {
  rejectReason.value = ''
  rejectError.value = ''
  showRejectModal.value = true
}

function closeRejectModal() {
  showRejectModal.value = false
  rejectReason.value = ''
  rejectError.value = ''
}

async function handleBatchReject() {
  if (!rejectReason.value.trim()) {
    rejectError.value = '请填写拒绝原因'
    return
  }
  rejecting.value = true
  rejectError.value = ''
  try {
    let data
    if (currentTab.value === 'voucher') {
      const resp = await approvalsApi.batchRejectVoucher(selectedIds.value, rejectReason.value)
      data = resp.data
    } else if (currentTab.value === 'price') {
      const resp = await approvalsApi.batchRejectPrice(selectedIds.value, rejectReason.value)
      data = resp.data
    } else {
      const resp = await approvalsApi.batchRejectStock(selectedIds.value, rejectReason.value)
      data = resp.data
    }
    alert(`已拒绝 ${data.rejected_count} 条`)
    closeRejectModal()
    selectedIds.value = []
    await loadCurrentTab()
  } catch (e) {
    rejectError.value = pickErrorDetail(e, '批量拒绝失败，请重试')
  } finally {
    rejecting.value = false
  }
}

onMounted(loadCurrentTab)
</script>

<style scoped>
.hub-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
}

.hub-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.hub-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-icon {
  margin-right: 4px;
}

/* Tab 栏 */
.tabs-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
}

.tab-btn {
  padding: 8px 18px;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-muted);
  transition: color 120ms ease, border-color 120ms ease;
  white-space: nowrap;
}

.tab-btn:hover {
  color: var(--text);
}

.tab-btn.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 500;
}

/* 批量操作栏 */
.batch-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  background: var(--elevated);
  border: 1px solid var(--border);
  border-radius: 6px;
}

.batch-info {
  font-size: 13px;
  color: var(--text-secondary);
  margin-right: 4px;
}

.batch-info strong {
  color: var(--primary);
}

/* 状态标签 */
.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.status-pending {
  background: var(--warning-subtle);
  color: var(--warning-emphasis);
}

.status-creating {
  background: color-mix(in srgb, var(--primary) 10%, transparent);
  color: var(--primary);
}

.status-created {
  background: color-mix(in srgb, var(--info) 10%, transparent);
  color: var(--info);
}

.status-approved {
  background: var(--success-subtle);
  color: var(--success-emphasis);
}

.status-rejected {
  background: color-mix(in srgb, var(--error) 10%, transparent);
  color: var(--error);
}

/* 详情弹窗 */
.detail-content {
  max-height: 60vh;
  overflow-y: auto;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
}

.detail-table tr {
  border-bottom: 1px solid var(--border);
}

.detail-table tr:last-child {
  border-bottom: none;
}

.detail-key {
  width: 130px;
  padding: 8px 12px 8px 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  vertical-align: top;
  white-space: nowrap;
}

.detail-val {
  padding: 8px 0;
  font-size: 13px;
  color: var(--text);
  word-break: break-all;
}

.detail-json {
  white-space: pre-wrap;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  background: var(--elevated);
  padding: 8px 10px;
  border-radius: 4px;
  margin: 0;
  max-height: 200px;
  overflow: auto;
}

/* 拒绝弹窗表单 */
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.required {
  color: var(--error);
  margin-left: 2px;
}

.form-error {
  background: color-mix(in srgb, var(--error) 10%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
}
</style>
