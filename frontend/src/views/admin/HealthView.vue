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
          <AppBadge :variant="card.ok ? 'success' : 'error'">{{ card.ok ? '正常' : '异常' }}</AppBadge>
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

const LABEL = {
  status: '总体状态',
  postgres: 'PostgreSQL',
  redis: 'Redis',
  worker: 'Worker',
  dingtalk_stream: '钉钉 Stream',
  erp_default: 'ERP 默认',
  ai_provider: 'AI 提供商',
}
const OK_VALUES = ['ok', 'connected', 'configured', 'running', 'healthy']

const cards = computed(() => {
  if (!data.value) return []
  return Object.entries(data.value).map(([key, value]) => ({
    key,
    label: LABEL[key] || key,
    value: typeof value === 'object' ? JSON.stringify(value) : String(value),
    ok: OK_VALUES.includes(String(value).toLowerCase()),
  }))
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
