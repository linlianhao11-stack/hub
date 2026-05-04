<template>
  <div class="hub-page">
    <h1 class="hub-page__title">AI 提供商</h1>
    <p class="hub-page__hint">同时只允许一个 active。新建后默认为 active，会自动停用其他。</p>

    <div class="hub-page__notice hub-page__notice--warning">
      ⚠️ 切换 active 提供商或改 API Key 后，正在运行的 worker 仍用旧 provider。需要在主机执行
      <code>docker compose restart hub-worker</code> 让 LLMParser 用上新配置（gateway 不受影响）。
    </div>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <div class="hub-toolbar__spacer"></div>
      <AppButton variant="primary" size="sm" icon="plus" @click="openCreate">新建</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="暂无 AI 提供商">
        <template #header>
          <tr>
            <th class="app-th">名称</th>
            <th class="app-th">类型</th>
            <th class="app-th">Base URL</th>
            <th class="app-th">模型</th>
            <th class="app-th">状态</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="a in items" :key="a.id">
          <td class="app-td font-medium">{{ a.name }}</td>
          <td class="app-td">{{ providerLabel(a.provider_type) }}</td>
          <td class="app-td text-muted">{{ a.base_url }}</td>
          <td class="app-td"><span class="std-num">{{ a.model }}</span></td>
          <td class="app-td"><AppBadge :variant="statusVariant(a.status)">{{ statusLabel(a.status) }}</AppBadge></td>
          <td class="app-td text-right">
            <AppButton variant="secondary" size="xs" class="mr-1" @click="onEdit(a)">编辑</AppButton>
            <AppButton variant="secondary" size="xs" class="mr-1" @click="onTest(a)" :loading="testing === a.id">测试 chat</AppButton>
            <AppButton v-if="a.status !== 'active'" variant="primary" size="xs" class="mr-1" @click="onActive(a)">启用</AppButton>
            <AppButton v-if="a.status === 'active'" variant="danger" size="xs" @click="onDisable(a)" :loading="disabling === a.id">停用</AppButton>
          </td>
        </tr>
      </AppTable>
    </AppCard>

    <AppModal :visible="showCreate" title="新建 AI 提供商" size="md" @update:visible="(v) => { if (!v) showCreate = false }">
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <div class="hub-form">
        <label class="hub-form__field"><span>提供商</span>
          <AppSelect v-model="createForm.provider_type" :options="providerOptions" @change="applyDefaults" />
        </label>
        <label class="hub-form__field"><span>名称</span><AppInput v-model="createForm.name" /></label>
        <label class="hub-form__field"><span>API Key</span><AppInput v-model="createForm.api_key" type="password" /></label>
        <label class="hub-form__field"><span>Base URL</span><AppInput v-model="createForm.base_url" /></label>
        <label class="hub-form__field"><span>模型</span><AppInput v-model="createForm.model" /></label>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="showCreate = false">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onCreate">保存</AppButton>
      </template>
    </AppModal>

    <AppModal :visible="showEdit" title="编辑 AI 提供商" size="md" @update:visible="(v) => { if (!v) showEdit = false }">
      <div v-if="editError" class="hub-page__error">{{ editError }}</div>
      <div class="hub-form">
        <label class="hub-form__field"><span>提供商类型</span>
          <AppInput :model-value="providerLabel(editForm.provider_type)" disabled />
          <span class="hub-form__hint">类型不可改 — 要换种类请新建一个再设为 active</span>
        </label>
        <label class="hub-form__field"><span>名称</span><AppInput v-model="editForm.name" /></label>
        <label class="hub-form__field"><span>API Key</span>
          <AppInput v-model="editForm.api_key" type="password" placeholder="留空表示沿用旧 key" />
          <span class="hub-form__hint">出于安全,旧 key 不会回显;留空提交不会改 key</span>
        </label>
        <label class="hub-form__field"><span>Base URL</span><AppInput v-model="editForm.base_url" /></label>
        <label class="hub-form__field"><span>模型</span><AppInput v-model="editForm.model" /></label>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="showEdit = false">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="updating" @click="onUpdate">保存</AppButton>
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
  listAi, createAi, updateAi, testAiChat, setAiActive, disableAi, getAiDefaults,
} from '../../api/ai'
import { pickErrorDetail } from '../../api'
import { statusLabel, statusVariant } from '../../utils/format'
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
const saving = ref(false)
const testing = ref(null)
const disabling = ref(null)
const modalError = ref('')
const defaults = ref({})
const showConfirmModal = ref(false)
const confirmAction = ref(null)
const confirmMessage = ref('')

// 编辑 modal 状态
const showEdit = ref(false)
const updating = ref(false)
const editError = ref('')
const editForm = reactive({
  id: null,
  provider_type: '',
  name: '',
  api_key: '',  // 留空 = 不改
  base_url: '',
  model: '',
})

