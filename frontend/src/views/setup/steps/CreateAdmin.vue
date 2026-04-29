<template>
  <div class="setup-step">
    <h2 class="setup-step__title">创建第一个管理员</h2>
    <p class="setup-step__hint">
      使用 ERP 用户名 + 密码登录一次。HUB 会创建对应 hub_user，并赋予 platform_admin 全部权限。
    </p>

    <div v-if="error" class="setup-step__error">{{ error }}</div>

    <section class="setup-step__panel">
      <label class="setup-step__field">
        <span>ERP 用户名</span>
        <AppInput v-model="form.erp_username" autocomplete="username" />
      </label>
      <label class="setup-step__field">
        <span>ERP 密码</span>
        <AppInput v-model="form.erp_password" type="password" autocomplete="current-password" />
      </label>
    </section>

    <div class="setup-step__actions">
      <AppButton variant="primary" :loading="submitting" @click="onSubmit">登录并创建</AppButton>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { createAdmin } from '../../../api/setup'
import { pickErrorDetail } from '../../../api'
import AppInput from '../../../components/ui/AppInput.vue'
import AppButton from '../../../components/ui/AppButton.vue'

const props = defineProps({ session: { type: String, required: true } })
const emit = defineEmits(['next'])

const form = reactive({ erp_username: '', erp_password: '' })
const submitting = ref(false)
const error = ref('')

async function onSubmit() {
  if (!form.erp_username || !form.erp_password) {
    error.value = '请填写用户名和密码'
    return
  }
  error.value = ''
  submitting.value = true
  try {
    await createAdmin(props.session, form)
    emit('next')
  } catch (e) {
    error.value = pickErrorDetail(e, '创建管理员失败')
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
.setup-step__panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.setup-step__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.setup-step__actions { display: flex; justify-content: flex-end; }
</style>
