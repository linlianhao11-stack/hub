<template>
  <div class="hub-page">
    <h1 class="hub-page__title">ERP 账号关联</h1>
    <p class="hub-page__hint">把 HUB 用户和 ERP 系统中的真实账号绑定，绑定后该用户的 ERP 调用会用这个账号执行。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-toolbar">
      <AppInput
        v-model="keyword"
        size="toolbar"
        icon="search"
        placeholder="按显示名搜索…"
        @keyup.enter="onSearch"
      />
      <AppButton variant="secondary" size="sm" @click="onSearch">搜索</AppButton>
    </div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="暂无用户">
        <template #header>
          <tr>
            <th class="app-th">ID</th>
            <th class="app-th">显示名</th>
            <th class="app-th">状态</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="u in items" :key="u.id">
          <td class="app-td std-num">{{ u.id }}</td>
          <td class="app-td font-medium">{{ u.display_name }}</td>
          <td class="app-td"><AppBadge :variant="statusVariant(u.status)">{{ statusLabel(u.status) }}</AppBadge></td>
          <td class="app-td text-right">
            <AppButton variant="primary" size="xs" @click="openLink(u)">关联 ERP 账号</AppButton>
          </td>
        </tr>
        <template #footer>
          <span class="app-footer-stats">共 {{ totalCount }} 条</span>
          <AppPagination
            v-if="totalPages > 1"
            :page="page"
            :total-pages="totalPages"
            :visible-pages="visiblePages"
            @update:page="onPageChange"
          />
        </template>
      </AppTable>
    </AppCard>

    <AppModal
      :visible="!!editing"
      :title="editing ? `关联 ERP 账号：${editing.display_name}` : ''"
      size="sm"
      @update:visible="(v) => { if (!v) editing = null }"
    >
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <label class="hub-detail__field">
        <span>下游类型</span>
        <AppSelect v-model="form.downstream_type" :options="dsOptions" />
      </label>
      <label class="hub-detail__field">
        <span>ERP 用户 ID</span>
        <AppInput v-model.number="form.downstream_user_id" type="number" />
      </label>
      <p class="hub-page__hint">现有：{{ existingHint }}</p>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="editing = null">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onSave">保存</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { listHubUsers, getHubUser, updateDownstreamIdentity } from '../../api/users'
import { pickErrorDetail } from '../../api'
import { statusLabel, statusVariant, downstreamLabel } from '../../utils/format'
import { usePagination } from '../../composables/usePagination'
import { useAppStore } from '../../stores/app'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppPagination from '../../components/ui/AppPagination.vue'

const appStore = useAppStore()
const items = ref([])
const totalCount = ref(0)
const keyword = ref('')
const error = ref('')
const editing = ref(null)
const saving = ref(false)
const modalError = ref('')
const existingHint = ref('—')

const dsOptions = [
  { value: 'erp', label: 'ERP 系统' },
]

const form = reactive({ downstream_type: 'erp', downstream_user_id: null })

const { page, pageSize, totalPages, visiblePages, reset } = usePagination({
  total: () => totalCount.value,
  pageSize: 20,
})

async function load() {
  try {
    const data = await listHubUsers({
      page: page.value, page_size: pageSize.value, keyword: keyword.value || undefined,
    })
    items.value = data.items || []
    totalCount.value = data.total || 0
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function onSearch() { reset(); load() }
function onPageChange(p) { page.value = p; load() }

async function openLink(u) {
  editing.value = u
  modalError.value = ''
  form.downstream_type = 'erp'
  form.downstream_user_id = null
  existingHint.value = '加载中…'
  try {
    const detail = await getHubUser(u.id)
    const erp = detail.downstream_identities.find((d) => d.downstream_type === 'erp')
    existingHint.value = erp ? `已绑 ERP user_id = ${erp.downstream_user_id}` : '尚未关联'
    if (erp) form.downstream_user_id = erp.downstream_user_id
  } catch (e) {
    existingHint.value = '加载失败'
  }
}

async function onSave() {
  if (!editing.value) return
  if (!form.downstream_user_id || Number(form.downstream_user_id) <= 0) {
    modalError.value = '请填写有效的 ERP 用户 ID'
    return
  }
  saving.value = true
  modalError.value = ''
  try {
    await updateDownstreamIdentity(editing.value.id, {
      downstream_type: form.downstream_type,
      downstream_user_id: Number(form.downstream_user_id),
    })
    appStore.showToast('关联成功')
    editing.value = null
  } catch (e) {
    modalError.value = pickErrorDetail(e, '关联失败')
  } finally {
    saving.value = false
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
.hub-detail__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 12px;
}
</style>
