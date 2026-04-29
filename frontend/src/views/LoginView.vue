<template>
  <div class="hub-login">
    <div class="hub-login__panel">
      <div class="hub-login__brand">
        <div class="hub-login__logo">H</div>
        <div>
          <h1 class="hub-login__title">HUB 后台</h1>
          <p class="hub-login__subtitle">使用 ERP 账号登录</p>
        </div>
      </div>

      <form class="hub-login__form" @submit.prevent="onSubmit">
        <div v-if="error" class="hub-login__error">{{ error }}</div>

        <label class="hub-login__field">
          <span>用户名</span>
          <AppInput v-model="form.username" placeholder="ERP 用户名" autocomplete="username" />
        </label>
        <label class="hub-login__field">
          <span>密码</span>
          <AppInput v-model="form.password" type="password" placeholder="ERP 密码" autocomplete="current-password" />
        </label>

        <AppButton variant="primary" type="submit" :loading="submitting" block>登录</AppButton>
      </form>
    </div>
    <p class="hub-login__hint">如未完成初始化，请管理员前往 <router-link to="/setup">/setup</router-link></p>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { pickErrorDetail } from '../api'
import AppInput from '../components/ui/AppInput.vue'
import AppButton from '../components/ui/AppButton.vue'

const router = useRouter()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const submitting = ref(false)
const error = ref('')

async function onSubmit() {
  if (!form.username || !form.password) {
    error.value = '请填写用户名和密码'
    return
  }
  error.value = ''
  submitting.value = true
  try {
    await auth.login(form.username, form.password)
    router.replace('/admin')
  } catch (e) {
    if (e?.response?.status === 503) {
      error.value = 'HUB 尚未完成初始化，请先访问 /setup'
    } else {
      error.value = pickErrorDetail(e, '登录失败')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.hub-login {
  min-height: 100vh;
  background: var(--background);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 24px;
}
.hub-login__panel {
  width: 100%;
  max-width: 360px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 22px;
  box-shadow: var(--sh-md);
}
.hub-login__brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hub-login__logo {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--primary);
  color: var(--on-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 18px;
  letter-spacing: -0.02em;
}
.hub-login__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.01em;
}
.hub-login__subtitle {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}
.hub-login__form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.hub-login__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.hub-login__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-login__hint {
  font-size: 12px;
  color: var(--text-muted);
}
.hub-login__hint a {
  color: var(--primary);
  text-decoration: none;
}
</style>