const providerOptions = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'qwen', label: '通义千问 Qwen' },
]

function providerLabel(t) {
  return providerOptions.find((o) => o.value === t)?.label || t
}

const createForm = reactive({
  provider_type: 'deepseek',
  name: 'DeepSeek 默认',
  api_key: '',
  base_url: '',
  model: '',
})

function applyDefaults() {
  const d = defaults.value[createForm.provider_type]
  if (d) {
    createForm.base_url = d.base_url
    createForm.model = d.model
  }
  createForm.name = `${createForm.provider_type} 默认`
}

async function load() {
  try {
    const data = await listAi()
    items.value = data.items || []
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

async function loadDefaults() {
  try {
    defaults.value = await getAiDefaults()
    applyDefaults()
  } catch (e) {
    // ignore，让用户手动填
  }
}

function openCreate() {
  Object.assign(createForm, {
    provider_type: 'deepseek', name: 'DeepSeek 默认', api_key: '', base_url: '', model: '',
  })
  applyDefaults()
  modalError.value = ''
  showCreate.value = true
}

async function onCreate() {
  modalError.value = ''
  if (!createForm.api_key) { modalError.value = '请填写 API Key'; return }
  saving.value = true
  try {
    await createAi({ ...createForm })
    appStore.showToast('已创建（已设为 active）')
    showCreate.value = false
    load()
  } catch (e) {
    modalError.value = pickErrorDetail(e, '创建失败')
  } finally {
    saving.value = false
  }
}

async function onTest(a) {
  testing.value = a.id
  try {
    const data = await testAiChat(a.id)
    if (data.ok) appStore.showToast('chat 通过')
    else appStore.showToast(`chat 失败：${data.error || '未知'}`, 'error')
  } catch (e) {
    appStore.showToast(pickErrorDetail(e, '测试失败'), 'error')
  } finally {
    testing.value = null
  }
}

async function onActive(a) {
  try {
    await setAiActive(a.id)
    appStore.showToast('已启用（其他已自动停用）')
    load()
  } catch (e) {
    appStore.showToast(pickErrorDetail(e, '启用失败'), 'error')
  }
}

async function onDisable(a) {
  confirmMessage.value = `确定停用「${a.name}」？停用后 worker 重启将无 active provider，LLM 路径会降级到 RuleParser。`
  confirmAction.value = async () => {
    disabling.value = a.id
    try {
      await disableAi(a.id)
      appStore.showToast('已停用')
      showConfirmModal.value = false
      load()
    } catch (e) {
      showConfirmModal.value = false
      appStore.showToast(pickErrorDetail(e, '停用失败'), 'error')
    } finally {
      disabling.value = null
    }
  }
  showConfirmModal.value = true
}

function onEdit(a) {
  Object.assign(editForm, {
    id: a.id,
    provider_type: a.provider_type,
    name: a.name || '',
    api_key: '',  // 留空 — 提示用户旧 key 沿用
    base_url: a.base_url || '',
    model: a.model || '',
  })
  editError.value = ''
  showEdit.value = true
}

async function onUpdate() {
  editError.value = ''
  updating.value = true
  try {
    // 仅传非空字段（api_key 留空 → 不发字段，避免后端 None 误清空）
    const body = {}
    if (editForm.name && editForm.name.trim()) body.name = editForm.name.trim()
    if (editForm.base_url && editForm.base_url.trim()) body.base_url = editForm.base_url.trim()
    if (editForm.model && editForm.model.trim()) body.model = editForm.model.trim()
    if (editForm.api_key && editForm.api_key.trim()) body.api_key = editForm.api_key.trim()
    await updateAi(editForm.id, body)
    appStore.showToast('已保存')
    showEdit.value = false
    load()
  } catch (e) {
    editError.value = pickErrorDetail(e, '保存失败')
  } finally {
    updating.value = false
  }
}

onMounted(() => {
  load()
  loadDefaults()
})
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
.hub-page__notice {
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.6;
}
.hub-page__notice--warning {
  background: color-mix(in srgb, var(--warning, #eab308) 14%, transparent);
  color: var(--warning-emphasis, #854d0e);
  border: 1px solid color-mix(in srgb, var(--warning, #eab308) 35%, transparent);
}
.hub-page__notice code {
  font-family: var(--font-mono);
  background: color-mix(in srgb, var(--text) 8%, transparent);
  padding: 1px 6px;
  border-radius: 4px;
}
.hub-toolbar { display: flex; align-items: center; gap: 8px; }
.hub-toolbar__spacer { flex: 1; }
.hub-form { display: flex; flex-direction: column; gap: 12px; }
.hub-form__field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-muted); }
.hub-form__hint { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
</style>
