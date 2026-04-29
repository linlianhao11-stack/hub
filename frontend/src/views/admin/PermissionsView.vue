<template>
  <div class="hub-page">
    <h1 class="hub-page__title">权限说明</h1>
    <p class="hub-page__hint">系统全部权限定义。仅展示，无法修改。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="暂无权限">
        <template #header>
          <tr>
            <th class="app-th">权限名</th>
            <th class="app-th">说明</th>
            <th class="app-th">资源</th>
            <th class="app-th">动作</th>
          </tr>
        </template>
        <tr v-for="p in items" :key="p.code">
          <td class="app-td font-medium">{{ p.name }}</td>
          <td class="app-td text-muted">{{ p.description || '—' }}</td>
          <td class="app-td">{{ resourceLabel(p) }}</td>
          <td class="app-td">{{ actionLabel(p.action) }}</td>
        </tr>
      </AppTable>
    </AppCard>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listPermissions } from '../../api/permissions'
import { pickErrorDetail } from '../../api'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'

const items = ref([])
const error = ref('')

const RES_LABEL = {
  platform: '平台',
  finance: '财务',
  sales: '销售',
  product: '商品',
  customer: '客户',
}
const SUB_LABEL = {
  users: '用户',
  apikeys: 'ApiKey',
  audit: '审计',
  flags: '系统配置',
  tasks: '任务',
}
const ACTION_LABEL = {
  read: '查看',
  write: '修改',
  approve: '审批',
}

function resourceLabel(p) {
  const r = RES_LABEL[p.resource] || p.resource
  if (!p.sub_resource) return r
  const s = SUB_LABEL[p.sub_resource] || p.sub_resource
  return `${r} / ${s}`
}
function actionLabel(a) {
  return ACTION_LABEL[a] || a
}

async function load() {
  try {
    const data = await listPermissions()
    items.value = data.items || []
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
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
</style>
