<template>
  <div class="setup-step">
    <h2 class="setup-step__title">注册钉钉企业内部应用</h2>
    <p class="setup-step__hint">
      把钉钉开放平台后台的 AppKey/AppSecret 粘贴进来。保存后 HUB 会立刻重连 Stream，开始接收钉钉消息。
    </p>

    <div v-if="error" class="setup-step__error">{{ error }}</div>

    <section class="setup-step__panel">
      <label class="setup-step__field">
        <span>名称</span>
        <AppInput v-model="form.name" placeholder="比如：销售部钉钉" />
      </label>
      <label class="setup-step__field">
        <span>AppKey</span>
        <AppInput v-model="form.app_key" />
      </label>
      <label class="setup-step__field">
        <span>AppSecret</span>
        <AppInput v-model="form.app_secret" type="password" />
      </label>
      <label class="setup-step__field">
        <span>机器人 ID（可选）</span>
        <AppInput v-model="form.robot_id" placeholder="如果钉钉对话还需要单独绑机器人 ID" />
      </label>
    </section>

    <div class="setup-step__actions">
      <AppButton variant="primary" :loading="submitting" @click="onSubmit">保存</AppButton>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { connectDingtalk } from '../../../api/setup'
import { pickErrorDetail } from '../../../api'
import AppInput from '../../../components/ui/AppInput.vue'
import AppButton from '../../../components/ui/AppButton.vue'

const props = defineProps({ session: { type: String, required: true } })
const emit = defineEmits(['next'])

const form = reactive({
  name: '钉钉企业内部应用',
  app_key: '',
  app_secret: '',
  robot_id: '',
})
const submitting = ref(false)
const error = ref('')

async function onSubmit() {
  if (!form.app_key || !form.app_secret) {
    error.value = '请填写 AppKey 和 AppSecret'
    return
  }
  error.value = ''
  submitting.value = true
  try {
    const payload = { ...form }
    if (!payload.robot_id) delete payload.robot_id
    await connectDingtalk(props.session, payload)
    emit('next')
  } catch (e) {
    error.value = pickErrorDetail(e, '保存钉钉配置失败')
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
