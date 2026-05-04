<!--
  Plan 6 Task 11 admin 合同模板管理页。

  弹窗已拆为子组件：
  - contract-templates/TemplateUploadModal.vue（上传逻辑 + 文件输入 + form）
  - contract-templates/TemplatePlaceholdersModal.vue（占位符列表展示）
  - contract-templates/TemplatePreviewModal.vue（docx 预览 + mammoth）
  - contract-templates/TemplateEditModal.vue（编辑元信息）
  保持本 view 仅做"列表 + 筛选 + 弹窗调度 + toggleActive"。
-->
<template>
  <div class="hub-page">
    <div class="hub-page__header">
      <div>
        <h1 class="hub-page__title">合同模板管理</h1>
        <p class="hub-page__hint">上传 .docx 模板，系统自动识别 <code v-pre class="hint-code">{{占位符}}</code>，机器人生成合同时自动填充。</p>
      </div>
      <AppButton variant="primary" size="sm" @click="showUpload = true">
        <Plus class="btn-icon" :size="14" /> 上传模板
      </AppButton>
    </div>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <!-- 筛选工具栏 -->
    <div class="hub-toolbar">
      <AppSelect
        v-model="filterType"
        size="toolbar"
        placeholder="所有类型"
        :options="typeOptions"
        @update:modelValue="load"
      />
      <AppSelect
        v-model="filterActive"
        size="toolbar"
        placeholder="所有状态"
        :options="activeOptions"
        @update:modelValue="load"
      />
      <AppButton variant="secondary" size="sm" @click="load">刷新</AppButton>
    </div>

    <!-- 模板列表 -->
    <AppCard padding="none">
      <AppTable :card="false" sticky :empty="!items.length" empty-text="还没有合同模板，点击「上传模板」开始">
        <template #header>
          <tr>
            <th class="app-th">ID</th>
            <th class="app-th">模板名称</th>
            <th class="app-th">类型</th>
            <th class="app-th">占位符数</th>
            <th class="app-th">状态</th>
            <th class="app-th">创建时间</th>
            <th class="app-th text-right">操作</th>
          </tr>
        </template>
        <tr v-for="tpl in items" :key="tpl.id">
          <td class="app-td std-num">{{ tpl.id }}</td>
          <td class="app-td font-medium">{{ tpl.name }}</td>
          <td class="app-td">
            <AppBadge variant="info" :label="typeLabel(tpl.template_type)" />
          </td>
          <td class="app-td std-num">{{ tpl.placeholders?.length || 0 }}</td>
          <td class="app-td">
            <AppBadge
              :variant="tpl.is_active ? 'success' : 'gray'"
              :label="tpl.is_active ? '启用' : '禁用'"
            />
          </td>
          <td class="app-td text-muted">{{ fmtDateTime(tpl.created_at) }}</td>
          <td class="app-td text-right">
            <div class="row-actions">
              <AppButton variant="ghost" size="xs" @click="openPreview(tpl)">预览</AppButton>
              <AppButton variant="ghost" size="xs" @click="openPlaceholders(tpl)">占位符</AppButton>
              <AppButton variant="ghost" size="xs" @click="openEdit(tpl)">编辑</AppButton>
              <AppButton
                variant="ghost"
                :class="tpl.is_active ? 'action-danger' : 'action-success'"
                size="xs"
                @click="toggleActive(tpl)"
              >
                {{ tpl.is_active ? '禁用' : '启用' }}
              </AppButton>
            </div>
          </td>
        </tr>
        <template #footer>
          <span class="app-footer-stats">共 {{ total }} 个模板</span>
        </template>
      </AppTable>
    </AppCard>

    <TemplateUploadModal :showUpload="showUpload" @close="showUpload = false" @uploaded="onUploaded" />
    <TemplatePlaceholdersModal :showModal="showPlaceholdersModal" :template="currentTpl" @close="showPlaceholdersModal = false" @saved="onPlaceholdersSaved" />
    <TemplatePreviewModal :showModal="showPreviewModal" :template="currentTpl" @close="showPreviewModal = false" />
    <TemplateEditModal :showModal="showEditModal" :template="editTemplate" @close="showEditModal = false" @saved="onEditSaved" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Plus } from 'lucide-vue-next'
import { contractTemplatesApi } from '../../api/contract_templates'
import { pickErrorDetail } from '../../api'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import TemplateUploadModal from './contract-templates/TemplateUploadModal.vue'
import TemplatePlaceholdersModal from './contract-templates/TemplatePlaceholdersModal.vue'
import TemplatePreviewModal from './contract-templates/TemplatePreviewModal.vue'
import TemplateEditModal from './contract-templates/TemplateEditModal.vue'

// 状态
const items = ref([])
const total = ref(0)
const error = ref('')
const filterType = ref('')
const filterActive = ref('')
const showUpload = ref(false)
const showPlaceholdersModal = ref(false)
const showPreviewModal = ref(false)
const showEditModal = ref(false)
const currentTpl = ref(null)
const editTemplate = ref(null)

// 选项数据
const typeOptions = [
  { value: '', label: '所有类型' },
  { value: 'sales', label: '销售合同' },
  { value: 'purchase', label: '采购合同' },
  { value: 'framework', label: '框架协议' },
  { value: 'quote', label: '报价单' },
  { value: 'other', label: '其他' },
]
const activeOptions = [
  { value: '', label: '所有状态' },
  { value: 'true', label: '启用' },
  { value: 'false', label: '禁用' },
]
const TYPE_LABEL_MAP = {
  sales: '销售合同', purchase: '采购合同', framework: '框架协议',
  quote: '报价单', other: '其他',
}
function typeLabel(t) { return TYPE_LABEL_MAP[t] || t }
function fmtDateTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// 数据加载
async function load() {
  error.value = ''
  try {
    const params = {}
    if (filterType.value) params.template_type = filterType.value
    if (filterActive.value !== '') params.is_active = filterActive.value === 'true'
    const { data } = await contractTemplatesApi.list(params)
    items.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败，请刷新重试')
  }
}

// 弹窗调度
function openPlaceholders(tpl) { currentTpl.value = tpl; showPlaceholdersModal.value = true }
function openPreview(tpl) { currentTpl.value = tpl; showPreviewModal.value = true }
function openEdit(tpl) { editTemplate.value = tpl; showEditModal.value = true }
async function onUploaded() { showUpload.value = false; await load() }
function onPlaceholdersSaved(updatedPlaceholders) {
  if (currentTpl.value) {
    const idx = items.value.findIndex((it) => it.id === currentTpl.value.id)
    if (idx >= 0) items.value[idx].placeholders = updatedPlaceholders
  }
  showPlaceholdersModal.value = false
}
async function onEditSaved() { showEditModal.value = false; await load() }

// 启用 / 禁用
async function toggleActive(tpl) {
  try {
    if (tpl.is_active) { await contractTemplatesApi.disable(tpl.id) } else { await contractTemplatesApi.enable(tpl.id) }
    await load()
  } catch (e) { error.value = pickErrorDetail(e, '操作失败') }
}

onMounted(load)
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__hint { font-size: 12px; color: var(--text-muted); margin: 4px 0 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.btn-icon { margin-right: 4px; }
.row-actions { display: flex; align-items: center; gap: 4px; justify-content: flex-end; }
.action-danger { color: var(--error) !important; }
.action-success { color: var(--success) !important; }
.hint-code {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  background: var(--elevated);
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--text-secondary);
}
</style>
