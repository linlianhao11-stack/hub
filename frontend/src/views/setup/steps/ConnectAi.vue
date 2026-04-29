<template>
  <div class="setup-step">
    <h2 class="setup-step__title">配置 AI 提供商</h2>
    <p class="setup-step__hint">
      HUB 用 AI 解析自然语言指令。可选，跳过的话先用 fallback 规则解析；填写后会真实调一次 chat 确认 ApiKey 有效。
    </p>

    <div v-if="error" class="setup-step__error">{{ error }}</div>

    <section class="setup-step__panel">
      <label class="setup-step__field">
        <span>提供商</span>
        <AppSelect v-model="form.provider_type" :options="providerOptions" @change="applyDefaults" />
      </label>
      <label class="setup-step__field">
        <span>名称</span>
        <AppInput v-model="form.name" placeholder="比如：DeepSeek 默认" />
      </label>
      <label class="setup-step__field">
        <span>API Key</span>
        <AppInput v-model="form.api_key" type="password" />
      </label>
      <label class="setup-step__field">
        <span>Base URL</span>
        <AppInput v-model="form.base_url" />
      </label>
      <label class="setup-step__field">
        <span>模型</span>
        <AppInput v-model="form.model" />
      </label>
    </section>

    <div class="setup-step__actions">
      <AppButton variant="ghost" @click="emit('skip')">暂时跳过</AppButton>
      <AppButton variant="primary" :loading="submitting" @click="onSubmit">测试并保存</AppButton>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { connectAi } from '../../../api/setup'
import { pickErrorDetail } from '../../../api'
import AppInput from '../../../components/ui/AppInput.vue'
import AppButton from '../../../components/ui/AppButton.vue'
import AppSelect from '../../../components/ui/AppSelect.vue'

const props = defineProps({ session: { type: String, required: true } })
const emit = defineEmits(['next', 'skip'])

const providerDefaults = {
  deepseek: {
    name: 'DeepSeek 默认',
    base_url: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
  },
  qwen: {
    name: 'Qwen 默认',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
  },
}

const providerOptions = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'qwen', label: '通义千问 Qwen' },
]

const form = reactive({
  provider_type: 'deepseek',
  name: providerDefaults.deepseek.name,
  api_key: '',
  base_url: providerDefaults.deepseek.base_url,
  model: providerDefaults.deepseek.model,
})
const submitting = ref(false)
const error = ref('')

function applyDefaults() {
  const d = providerDefaults[form.provider_type]
  if (!d) return
  form.name = d.name
  form.base_url = d.base_url
  form.model = d.model
}

async function onSubmit() {
  if (!form.api_key) {
    error.value = '请填写 API Key'
    return
  }
  error.value = ''
  submitting.value = true
  try {
    await connectAi(props.session, { ...form })
    emit('next')
  } catch (e) {
    error.value = pickErrorDetail(e, 'AI 配置失败')
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
.setup-step__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
