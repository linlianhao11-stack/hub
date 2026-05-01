<template>
  <div class="hub-page">
    <div class="hub-page__head">
      <h1 class="hub-page__title">健康巡检</h1>
      <span class="hub-health__refresh">每 10 秒自动刷新 · {{ lastFetched }}</span>
    </div>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <section class="hub-health__grid">
      <div v-for="card in cards" :key="card.key" class="hub-health__card">
        <div class="hub-health__card-head">
          <span class="hub-health__card-name">{{ card.label }}</span>
          <AppBadge
            v-if="card.kind !== 'info'"
            :variant="card.ok ? 'success' : 'error'"
          >{{ card.ok ? '正常' : '异常' }}</AppBadge>
        </div>
        <div class="hub-health__card-value">{{ card.value }}</div>
      </div>
      <div v-if="!cards.length" class="hub-page__hint">加载中…</div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getHealth } from '../../api/dashboard'
import { pickErrorDetail } from '../../api'
import { fmtDateTime } from '../../utils/format'
import AppBadge from '../../components/ui/AppBadge.vue'

const data = ref(null)
const error = ref('')
const lastFetched = ref('—')
let timer = null

// 后端 health endpoint 返回结构（hub/routers/health.py）：
//   { status: "healthy"|"degraded"|"unhealthy",
//     components: { postgres, redis, dingtalk_stream, erp_default },
//     uptime_seconds: <int>, version: "0.1.0" }
const COMPONENT_LABELS = {
  postgres: 'PostgreSQL',
  redis: 'Redis',
  worker: 'Worker',
  dingtalk_stream: '钉钉 Stream',
  erp_default: 'ERP 默认',
  ai_provider: 'AI 提供商',
}

const OVERALL_LABELS = {
  healthy: '正常',
  degraded: '部分降级',
  unhealthy: '不可用',
}

const COMPONENT_LABELS_VALUE = {
  ok: '运行中',
  connected: '已连接',
  configured: '已配置',
  running: '运行中',
  not_configured: '未配置',
  not_started: '未启动',
  waiting_config: '等待配置',
  unknown: '未知',
  down: '不可用',
}

// 哪些 status 字符串视为"正常"
const OK_VALUES = ['ok', 'connected', 'configured', 'running', 'healthy']

function fmtUptime(sec) {
  const s = parseInt(sec, 10) || 0
  if (s < 60) return `${s} 秒`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    const r = s % 60
    return r ? `${m} 分 ${r} 秒` : `${m} 分钟`
  }
  if (s < 86400) {
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return m ? `${h} 小时 ${m} 分` : `${h} 小时`
  }
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  return h ? `${d} 天 ${h} 小时` : `${d} 天`
}

const cards = computed(() => {
  if (!data.value) return []
  const list = []
  const d = data.value

  // 1. 总体状态（带状态徽章；带中文 label）
  if (d.status !== undefined) {
    list.push({
      key: 'status',
      label: '总体状态',
      value: OVERALL_LABELS[d.status] || d.status,
      kind: 'status',
      ok: OK_VALUES.includes(String(d.status).toLowerCase()),
    })
  }

  // 2. components 展开（每个组件单独一张卡，带状态徽章）
  if (d.components && typeof d.components === 'object') {
    for (const [k, v] of Object.entries(d.components)) {
      const valueStr = String(v)
      list.push({
        key: `comp_${k}`,
        label: COMPONENT_LABELS[k] || k,
        value: COMPONENT_LABELS_VALUE[valueStr.toLowerCase()] || valueStr,
        kind: 'component',
        ok: OK_VALUES.includes(valueStr.toLowerCase()),
      })
    }
  }

  // 3. 元信息字段（不是状态，**不带徽章**）
  if (d.uptime_seconds !== undefined) {
    list.push({
      key: 'uptime',
      label: '运行时长',
      value: fmtUptime(d.uptime_seconds),
      kind: 'info',
    })
  }
  if (d.version !== undefined) {
    list.push({
      key: 'version',
      label: '版本',
      value: String(d.version),
      kind: 'info',
    })
  }

  return list
})

async function load() {
  try {
    data.value = await getHealth()
    lastFetched.value = fmtDateTime(new Date())
    error.value = ''
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 10000)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
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
.hub-health__refresh { font-size: 12px; color: var(--text-muted); }
.hub-health__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}
.hub-health__card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hub-health__card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.hub-health__card-name { font-size: 13px; font-weight: 500; color: var(--text); }
.hub-health__card-value {
  font-size: 13px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}
</style>
