<template>
  <div class="hub-page">
    <h1 class="hub-page__title">渠道应用</h1>
    <p class="hub-page__hint">钉钉等消息渠道的 AppKey/AppSecret 配置。修改后会立刻重连 Stream。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <div class="hub-toolbar__spacer"></div>
      <AppButton variant="primary" size="sm" icon="plus" @click="openCreate">新建</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="暂无渠道">
        <template #header>
          <tr>
            <th class="app-th">名称</th>
            <th class="app-th">渠道</th>
            <th class="app-th">机器人 ID</th>
            <th class="app-th">密钥</th>
            <th class="app-th">状态</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="c in items" :key="c.id">
          <td class="app-td font-medium">{{ c.name }}</td>
          <td class="app-td">{{ channelLabel(c.channel_type) }}</td>
          <td class="app-td text-muted">{{ c.robot_id || '—' }}</td>
          <td class="app-td"><AppBadge :variant="c.secret_set ? 'success' : 'gray'">{{ c.secret_set ? '已配置' : '未配置' }}</AppBadge></td>
          <td class="app-td"><AppBadge :variant="statusVariant(c.status)">{{ statusLabel(c.status) }}</AppBadge></td>
          <td class="app-td text-right">
            <AppButton variant="secondary" size="xs" class="mr-1" @click="openEdit(c)">改 Secret</AppButton>
            <AppButton v-if="c.status === 'active'" variant="danger" size="xs" @click="onDisable(c)">停用</AppButton>
          </td>
        </tr>
      </AppTable>
    </AppCard>

    <AppModal :visible="showCreate" title="新建渠道" size="md" @update:visible="(v) => { if (!v) showCreate = false }">
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <div class="hub-form">
        <label class="hub-form__field"><span>渠道</span>
          <AppSelect v-model="createForm.channel_type" :options="channelOptions" />
        </label>
        <label class="hub-form__field"><span>名称</span><AppInput v-model="createForm.name" /></label>
        <label class="hub-form__field"><span>AppKey</span><AppInput v-model="createForm.app_key" /></label>
        <label class="hub-form__field"><span>AppSecret</span><AppInput v-model="createForm.app_secret" type="password" /></label>
        <label class="hub-form__field"><span>机器人 ID（可选）</span><AppInput v-model="createForm.robot_id" /></label>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="showCreate = false">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onCreate">保存</AppButton>
      </template>
    </AppModal>

    <AppModal :visible="!!editing" :title="editing ? `修改渠道：${editing.name}` : ''" size="sm" @update:visible="(v) => { if (!v) editing = null }">
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <p class="hub-page__hint">不填的字段保持原值。</p>
      <div class="hub-form">
        <label class="hub-form__field"><span>新 AppKey</span><AppInput v-model="editForm.app_key" placeholder="留空表示不改" /></label>
        <label class="hub-form__field"><span>新 AppSecret</span><AppInput v-model="editForm.app_secret" type="password" placeholder="留空表示不改" /></label>
        <label class="hub-form__field"><span>新机器人 ID</span><AppInput v-model="editForm.robot_id" placeholder="留空表示不改" /></label>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="editing = null">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onEdit">保存</AppButton>
      </template>
    </AppModal>

    <!-- M15: 停用二次确认 modal（替换 confirm()） -->
    <AppModal
      :visible="showConfirmModal"
      title="确认操作"
      size="sm"
      @update:visible="(v) => { if (!v) showConfirmModal = false }"
    >
      <p>{{ confirmMessage }}</p>
      <template #footer>
        <AppButton variant="ghost" size="sm" @click="showConfirmModal = false">取消</AppButton>
        <AppButton variant="danger" size="sm" @click="confirmAction?.()">确认停用</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  listChannels, createChannel, updateChannel, disableChannel,
} from '../../api/channels'
import { pickErrorDetail } from '../../api'
import { statusLabel, statusVariant, channelLabel } from '../../utils/format'
import { useAppStore } from '../../stores/app'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppSelect from '../../components/ui/AppSelect.vue'

const appStore = useAppStore()
const items = ref([])
const error = ref('')
const showCreate = ref(false)
const editing = ref(null)
const saving = ref(false)
const modalError = ref('')
const showConfirmModal = ref(false)
const confirmAction = ref(null)
const confirmMessage = ref('')

const channelOptions = [
  { value: 'dingtalk', label: '钉钉' },
]

const createForm = reactive({
  channel_type: 'dingtalk',
  name: '',
  app_key: '',
  app_secret: '',
  robot_id: '',
})
const editForm = reactive({ app_key: '', app_secret: '', robot_id: '' })

async function load() {
  try {
    const data = await listChannels()
    items.value = data.items || []
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function openCreate() {
  Object.assign(createForm, {
    channel_type: 'dingtalk', name: '', app_key: '', app_secret: '', robot_id: '',
  })
  modalError.value = ''
  showCreate.value = true
}

async function onCreate() {
  modalError.value = ''
  if (!createForm.name || !createForm.app_key || !createForm.app_secret) {
    modalError.value = '请填写名称、AppKey、AppSecret'
    return
  }
  saving.value = true
  try {
    const payload = { ...createForm }
    if (!payload.robot_id) delete payload.robot_id
    await createChannel(payload)
    appStore.showToast('已创建')
    showCreate.value = false
    load()
  } catch (e) {
    modalError.value = pickErrorDetail(e, '创建失败')
  } finally {
    saving.value = false
  }
}

function openEdit(c) {
  editing.value = c
  Object.assign(editForm, { app_key: '', app_secret: '', robot_id: '' })
  modalError.value = ''
}

async function onEdit() {
  modalError.value = ''
  saving.value = true
  try {
    const body = {}
    if (editForm.app_key) body.app_key = editForm.app_key
    if (editForm.app_secret) body.app_secret = editForm.app_secret
    if (editForm.robot_id) body.robot_id = editForm.robot_id
    await updateChannel(editing.value.id, body)
    appStore.showToast('已更新')
    editing.value = null
    load()
  } catch (e) {
    modalError.value = pickErrorDetail(e, '更新失败')
  } finally {
    saving.value = false
  }
}

async function onDisable(c) {
  confirmMessage.value = `确认停用「${c.name}」吗？停用后相关功能将不可用。`
  confirmAction.value = async () => {
    try {
      await disableChannel(c.id)
      appStore.showToast('已停用')
      showConfirmModal.value = false
      load()
    } catch (e) {
      showConfirmModal.value = false
      appStore.showToast(pickErrorDetail(e, '停用失败'), 'error')
    }
  }
  showConfirmModal.value = true
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
.hub-toolbar { display: flex; align-items: center; gap: 8px; }
.hub-toolbar__spacer { flex: 1; }
.hub-form { display: flex; flex-direction: column; gap: 12px; }
.hub-form__field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-muted); }
</style>
