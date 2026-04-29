<template>
  <div class="hub-page">
    <h1 class="hub-page__title">系统配置</h1>
    <p class="hub-page__hint">已知 key 白名单，类型严格校验。改动直接生效。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-config">
      <article v-for="cfg in configs" :key="cfg.key" class="hub-config__item">
        <div class="hub-config__head">
          <div>
            <div class="hub-config__name">{{ cfg.label }}</div>
            <div class="hub-config__sub">{{ cfg.desc }}</div>
          </div>
          <code class="hub-config__key">{{ cfg.key }}</code>
        </div>
        <div class="hub-config__body">
          <label v-if="cfg.type === 'list'" class="hub-config__field">
            <span>当前值（每行一个）</span>
            <AppTextarea v-model="cfg.input" :rows="4" />
          </label>
          <label v-else class="hub-config__field">
            <span>当前值（{{ cfg.type === 'int' ? '整数' : '小数' }}）</span>
            <AppInput v-model="cfg.input" :type="cfg.type === 'int' || cfg.type === 'float' ? 'number' : 'text'" />
          </label>
          <div class="hub-config__actions">
            <AppButton variant="primary" size="sm" :loading="cfg.saving" @click="saveOne(cfg)">保存</AppButton>
          </div>
          <div v-if="cfg.error" class="hub-config__error">{{ cfg.error }}</div>
        </div>
      </article>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { getConfig, setConfig } from '../../api/config'
import { pickErrorDetail } from '../../api'
import { useAppStore } from '../../stores/app'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppTextarea from '../../components/ui/AppTextarea.vue'

const appStore = useAppStore()
const error = ref('')

// 后端白名单
const KNOWN = [
  { key: 'alert_receivers', type: 'list', label: '告警接收人', desc: '钉钉 userid，每行一个' },
  { key: 'task_payload_ttl_days', type: 'int', label: 'task_payload 保留天数', desc: '过期 payload 自动清理' },
  { key: 'task_log_ttl_days', type: 'int', label: 'task_log 保留天数', desc: '历史任务保留天数' },
  { key: 'daily_audit_hour', type: 'int', label: '每日巡检时刻', desc: '0-23 整数' },
  { key: 'low_confidence_threshold', type: 'float', label: '低置信度阈值', desc: '0-1 之间，触发人工审批' },
]

const configs = reactive(KNOWN.map((k) => ({ ...k, value: null, input: '', saving: false, error: '' })))

async function load() {
  for (const cfg of configs) {
    try {
      const data = await getConfig(cfg.key)
      cfg.value = data.value
      cfg.input = formatInput(cfg, data.value)
    } catch (e) {
      cfg.error = pickErrorDetail(e, '加载失败')
    }
  }
}

function formatInput(cfg, value) {
  if (value == null) return ''
  if (cfg.type === 'list') return Array.isArray(value) ? value.join('\n') : ''
  return String(value)
}

function parseInput(cfg) {
  if (cfg.type === 'list') {
    return cfg.input.split('\n').map((s) => s.trim()).filter(Boolean)
  }
  if (cfg.type === 'int') {
    const v = Number(cfg.input)
    if (!Number.isInteger(v)) throw new Error('请填写整数')
    return v
  }
  if (cfg.type === 'float') {
    const v = Number(cfg.input)
    if (Number.isNaN(v)) throw new Error('请填写数字')
    return v
  }
  return cfg.input
}

async function saveOne(cfg) {
  cfg.error = ''
  let value
  try {
    value = parseInput(cfg)
  } catch (e) {
    cfg.error = e.message
    return
  }
  cfg.saving = true
  try {
    await setConfig(cfg.key, value)
    cfg.value = value
    appStore.showToast('已保存')
  } catch (e) {
    cfg.error = pickErrorDetail(e, '保存失败')
  } finally {
    cfg.saving = false
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
.hub-config {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.hub-config__item {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
}
.hub-config__head {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.hub-config__name { font-size: 13px; font-weight: 600; color: var(--text); }
.hub-config__sub { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.hub-config__key {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--elevated);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
}
.hub-config__body {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.hub-config__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.hub-config__actions { display: flex; justify-content: flex-end; }
.hub-config__error {
  color: var(--error);
  font-size: 12px;
}
</style>
