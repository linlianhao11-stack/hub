<template>
  <div class="hub-page">
    <h1 class="hub-page__title">用户角色分配</h1>
    <p class="hub-page__hint">为 HUB 用户勾选角色。保存后立刻生效。</p>

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
            <AppButton variant="primary" size="xs" @click="openEdit(u)">分配角色</AppButton>
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
      :title="editing ? `分配角色：${editing.display_name}` : ''"
      size="md"
      @update:visible="(v) => { if (!v) editing = null }"
    >
      <div v-if="modalError" class="hub-page__error">{{ modalError }}</div>
      <div class="hub-roles-pick">
        <label v-for="r in roles" :key="r.id" class="hub-roles-pick__row">
          <AppCheckbox
            :model-value="selected.includes(r.id)"
            @update:model-value="toggleRole(r.id, $event)"
          />
          <div>
            <div class="hub-roles-pick__name">{{ r.name }}</div>
            <div class="hub-roles-pick__desc">{{ r.description || '—' }}</div>
          </div>
        </label>
        <div v-if="!roles.length" class="hub-page__hint">未拉取到角色</div>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="editing = null">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" @click="onSave">保存</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { listHubUsers, getHubUser, assignRoles } from '../../api/users'
import { listRoles } from '../../api/roles'
import { pickErrorDetail } from '../../api'
import { statusLabel, statusVariant } from '../../utils/format'
import { usePagination } from '../../composables/usePagination'
import { useAppStore } from '../../stores/app'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppCheckbox from '../../components/ui/AppCheckbox.vue'
import AppPagination from '../../components/ui/AppPagination.vue'

const appStore = useAppStore()
const items = ref([])
const totalCount = ref(0)
const keyword = ref('')
const roles = ref([])
const error = ref('')
const editing = ref(null)
const selected = ref([])
const saving = ref(false)
const modalError = ref('')

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

async function loadRoles() {
  try {
    const data = await listRoles()
    roles.value = data.items || []
  } catch (e) {
    // 静默失败，main load 接管错误显示
  }
}

function onSearch() {
  reset()
  load()
}

function onPageChange(p) {
  page.value = p
  load()
}

async function openEdit(u) {
  editing.value = u
  modalError.value = ''
  try {
    const detail = await getHubUser(u.id)
    selected.value = detail.roles.map((r) => r.id)
  } catch (e) {
    modalError.value = pickErrorDetail(e, '加载用户角色失败')
    selected.value = []
  }
}

function toggleRole(id, v) {
  if (v) {
    if (!selected.value.includes(id)) selected.value = [...selected.value, id]
  } else {
    selected.value = selected.value.filter((x) => x !== id)
  }
}

async function onSave() {
  if (!editing.value) return
  saving.value = true
  modalError.value = ''
  try {
    await assignRoles(editing.value.id, selected.value)
    appStore.showToast('已保存')
    editing.value = null
  } catch (e) {
    modalError.value = pickErrorDetail(e, '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  load()
  loadRoles()
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
.hub-toolbar { display: flex; align-items: center; gap: 8px; }
.hub-roles-pick { display: flex; flex-direction: column; gap: 8px; }
.hub-roles-pick__row {
  display: flex;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.hub-roles-pick__row:hover { background: var(--elevated); }
.hub-roles-pick__name { font-size: 13px; font-weight: 500; color: var(--text); }
.hub-roles-pick__desc { font-size: 12px; color: var(--text-muted); }
</style>
