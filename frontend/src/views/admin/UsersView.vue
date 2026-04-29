<template>
  <div class="hub-page">
    <h1 class="hub-page__title">HUB 用户列表</h1>

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
            <th class="app-th">创建时间</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="u in items" :key="u.id">
          <td class="app-td std-num">{{ u.id }}</td>
          <td class="app-td font-medium">{{ u.display_name }}</td>
          <td class="app-td"><AppBadge :variant="statusVariant(u.status)">{{ statusLabel(u.status) }}</AppBadge></td>
          <td class="app-td text-muted">{{ fmtDateTime(u.created_at) }}</td>
          <td class="app-td text-right">
            <AppButton variant="primary" size="xs" @click="openDetail(u)">详情</AppButton>
          </td>
        </tr>
        <template #footer>
          <span class="app-footer-stats">共 {{ total }} 条</span>
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
      :visible="!!current"
      :title="current ? `用户详情：${current.display_name}` : ''"
      size="lg"
      @update:visible="(v) => { if (!v) current = null }"
    >
      <div v-if="detailLoading" class="hub-page__hint">加载中…</div>
      <div v-else-if="detail" class="hub-detail">
        <section class="hub-detail__section">
          <h3 class="hub-detail__title">基本信息</h3>
          <div class="hub-detail__rows">
            <div><span>ID</span><strong>{{ detail.id }}</strong></div>
            <div><span>显示名</span><strong>{{ detail.display_name }}</strong></div>
            <div><span>状态</span><strong><AppBadge :variant="statusVariant(detail.status)">{{ statusLabel(detail.status) }}</AppBadge></strong></div>
          </div>
        </section>

        <section class="hub-detail__section">
          <h3 class="hub-detail__title">渠道绑定</h3>
          <AppTable :card="false">
            <template #header>
              <tr><th class="app-th">渠道</th><th class="app-th">渠道用户</th><th class="app-th">状态</th><th class="app-th">绑定时间</th><th class="app-th">解绑原因</th></tr>
            </template>
            <tr v-for="(b, i) in detail.channel_bindings" :key="i">
              <td class="app-td">{{ channelLabel(b.channel_type) }}</td>
              <td class="app-td std-num">{{ b.channel_userid }}</td>
              <td class="app-td"><AppBadge :variant="statusVariant(b.status)">{{ statusLabel(b.status) }}</AppBadge></td>
              <td class="app-td text-muted">{{ fmtDateTime(b.bound_at) }}</td>
              <td class="app-td text-muted">{{ b.revoked_reason || '-' }}</td>
            </tr>
            <tr v-if="!detail.channel_bindings.length"><td colspan="5" class="app-td text-muted">暂无</td></tr>
          </AppTable>
        </section>

        <section class="hub-detail__section">
          <h3 class="hub-detail__title">下游账号</h3>
          <AppTable :card="false">
            <template #header><tr><th class="app-th">下游</th><th class="app-th">下游用户 ID</th></tr></template>
            <tr v-for="(d, i) in detail.downstream_identities" :key="i">
              <td class="app-td">{{ downstreamLabel(d.downstream_type) }}</td>
              <td class="app-td std-num">{{ d.downstream_user_id }}</td>
            </tr>
            <tr v-if="!detail.downstream_identities.length"><td colspan="2" class="app-td text-muted">暂无</td></tr>
          </AppTable>
        </section>

        <section class="hub-detail__section">
          <h3 class="hub-detail__title">角色</h3>
          <div class="hub-detail__chips">
            <AppBadge v-for="r in detail.roles" :key="r.id" variant="info">{{ r.name }}</AppBadge>
            <span v-if="!detail.roles.length" class="text-muted">未分配角色</span>
          </div>
        </section>
      </div>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="current = null">关闭</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { onMounted, ref, computed } from 'vue'
import { listHubUsers, getHubUser } from '../../api/users'
import { pickErrorDetail } from '../../api'
import {
  fmtDateTime, statusLabel, statusVariant, channelLabel, downstreamLabel,
} from '../../utils/format'
import { usePagination } from '../../composables/usePagination'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppPagination from '../../components/ui/AppPagination.vue'

const error = ref('')
const items = ref([])
const totalCount = ref(0)
const keyword = ref('')

const { page, pageSize, totalPages, visiblePages, reset } = usePagination({
  total: () => totalCount.value,
  pageSize: 20,
})
const total = computed(() => totalCount.value)

async function load() {
  try {
    const data = await listHubUsers({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value || undefined,
    })
    items.value = data.items || []
    totalCount.value = data.total || 0
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function onPageChange(p) {
  page.value = p
  load()
}

function onSearch() {
  reset()
  load()
}

const current = ref(null)
const detail = ref(null)
const detailLoading = ref(false)

async function openDetail(u) {
  current.value = u
  detail.value = null
  detailLoading.value = true
  try {
    detail.value = await getHubUser(u.id)
  } catch (e) {
    error.value = pickErrorDetail(e, '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-page__hint { color: var(--text-muted); font-size: 13px; padding: 24px; text-align: center; }
.hub-toolbar { display: flex; align-items: center; gap: 8px; }
.hub-detail { display: flex; flex-direction: column; gap: 16px; }
.hub-detail__section { display: flex; flex-direction: column; gap: 8px; }
.hub-detail__title { font-size: 13px; font-weight: 600; color: var(--text); margin: 0; }
.hub-detail__rows { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 16px; font-size: 13px; }
.hub-detail__rows > div { display: flex; gap: 6px; align-items: center; }
.hub-detail__rows > div span { color: var(--text-muted); min-width: 64px; }
.hub-detail__chips { display: flex; flex-wrap: wrap; gap: 6px; }
</style>
