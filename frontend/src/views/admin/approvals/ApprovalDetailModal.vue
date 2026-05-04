<template>
  <AppModal
    :visible="!!detailRow"
    :title="detailTitle"
    size="lg"
    @update:visible="(v) => { if (!v) emit('close') }"
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
      <AppButton variant="secondary" size="sm" @click="emit('close')">关闭</AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import { computed } from 'vue'
// I5: 复用 utils/format.js 的 fmtDateTime（接受 ISO string / Date 直接值）
import { fmtDateTime } from '../../../utils/format'
import AppModal from '../../../components/ui/AppModal.vue'
import AppButton from '../../../components/ui/AppButton.vue'

const props = defineProps({
  detailRow: { type: Object, default: null },
  currentTab: { type: String, required: true },
  currentTabLabel: { type: String, required: true },
})

const emit = defineEmits(['close'])

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

// M3: 数字格式化辅助
function fmtNumber(v, decimals = 2) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (isNaN(n)) return String(v)
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

const detailTitle = computed(() => {
  if (!props.detailRow) return ''
  return `${props.currentTabLabel} 详情 #${props.detailRow.id}`
})

const detailFields = computed(() => {
  if (!props.detailRow) return {}
  const row = props.detailRow
  if (props.currentTab === 'voucher') {
    return {
      'ID': row.id,
      '申请人 ID': row.requester_hub_user_id != null ? `#${row.requester_hub_user_id}` : '-',
      '凭证类型': row.rule_matched ?? '-',
      '状态': voucherStatusLabel(row.status),
      '凭证数据': row.voucher_data,
      'ERP 凭证 ID': row.erp_voucher_id ?? '-',
      '拒绝原因': row.rejection_reason ?? '-',
      '处理开始时间': fmtDateTime(row.creating_started_at),
      '创建时间': fmtDateTime(row.created_at),
    }
  }
  if (props.currentTab === 'price') {
    return {
      'ID': row.id,
      '申请人 ID': row.requester_hub_user_id != null ? `#${row.requester_hub_user_id}` : '-',
      '客户 ID': row.customer_id,
      '商品 ID': row.product_id,
      '当前价格': fmtNumber(row.current_price, 2),
      '申请新价格': fmtNumber(row.new_price, 2),
      '折扣比例': row.discount_pct != null ? (row.discount_pct * 100).toFixed(2) + '%' : '-',
      '申请原因': row.reason ?? '-',
      '状态': voucherStatusLabel(row.status),
      '创建时间': fmtDateTime(row.created_at),
    }
  }
  // stock
  return {
    'ID': row.id,
    '申请人 ID': row.requester_hub_user_id != null ? `#${row.requester_hub_user_id}` : '-',
    '商品 ID': row.product_id,
    '仓库 ID': row.warehouse_id,
    '调整数量': fmtNumber(row.adjustment_qty, 0),
    '申请原因': row.reason ?? '-',
    '状态': voucherStatusLabel(row.status),
    '创建时间': fmtDateTime(row.created_at),
  }
})
</script>

<style scoped>
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
</style>
