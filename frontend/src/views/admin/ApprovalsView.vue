<!--
  Plan 6 Task 12 — 审批 inbox。
  三 tab（凭证 / 调价 / 库存调整）+ 批量勾选 + 详情弹窗 + 批量通过/拒绝。

  TODO（follow-up）：当前文件偏大，建议拆成：
  - approvals/VoucherTab.vue / PriceTab.vue / StockTab.vue（每个 ~200 行，独立 columns + detail 字段）
  - composables/useApprovalsBatch.js（共享 handleBatchApprove / handleBatchReject 流程）
  - approvals/ApprovalsResultModal.vue（结果展示 modal 跨 tab 复用）
  保持 ApprovalsView.vue 仅做"tab 路由 + state 协调"。

  TODO（follow-up, M2）：申请人 ID 显示改为用户名。
  目前加了 #{id} 前缀。待 usersApi.list 接口稳定后，加前端 Map 缓存：
    const userIdToName = ref(new Map())
    async function loadUserNames() { ... }
    function fmtRequester(id) { return userIdToName.value.get(id) || `#${id}` }

  TODO（follow-up, M6）：tab 切换不缓存（每次重新拉数据），保证审批员看到最新状态。
  如果未来流量瓶颈，可加 ETag/If-Modified-Since 模式。

  TODO（follow-up, I4）：手搓 tabs/badge 待替换为 AppTabs / AppBadge。
  需先确认 AppTabs 支持 modelValue + tabs prop，AppBadge 支持 variant prop。
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

    <!-- Tab 栏（TODO follow-up：替换为 AppTabs 组件） -->
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
              <!-- I4: AppCheckbox 替换裸 input checkbox（全选，支持 indeterminate） -->
              <AppCheckbox
                :model-value="allSelected"
                :indeterminate="someSelected && !allSelected"
                :disabled="currentRows.length === 0"
                :aria-label="allSelectedLabel"
                @update:model-value="onSelectAllChange"
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
            <!-- I4: AppCheckbox 替换裸 input checkbox（行选，数组 v-model 模式） -->
            <AppCheckbox
              :model-value="selectedIds"
              :value="row.id"
              :aria-label="`选择记录 ${row.id}`"
              @update:model-value="(v) => selectedIds = v"
            />
          </td>
          <td
            v-for="col in currentColumns"
            :key="col.key"
            class="app-td"
            :class="col.numeric ? 'std-num text-right' : ''"
          >
            <template v-if="col.key === 'status'">
              <!-- TODO follow-up（I4）：替换为 <AppBadge :variant="voucherStatusVariant(row.status)"> -->
              <span :class="['status-badge', `status-${row.status}`]">{{ voucherStatusLabel(row.status) }}</span>
            </template>
            <template v-else>{{ formatCell(row, col) }}</template>
          </td>
          <td class="app-td text-right">
            <AppButton variant="ghost" size="xs" @click="showDetail(row)">详情</AppButton>
          </td>
        </tr>

        <!-- I2: AppPagination + usePagination -->
        <template #footer>
          <span class="app-footer-stats">共 {{ currentPagination.total.value }} 条</span>
          <AppPagination
            :page="currentPagination.page.value"
            :total-pages="currentPagination.totalPages.value"
            :visible-pages="currentPagination.visiblePages.value"
            @update:page="(p) => { currentPagination.page.value = p; loadCurrentTab() }"
          />
        </template>
      </AppTable>
    </AppCard>

    <!-- 详情弹窗 -->
    <ApprovalDetailModal
      :detail-row="detailRow"
      :current-tab="currentTab"
      :current-tab-label="currentTabLabel"
      @close="detailRow = null"
    />

    <!-- M1: 批量通过二次确认 modal（替换 confirm()） -->
    <AppModal
      :visible="showApproveConfirm"
      title="确认批量通过"
      size="sm"
      @update:visible="(v) => { if (!v) showApproveConfirm = false }"
    >
      <p class="confirm-text">
        确认通过这 <strong>{{ selectedIds.length }}</strong> 条
        <strong>{{ currentTabLabel }}</strong> 草稿？
      </p>
      <template #footer>
        <AppButton variant="ghost" size="sm" @click="showApproveConfirm = false">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="approving" @click="executeBatchApprove">确认通过</AppButton>
      </template>
    </AppModal>

    <!-- I3: 批量审批结果 modal（替换 alert，展示 reason） -->
    <ApprovalsResultModal
      :result-data="resultData"
      :result-title="resultTitle"
      @close="resultData = null"
    />

    <!-- 批量拒绝 reason 输入弹窗 -->
    <ApprovalRejectModal
      :show-reject-modal="showRejectModal"
      :selected-ids="selectedIds"
      :current-tab-label="currentTabLabel"
      :rejecting="rejecting"
      @close="showRejectModal = false"
      @submit="onRejectSubmit"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { approvalsApi } from '../../api/approvals'
