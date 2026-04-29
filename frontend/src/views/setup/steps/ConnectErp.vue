<template>
  <div class="setup-step">
    <h2 class="setup-step__title">连接 ERP 系统</h2>
    <p class="setup-step__hint">
      填写 ERP 后台对外的 base URL 和 ApiKey。HUB 会先调一次 health check 确认能连通，再保存。
    </p>

    <div v-if="error" class="setup-step__error">{{ error }}</div>

    <section class="setup-step__panel">
      <label class="setup-step__field">
        <span>名称</span>
        <AppInput v-model="form.name" placeholder="比如：主 ERP" />
      </label>
      <label class="setup-step__field">
        <span>Base URL</span>
        <AppInput v-model="form.base_url" placeholder="https://erp.example.com" />
      </label>
      <label class="setup-step__field">
        <span>ApiKey</span>
        <AppInput v-model="form.api_key" type="password" placeholder="ApiKey 明文（写库前会加密）" />
      </label>
      <label class="setup-step__field">
        <span>授权 scope（逗号分隔）</span>
        <AppInput v-model="scopesInput" placeholder="orders.read, products.read" />
        <small class="setup-step__sub">可填多个，比如 orders.read, products.read, users.write</small>
      </label>
    </section>

    <div class="setup-step__actions">
      <AppButton variant="primary" :loading="submitting" @click="onSubmit">测试并保存</AppButton>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed } from 'vue'
import { connectErp } from '../../../api/setup'
import { pickErrorDetail } from '../../../api'
import AppInput from '../../../components/ui/AppInput.vue'
import AppButton from '../../../components/ui/AppButton.vue'

const props = defineProps({ session: { type: String, required: true } })
const emit = defineEmits(['next'])

const form = reactive({
  name: '主 ERP',
  base_url: '',
  api_key: '',
})
const scopesInput = ref('orders.read, products.read, users.read')
const submitting = ref(false)
const error = ref('')

const scopes = computed(() =>
  scopesInput.value.split(',').map((s) => s.trim()).filter(Boolean),
)

async function onSubmit() {
  error.value = ''
  if (!form.name || !form.base_url || !form.api_key) {
    error.value = '请填写名称、Base URL 和 ApiKey'
    return
  }
  if (!scopes.value.length) {
    error.value = '请至少填一个 scope'
    return
  }
  submitting.value = true
  try {
    await connectErp(props.session, { ...form, apikey_scopes: scopes.value })
    emit('next')
  } catch (e) {
    error.value = pickErrorDetail(e, '注册 ERP 失败')
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
  gap: 12px;
}
.setup-step__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.setup-step__sub {
  color: var(--text-muted);
  font-size: 11px;
}
.setup-step__actions {
  display: flex;
  justify-content: flex-end;
}
</style>
