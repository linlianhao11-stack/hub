<template>
  <div class="setup-step">
    <h2 class="setup-step__title">系统自检 + 校验初始化 Token</h2>
    <p class="setup-step__hint">
      启动 HUB Gateway 时控制台会输出一次性初始化 Token（{{ ttlHint }}）。请粘贴到下面校验，校验通过后才能进入后续步骤。
    </p>

    <div v-if="error" class="setup-step__error">{{ error }}</div>

    <section class="setup-step__panel">
      <h3 class="setup-step__panel-title">系统自检</h3>
      <ul v-if="welcome" class="setup-step__check-list">
        <li v-for="(value, key) in welcome.checks" :key="key">
          <span class="setup-step__check-name">{{ checkLabel(key) }}</span>
          <span class="setup-step__check-value" :class="checkClass(value)">{{ value }}</span>
        </li>
      </ul>
      <p v-else class="setup-step__hint">检测中…</p>
    </section>

    <section class="setup-step__panel">
      <h3 class="setup-step__panel-title">初始化 Token</h3>
      <AppInput v-model="tokenInput" placeholder="粘贴控制台输出的初始化 Token" />
      <div class="setup-step__actions">
        <AppButton variant="primary" :loading="submitting" @click="onVerify">校验并继续</AppButton>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref, inject } from 'vue'
import { getWelcome, verifyToken } from '../../../api/setup'
import { pickErrorDetail } from '../../../api'
import AppInput from '../../../components/ui/AppInput.vue'
import AppButton from '../../../components/ui/AppButton.vue'

const emit = defineEmits(['next'])
const setupCtx = inject('hubSetupSession')

const welcome = ref(null)
const tokenInput = ref('')
const submitting = ref(false)
const error = ref('')
const ttlHint = '默认 30 分钟内有效'

onMounted(async () => {
  try {
    welcome.value = await getWelcome()
  } catch (e) {
    if (e?.response?.status === 404) {
      // 已初始化
      error.value = 'HUB 已完成初始化，请前往 /login 登录。'
    } else {
      error.value = pickErrorDetail(e, '自检失败')
    }
  }
})

function checkLabel(key) {
  return { master_key: '主加密 Key', postgres: 'PostgreSQL', redis: 'Redis' }[key] || key
}

function checkClass(value) {
  if (value === 'ok' || value === 'configured') return 'is-ok'
  return 'is-bad'
}

async function onVerify() {
  if (!tokenInput.value.trim()) {
    error.value = '请粘贴 Token'
    return
  }
  error.value = ''
  submitting.value = true
  try {
    const data = await verifyToken(tokenInput.value.trim())
    setupCtx.set(data.session)
    emit('next')
  } catch (e) {
    error.value = pickErrorDetail(e, 'Token 校验失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.setup-step {
  width: 100%;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.setup-step__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}
.setup-step__hint {
  font-size: 12px;
  color: var(--text-muted);
  margin: 0;
}
.setup-step__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.setup-step__panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.setup-step__panel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}
.setup-step__check-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.setup-step__check-list li {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}
.setup-step__check-name { color: var(--text-muted); }
.setup-step__check-value { font-family: var(--font-mono); }
.setup-step__check-value.is-ok { color: var(--success); }
.setup-step__check-value.is-bad { color: var(--error); }
.setup-step__actions {
  display: flex;
  justify-content: flex-end;
}
</style>