import { pickErrorDetail } from '../../api'
// I5: 复用 utils/format.js 的 fmtDateTime（接受 ISO string / Date 直接值）
import { fmtDateTime } from '../../utils/format'
import { usePagination } from '../../composables/usePagination'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppCheckbox from '../../components/ui/AppCheckbox.vue'
import AppPagination from '../../components/ui/AppPagination.vue'
import ApprovalDetailModal from './approvals/ApprovalDetailModal.vue'
import ApprovalsResultModal from './approvals/ApprovalsResultModal.vue'
import ApprovalRejectModal from './approvals/ApprovalRejectModal.vue'

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
const priceRows = ref([])
const stockRows = ref([])

const selectedIds = ref([])
const detailRow = ref(null)

const showRejectModal = ref(false)
const rejecting = ref(false)
const approving = ref(false)

// M1: 批量通过二次确认 + I3: 结果 modal
const showApproveConfirm = ref(false)
const resultData = ref(null)

// ────────────────────────────────────────
// I2: 三 tab 独立 pagination state（opts.total 回调在下方 _xxxTotal ref 定义后生效）
// ────────────────────────────────────────

// 各 tab 的 total ref（供 usePagination opts.total 回调）
const _voucherTotal = ref(0)
const _priceTotal = ref(0)
const _stockTotal = ref(0)

const voucherPagination = usePagination({ total: () => _voucherTotal.value, pageSize: 20 })
const pricePagination = usePagination({ total: () => _priceTotal.value, pageSize: 20 })
const stockPagination = usePagination({ total: () => _stockTotal.value, pageSize: 20 })

const currentPagination = computed(() => {
  if (currentTab.value === 'voucher') return voucherPagination
  if (currentTab.value === 'price') return pricePagination
  return stockPagination
})

// ────────────────────────────────────────
// M4: 状态选项（加 creating）
// ────────────────────────────────────────

