<template>
  <div class="hub-page">
    <h1 class="hub-page__title">下游系统</h1>
    <p class="hub-page__hint">管理 ERP 等下游 ApiKey、scope 与连通性测试。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <div class="hub-toolbar__spacer"></div>
      <AppButton variant="primary" size="sm" icon="plus" @click="openCreate">新建</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="暂无下游">
        <template #header>
          <tr>
            <th class="app-th">名称</th>
            <th class="app-th">类型</th>
            <th class="app-th">Base URL</th>
            <th class="app-th">Scope</th>
            <th class="app-th">ApiKey</th>
            <th class="app-th">状态</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="d in items" :key="d.id">
          <td class="app-td font-medium">{{ d.name }}</td>
          <td class="app-td">{{ downstreamLabel(d.downstream_type) }}</td>
          <td class="app-td text-muted">{{ d.base_url }}</td>
          <td class="app-td text-muted">{{ (d.apikey_scopes || []).join(', ') || '—' }}</td>
          <td class="app-td"><AppBadge :variant="d.apikey_set ? 'success' : 'gray'">{{ d.apikey_set ? '已配置' : '未配置' }}</AppBadge></td>
          <td class="app-td"><AppBadge :variant="statusVariant(d.status)">{{ statusLabel(d.status) }}</AppBadge></td>
          <td class="app-td text-right">
            <AppButton variant="secondary" size="xs" class="mr-1" @click="onTest(d)" :loading="testing === d.id">测试</AppButton>
            <AppButton variant="secondary" size="xs" class="mr-1" @click="openRotate(d)">改 ApiKey</AppButton>
            <AppButton v-if="d.status === 'active'" variant="danger" size="xs" @click="onDisable(d)">停用</AppButton>
          </td>
        </tr>
      </AppTable>
    </AppCard>

    <!-- Create -->
    <AppModal :visible="showCreate" title="新建下游" size="md" @update:visible="(v) => { if (!v) showCreate = false }">
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <div class="hub-form">
        <label class="hub-form__field"><span>类型</span>
          <AppSelect v-model="createForm.downstream_type" :options="dsOptions" />
        </label>
        <label class="hub-form__field"><span>名称</span><AppInput v-model="createForm.name" /></label>
        <label class="hub-form__field"><span>Base URL</span><AppInput v-model="createForm.base_url" /></label>
        <label class="hub-form__field"><span>ApiKey</span><AppInput v-model="createForm.api_key" type="password" /></label>
        <label class="hub-form__field"><span>Scope（逗号分隔）</span><AppInput v-model="createForm.scopes_input" /></label>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="showCreate = false">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onCreate">保存</AppButton>
      </template>
    </AppModal>

    <!-- Rotate ApiKey -->
    <AppModal :visible="!!rotating" :title="rotating ? `改 ApiKey：${rotating.name}` : ''" size="sm" @update:visible="(v) => { if (!v) rotating = null }">
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <label class="hub-form__field"><span>新 ApiKey</span><AppInput v-model="rotateForm.api_key" type="password" /></label>
      <label class="hub-form__field"><span>Scope（可选，逗号分隔）</span><AppInput v-model="rotateForm.scopes_input" placeholder="不填表示不改" /></label>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="rotating = null">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onRotate">保存</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  listDownstreams, createDownstream, updateDownstreamApiKey, testDownstream, disableDownstream,
} from '../../api/downstreams'
import { pickErrorDetail } from '../../api'
import { statusLabel, statusVariant, downstreamLabel } from '../../utils/format'
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
const rotating = ref(null)
const saving = ref(false)
const modalError = ref('')
const testing = ref(null)

const dsOptions = [
  { value: 'erp', label: 'ERP 系统' },
  { value: 'crm', label: 'CRM 系统' },
  { value: 'wms', label: '仓储 WMS' },
]

const createForm = reactive({
  downstream_type: 'erp',
  name: '',
  base_url: '',
  api_key: '',
  scopes_input: 'orders.read, products.read',
})
const rotateForm = reactive({ api_key: '', scopes_input: '' })

function parseScopes(s) {
  return s.split(',').map((x) => x.trim()).filter(Boolean)
}

async function load() {
  try {
    const data = await listDownstreams()
    items.value = data.items || []
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function openCreate() {
  Object.assign(createForm, {
    downstream_type: 'erp', name: '', base_url: '', api_key: '',
    scopes_input: 'orders.read, products.read',
  })
  modalError.value = ''
  showCreate.value = true
}

async function onCreate() {
  modalError.value = ''
  if (!createForm.name || !createForm.base_url || !createForm.api_key) {
    modalError.value = '请填写名称、Base URL 和 ApiKey'
    return
  }
  const scopes = parseScopes(createForm.scopes_input)
  if (!scopes.length) { modalError.value = '请至少填一个 scope'; return }
  saving.value = true
  try {
    await createDownstream({
      downstream_type: createForm.downstream_type,
      name: createForm.name,
      base_url: createForm.base_url,
      api_key: createForm.api_key,
      apikey_scopes: scopes,
    })
    appStore.showToast('已创建')
    showCreate.value = false
    load()
  } catch (e) {
    modalError.value = pickErrorDetail(e, '创建失败')
  } finally {
    saving.value = false
  }
}

function openRotate(d) {
  rotating.value = d
  rotateForm.api_key = ''
  rotateForm.scopes_input = (d.apikey_scopes || []).join(', ')
  modalError.value = ''
}

async function onRotate() {
  modalError.value = ''
  if (!rotateForm.api_key) { modalError.value = '请填写新 ApiKey'; return }
  saving.value = true
  try {
    const body = { api_key: rotateForm.api_key }
    const scopes = parseScopes(rotateForm.scopes_input)
    if (scopes.length) body.apikey_scopes = scopes
    await updateDownstreamApiKey(rotating.value.id, body)
    appStore.showToast('已更新')
    rotating.value = null
    load()
  } catch (e) {
    modalError.value = pickErrorDetail(e, '更新失败')
  } finally {
    saving.value = false
  }
}

async function onTest(d) {
  testing.value = d.id
  try {
    const data = await testDownstream(d.id)
    appStore.showToast(data.ok ? '连接通过' : '连接失败', data.ok ? 'success' : 'error')
  } catch (e) {
    appStore.showToast(pickErrorDetail(e, '测试失败'), 'error')
  } finally {
    testing.value = null
  }
}

async function onDisable(d) {
  if (!confirm(`确认停用「${d.name}」吗？`)) return
  try {
    await disableDownstream(d.id)
    appStore.showToast('已停用')
    load()
  } catch (e) {
    appStore.showToast(pickErrorDetail(e, '停用失败'), 'error')
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
.hub-toolbar { display: flex; align-items: center; gap: 8px; }
.hub-toolbar__spacer { flex: 1; }
.hub-form { display: flex; flex-direction: column; gap: 12px; }
.hub-form__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
</style>
