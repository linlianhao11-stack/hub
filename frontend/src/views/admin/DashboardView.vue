<!--
  Plan 6 Task 14 加 LLM 成本 section 后已 ~390 行；
  follow-up：抽出 DashboardLlmCostSection.vue 子组件让本 view 回到 ~250 行。
-->
<template>
  <div class="hub-page">
    <h1 class="hub-page__title">仪表盘</h1>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <section class="hub-dashboard__health">
      <div v-for="card in healthCards" :key="card.key" class="hub-dashboard__health-card">
        <div class="hub-dashboard__health-name">{{ card.label }}</div>
        <div class="hub-dashboard__health-value" :class="card.cls">{{ card.text }}</div>
      </div>
    </section>

    <section class="hub-dashboard__stats">
      <div class="hub-dashboard__stat">
        <span class="hub-dashboard__stat-label">24h 任务总数</span>
        <span class="hub-dashboard__stat-value">{{ today.total }}</span>
      </div>
      <div class="hub-dashboard__stat">
        <span class="hub-dashboard__stat-label">成功率</span>
        <span class="hub-dashboard__stat-value">{{ today.success_rate }}%</span>
      </div>
      <div class="hub-dashboard__stat">
        <span class="hub-dashboard__stat-label">失败次数</span>
        <span class="hub-dashboard__stat-value hub-dashboard__stat-value--error">{{ today.failed }}</span>
      </div>
      <div class="hub-dashboard__stat">
        <span class="hub-dashboard__stat-label">活跃用户</span>
        <span class="hub-dashboard__stat-value">{{ today.active_users }}</span>
      </div>
      <div class="hub-dashboard__stat">
        <span class="hub-dashboard__stat-label">平均延迟</span>
        <span class="hub-dashboard__stat-value">{{ today.avg_duration_ms }} ms</span>
      </div>
    </section>

    <section class="hub-dashboard__chart-card">
      <div class="hub-dashboard__chart-head">
        <h2 class="hub-dashboard__chart-title">最近 24 小时任务量</h2>
        <span class="hub-dashboard__chart-sub">按自然小时分桶</span>
      </div>
      <div class="hub-dashboard__chart-wrap">
        <canvas ref="chartCanvas"></canvas>
      </div>
    </section>

    <!-- Plan 6 Task 14：LLM 成本指标 -->
    <section v-if="data.llm_cost" class="hub-dashboard__llm-section">
      <h2 class="hub-dashboard__section-title">LLM 成本</h2>
      <div class="hub-dashboard__llm-grid">
        <div class="hub-dashboard__llm-card">
          <div class="hub-dashboard__llm-label">今日调用次数</div>
          <div class="hub-dashboard__llm-value">{{ formatNumber(data.llm_cost.today_llm_calls) }}</div>
        </div>
        <div class="hub-dashboard__llm-card">
          <div class="hub-dashboard__llm-label">今日 Token 消耗</div>
          <div class="hub-dashboard__llm-value">{{ formatNumber(data.llm_cost.today_total_tokens) }}</div>
        </div>
        <div class="hub-dashboard__llm-card">
          <div class="hub-dashboard__llm-label">今日成本</div>
          <div class="hub-dashboard__llm-value">¥{{ formatCost(data.llm_cost.today_cost_yuan) }}</div>
        </div>
        <div class="hub-dashboard__llm-card">
          <div class="hub-dashboard__llm-label">本月累计</div>
          <div class="hub-dashboard__llm-value">¥{{ formatCost(data.llm_cost.month_to_date_cost_yuan) }}</div>
        </div>
      </div>

      <div class="hub-dashboard__budget" :class="{ 'hub-dashboard__budget--alert': data.llm_cost.budget_alert }">
        <div class="hub-dashboard__budget-header">
          <span>本月预算 ¥{{ formatCost(data.llm_cost.month_budget_yuan) }}</span>
          <span class="hub-dashboard__budget-pct">{{ data.llm_cost.budget_used_pct.toFixed(2) }}%</span>
        </div>
        <div class="hub-dashboard__progress-bar">
          <div
            class="hub-dashboard__progress-fill"
            :style="{ width: Math.min(data.llm_cost.budget_used_pct, 100) + '%' }"
          ></div>
        </div>
        <div v-if="data.llm_cost.budget_alert" class="hub-dashboard__budget-alert-msg">
          已超 80% 预算，请关注
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Filler,
  Legend,
  Tooltip,
} from 'chart.js'

Chart.register(LineController, LineElement, PointElement, LinearScale, CategoryScale, Filler, Legend, Tooltip)