const statusOptions = [
  { value: 'pending', label: '待审批' },
  { value: 'creating', label: '处理中' },
  { value: 'created', label: '已创建（凭证已落 ERP）' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
]

// I5: voucher_draft 五值状态机本地 LABEL（与 utils/format.js 的 pending:'排队中' 冲突，故保留本地）
const VOUCHER_STATUS_LABEL = {
  pending: '待审批',
  creating: '处理中',
  created: '已创建',
  approved: '已通过',
  rejected: '已拒绝',
}

function voucherStatusLabel(s) {
  return VOUCHER_STATUS_LABEL[s] || s
}

// ────────────────────────────────────────
// M3: 数字格式化辅助
// ────────────────────────────────────────

function fmtNumber(v, decimals = 2) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (isNaN(n)) return String(v)
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

// ────────────────────────────────────────
// 列定义
// ────────────────────────────────────────

const voucherColumns = [
  { key: 'id', label: 'ID', numeric: true },
  // M2: 申请人 ID 加 # 前缀，follow-up 替换为用户名
  { key: 'requester_hub_user_id', label: '申请人', numeric: false,
    accessor: (r) => r.requester_hub_user_id != null ? `#${r.requester_hub_user_id}` : '-' },
  { key: 'rule_matched', label: '凭证类型' },
  { key: 'amount', label: '总金额', numeric: true,
    accessor: (r) => fmtNumber(r.voucher_data?.total_amount) },
  { key: 'status', label: '状态' },
  { key: 'created_at', label: '创建时间',
    accessor: (r) => fmtDateTime(r.created_at) },
]

const priceColumns = [
  { key: 'id', label: 'ID', numeric: true },
  // M2: 申请人 ID 加 # 前缀
  { key: 'requester_hub_user_id', label: '申请人', numeric: false,
    accessor: (r) => r.requester_hub_user_id != null ? `#${r.requester_hub_user_id}` : '-' },
  { key: 'customer_id', label: '客户 ID', numeric: true },
  { key: 'product_id', label: '商品 ID', numeric: true },
  // M3: 价格字段格式化
  { key: 'current_price', label: '当前价', numeric: true,
    accessor: (r) => fmtNumber(r.current_price, 2) },
  { key: 'new_price', label: '新价格', numeric: true,
    accessor: (r) => fmtNumber(r.new_price, 2) },
  { key: 'discount_pct', label: '折扣',
    accessor: (r) => r.discount_pct != null ? (r.discount_pct * 100).toFixed(2) + '%' : '-' },
  { key: 'status', label: '状态' },
  { key: 'created_at', label: '创建时间',
    accessor: (r) => fmtDateTime(r.created_at) },
]

const stockColumns = [
  { key: 'id', label: 'ID', numeric: true },
  // M2: 申请人 ID 加 # 前缀
  { key: 'requester_hub_user_id', label: '申请人', numeric: false,
    accessor: (r) => r.requester_hub_user_id != null ? `#${r.requester_hub_user_id}` : '-' },
  { key: 'product_id', label: '商品 ID', numeric: true },
  { key: 'warehouse_id', label: '仓库 ID', numeric: true },
  // M3: 数量字段格式化（整数）
  { key: 'adjustment_qty', label: '调整数量', numeric: true,
    accessor: (r) => fmtNumber(r.adjustment_qty, 0) },
  { key: 'reason', label: '原因' },
  { key: 'status', label: '状态' },
  { key: 'created_at', label: '创建时间',
    accessor: (r) => fmtDateTime(r.created_at) },
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

const currentTabLabel = computed(() => {
  return tabs.find((t) => t.key === currentTab.value)?.label || ''
})

const allSelected = computed(() =>
  currentRows.value.length > 0 &&
  selectedIds.value.length === currentRows.value.length
)

const someSelected = computed(() =>
  selectedIds.value.length > 0 &&
  selectedIds.value.length < currentRows.value.length
)

// M9: 全选 aria-label 动态化
const allSelectedLabel = computed(() => {
  if (allSelected.value) return '已全选，点击取消'
  if (someSelected.value) return `已选 ${selectedIds.value.length} 条，点击全选`
  return `全选所有 ${currentRows.value.length} 条`
})

// I3: 结果 modal 标题
const resultTitle = computed(() => {
  const tabLabel = currentTabLabel.value
  return `${tabLabel} 批量操作结果`
})

// ────────────────────────────────────────
// 辅助函数
// ────────────────────────────────────────

function formatCell(row, col) {
  if (col.accessor) {
    const result = col.accessor(row)
    return result ?? '-'
  }
  const val = row[col.key]
  return val ?? '-'
}

// ────────────────────────────────────────
// I2: 数据加载（带分页 + M5: AbortController 防 race condition）
// ────────────────────────────────────────

let activeRequestController = null

async function loadCurrentTab() {
  // M5: 取消上一个未完成的请求
  if (activeRequestController) {
    activeRequestController.abort()
  }
  const controller = new AbortController()
  activeRequestController = controller

  loading.value = true
  pageError.value = ''
  selectedIds.value = []

  try {
    const pag = currentPagination.value
    const params = {
      status: statusFilter.value,
      limit: pag.pageSize.value,
      offset: (pag.page.value - 1) * pag.pageSize.value,
    }

    if (currentTab.value === 'voucher') {
      const { data } = await approvalsApi.listVoucher(params, { signal: controller.signal })
      voucherRows.value = data.items || []
      _voucherTotal.value = data.total || 0
    } else if (currentTab.value === 'price') {
      const { data } = await approvalsApi.listPrice(params, { signal: controller.signal })
      priceRows.value = data.items || []
      _priceTotal.value = data.total || 0
    } else {
      const { data } = await approvalsApi.listStock(params, { signal: controller.signal })
      stockRows.value = data.items || []
      _stockTotal.value = data.total || 0
    }
  } catch (e) {
    // M5: AbortError / CanceledError 静默忽略
    if (e?.name === 'CanceledError' || e?.name === 'AbortError' || e?.code === 'ERR_CANCELED') {
      return
    }
    pageError.value = pickErrorDetail(e, '加载失败，请刷新重试')
  } finally {
    if (activeRequestController === controller) {
      loading.value = false
      activeRequestController = null
    }
  }
}

function onStatusChange() {
  // I2: 状态切换 reset 到 page=1
  currentPagination.value.reset()
  loadCurrentTab()
}

async function switchTab(key) {
  currentTab.value = key
  selectedIds.value = []
  // I2: tab 切换不 reset pagination（维持各 tab 独立 page state）
  await loadCurrentTab()
}

// ────────────────────────────────────────
// 勾选
// ────────────────────────────────────────

function onSelectAllChange(checked) {
  if (checked) {
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
// M1 + I3: 批量通过（两步：先确认 modal，再执行）
// ────────────────────────────────────────

function handleBatchApprove() {
  if (selectedIds.value.length === 0) return
  showApproveConfirm.value = true
}

async function executeBatchApprove() {
  showApproveConfirm.value = false
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
    // I3: 结果赋值给 resultData，展示 modal
    resultData.value = data
    selectedIds.value = []
    await loadCurrentTab()
  } catch (e) {
    pageError.value = '批量通过失败：' + pickErrorDetail(e, '请求失败')
  } finally {
    approving.value = false
  }
}

// ────────────────────────────────────────
// 批量拒绝
// ────────────────────────────────────────

function openRejectModal() {
  showRejectModal.value = true
}

async function onRejectSubmit(rejectReason) {
  rejecting.value = true
  try {
    let data
    if (currentTab.value === 'voucher') {
      const resp = await approvalsApi.batchRejectVoucher(selectedIds.value, rejectReason)
      data = resp.data
    } else if (currentTab.value === 'price') {
      const resp = await approvalsApi.batchRejectPrice(selectedIds.value, rejectReason)
      data = resp.data
    } else {
      const resp = await approvalsApi.batchRejectStock(selectedIds.value, rejectReason)
      data = resp.data
    }
    // I3: 拒绝结果也用 resultData modal 展示
    resultData.value = data
    showRejectModal.value = false
    selectedIds.value = []
    await loadCurrentTab()
  } catch (e) {
    pageError.value = '批量拒绝失败：' + pickErrorDetail(e, '请求失败')
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
  margin-top: 0; /* M10: 贴近 tab 栏 */
}

.batch-info {
  font-size: 13px;
  color: var(--text-secondary);
  margin-right: 4px;
}

.batch-info strong {
  color: var(--primary);
}

/* 状态标签（TODO follow-up：替换为 AppBadge） */
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

/* 确认弹窗 */
.confirm-text {
  font-size: 14px;
  color: var(--text);
  margin: 0 0 4px;
  line-height: 1.6;
}
</style>
