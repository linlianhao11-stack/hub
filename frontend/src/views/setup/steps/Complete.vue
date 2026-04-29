<template>
  <div class="setup-step">
    <h2 class="setup-step__title">即将完成初始化</h2>
    <p class="setup-step__hint">
      点击「完成」后，HUB 会写入 system_initialized=true，关闭 setup 路由。今后再访问 /setup 会被跳到 /login。
    </p>

    <div v-if="error" class="setup-step__error">{{ error }}</div>
    <div v-if="success" class="setup-step__success">已完成。3 秒后跳转到登录页…</div>

    <div class="setup-step__actions">
      <AppButton variant="primary" :loading="submitting" :disabled="success" @click="onSubmit">
        完成
      </AppButton>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { setupComplete } from '../../../api/setup'
import { pickErrorDetail } from '../../../api'
import AppButton from '../../../components/ui/AppButton.vue'

const props = defineProps({ session: { type: String, required: true } })
const router = useRouter()

const submitting = ref(false)
const error = ref('')
const success = ref(false)

async function onSubmit() {
  error.value = ''
  submitting.value = true
  try {
    await setupComplete(props.session)
    success.value = true
    sessionStorage.removeItem('hub_setup_session')
    setTimeout(() => router.replace('/login'), 2000)
  } catch (e) {
    error.value = pickErrorDetail(e, '完成初始化失败')
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
.setup-step__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.setup-step__hint { font-size: 12px; color: var(--text-muted); margin: 0; }
.setup-step__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.setup-step__success {
  background: color-mix(in srgb, var(--success) 12%, transparent);
  color: var(--success);
  border: 1px solid color-mix(in srgb, var(--success) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.setup-step__actions { display: flex; justify-content: flex-end; }
</style>