const error = ref('')
const data = ref({ health: {}, today: {}, hourly: [], llm_cost: null })
const today = computed(() => ({
  total: data.value.today.total ?? 0,
  success_rate: data.value.today.success_rate ?? 0,
  failed: data.value.today.failed ?? 0,
  active_users: data.value.today.active_users ?? 0,
  avg_duration_ms: data.value.today.avg_duration_ms ?? 0,
}))

const HEALTH_LABELS = {
  postgres: 'PostgreSQL',
  redis: 'Redis',
  dingtalk_stream: '钉钉 Stream',
  erp_default: 'ERP 连接',
}
const HEALTH_TEXT = {
  ok: '正常', connected: '已连接', configured: '已配置',
  down: '异常', not_started: '未启动', not_configured: '未配置',
}
const HEALTH_OK = ['ok', 'connected', 'configured']

const healthCards = computed(() =>
  Object.entries(data.value.health).map(([key, value]) => ({
    key,
    label: HEALTH_LABELS[key] || key,
    text: HEALTH_TEXT[value] || value,
    cls: HEALTH_OK.includes(value) ? 'is-ok' : 'is-bad',
  })),
)

const chartCanvas = ref(null)
let chart = null

function renderChart() {
  if (!chartCanvas.value) return
  const labels = data.value.hourly.map((h) => `${String(h.hour).padStart(2, '0')}:00`)
  const totals = data.value.hourly.map((h) => h.total)
  const fails = data.value.hourly.map((h) => h.failed)
  if (chart) chart.destroy()
  chart = new Chart(chartCanvas.value, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '总数',
          data: totals,
          borderColor: 'rgba(70, 116, 180, 1)',
          backgroundColor: 'rgba(70, 116, 180, 0.1)',
          tension: 0.35,
          fill: true,
        },
        {
          label: '失败',
          data: fails,
          borderColor: 'rgba(217, 72, 65, 1)',
          backgroundColor: 'transparent',
          tension: 0.35,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  })
}

function formatNumber(n) {
  if (n === null || n === undefined) return '0'
  return Number(n).toLocaleString('zh-CN')
}

function formatCost(yuan) {
  if (yuan === null || yuan === undefined) return '0.00'
  return Number(yuan).toFixed(2)
}

async function load() {
  try {
    data.value = await getDashboard()
  } catch (e) {
    error.value = pickErrorDetail(e, '仪表盘加载失败')
  }
}

watch(data, renderChart, { deep: true })
onMounted(() => {
  load()
})
onBeforeUnmount(() => {
  if (chart) chart.destroy()
})
</script>

<style scoped>
.hub-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
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
.hub-dashboard__health {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.hub-dashboard__health-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hub-dashboard__health-name {
  font-size: 12px;
  color: var(--text-muted);
}
.hub-dashboard__health-value {
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-mono);
  color: var(--text);
}
.hub-dashboard__health-value.is-ok { color: var(--success); }
.hub-dashboard__health-value.is-bad { color: var(--error); }

.hub-dashboard__stats {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}
.hub-dashboard__stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hub-dashboard__stat-label {
  font-size: 12px;
  color: var(--text-muted);
}
.hub-dashboard__stat-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.hub-dashboard__stat-value--error { color: var(--error); }

.hub-dashboard__chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hub-dashboard__chart-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.hub-dashboard__chart-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}
.hub-dashboard__chart-sub {
  font-size: 12px;
  color: var(--text-muted);
}
.hub-dashboard__chart-wrap {
  height: 260px;
  position: relative;
}
@media (max-width: 1100px) {
  .hub-dashboard__health { grid-template-columns: repeat(2, 1fr); }
  .hub-dashboard__stats { grid-template-columns: repeat(2, 1fr); }
}

/* Plan 6 Task 14：LLM 成本指标 */
.hub-dashboard__llm-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hub-dashboard__section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}
.hub-dashboard__llm-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.hub-dashboard__llm-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hub-dashboard__llm-label {
  font-size: 12px;
  color: var(--text-muted);
}
.hub-dashboard__llm-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.hub-dashboard__budget {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.hub-dashboard__budget--alert {
  border-color: var(--warning);
  background: color-mix(in srgb, var(--warning) 8%, var(--surface));
}
.hub-dashboard__budget-header {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  color: var(--text);
}
.hub-dashboard__budget-pct {
  font-family: var(--font-mono);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.hub-dashboard__progress-bar {
  height: 8px;
  background: var(--border);
  border-radius: 4px;
  overflow: hidden;
}
.hub-dashboard__progress-fill {
  height: 100%;
  background: var(--success);
  border-radius: 4px;
  transition: width 0.3s ease-out;
}
.hub-dashboard__budget--alert .hub-dashboard__progress-fill {
  background: var(--warning);
}
.hub-dashboard__budget-alert-msg {
  font-size: 13px;
  color: var(--warning);
  font-weight: 500;
}
@media (max-width: 1100px) {
  .hub-dashboard__llm-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
