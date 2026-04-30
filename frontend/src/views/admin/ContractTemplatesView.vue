<template>
  <div class="hub-page">
    <div class="hub-page__header">
      <div>
        <h1 class="hub-page__title">合同模板管理</h1>
        <p class="hub-page__hint">上传 .docx 模板，系统自动识别 <code v-pre class="hint-code">{{占位符}}</code>，机器人生成合同时自动填充。</p>
      </div>
      <AppButton variant="primary" size="sm" @click="openUpload">
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
              <AppButton variant="ghost" size="xs" @click="openPlaceholders(tpl)">占位符</AppButton>
              <AppButton variant="ghost" size="xs" @click="openEdit(tpl)">编辑</AppButton>
              <AppButton
                :variant="tpl.is_active ? 'ghost' : 'ghost'"
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

    <!-- 上传模板弹窗 -->
    <AppModal
      :visible="showUpload"
      title="上传合同模板"
      size="md"
      @update:visible="(v) => { if (!v) closeUpload() }"
    >
      <form id="upload-form" @submit.prevent="handleUpload" class="modal-form">
        <div class="form-field">
          <label class="form-label" for="upload-name">模板名称 <span class="required">*</span></label>
          <AppInput
            id="upload-name"
            v-model="uploadForm.name"
            placeholder="如：标准销售合同 v2"
            maxlength="200"
          />
        </div>

        <div class="form-field">
          <label class="form-label" for="upload-type">模板类型 <span class="required">*</span></label>
          <AppSelect
            id="upload-type"
            v-model="uploadForm.template_type"
            :options="typeOptionsRequired"
          />
        </div>

        <div class="form-field">
          <label class="form-label" for="upload-desc">描述（可选）</label>
          <AppTextarea
            id="upload-desc"
            v-model="uploadForm.description"
            placeholder="简要说明该模板适用场景…"
            :rows="3"
            maxlength="1000"
          />
        </div>

        <div class="form-field">
          <label class="form-label" for="upload-file">docx 文件 <span class="required">*</span></label>
          <input
            id="upload-file"
            type="file"
            accept=".docx"
            class="file-input"
            @change="onFileChange"
            required
          />
          <p class="form-hint">仅支持 .docx，最大 5MB；文件中用 <code v-pre class="hint-code">{{变量名}}</code> 标记占位符，上传后自动识别</p>
        </div>

        <div v-if="uploadError" class="form-error">{{ uploadError }}</div>
      </form>

      <template #footer>
        <AppButton variant="secondary" size="sm" @click="closeUpload">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="uploading" type="submit" form="upload-form">
          {{ uploading ? '上传中…' : '上传' }}
        </AppButton>
      </template>
    </AppModal>

    <!-- 占位符查看弹窗 -->
    <AppModal
      :visible="showPlaceholdersModal"
      :title="`「${currentTpl?.name || ''}」占位符列表`"
      size="sm"
      @update:visible="(v) => { if (!v) showPlaceholdersModal = false }"
    >
      <div v-if="!currentPlaceholders.length" class="text-muted text-sm">该模板未识别到占位符</div>
      <ul v-else class="placeholder-list">
        <li v-for="ph in currentPlaceholders" :key="ph.name" class="placeholder-item">
          <code class="placeholder-code">{{ phLabel(ph.name) }}</code>
          <span class="placeholder-meta">
            <span class="ph-type">{{ ph.type }}</span>
            <span v-if="ph.required" class="ph-required">必填</span>
          </span>
        </li>
      </ul>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="showPlaceholdersModal = false">关闭</AppButton>
      </template>
    </AppModal>

    <!-- 编辑元信息弹窗 -->
    <AppModal
      :visible="showEditModal"
      title="编辑模板信息"
      size="md"
      @update:visible="(v) => { if (!v) showEditModal = false }"
    >
      <form id="edit-form" @submit.prevent="handleUpdate" class="modal-form">
        <div class="form-field">
          <label class="form-label" for="edit-name">模板名称</label>
          <AppInput id="edit-name" v-model="editForm.name" maxlength="200" />
        </div>
        <div class="form-field">
          <label class="form-label" for="edit-type">模板类型</label>
          <AppSelect id="edit-type" v-model="editForm.template_type" :options="typeOptionsRequired" />
        </div>
        <div class="form-field">
          <label class="form-label" for="edit-desc">描述</label>
          <AppTextarea id="edit-desc" v-model="editForm.description" :rows="3" maxlength="1000" />
        </div>
        <div v-if="editError" class="form-error">{{ editError }}</div>
      </form>
      <template #footer>
        <AppButton variant="secondary" size="sm" @click="showEditModal = false">取消</AppButton>
        <AppButton variant="primary" size="sm" :loading="saving" type="submit" form="edit-form">保存</AppButton>
      </template>
    </AppModal>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Plus } from 'lucide-vue-next'
import { contractTemplatesApi } from '../../api/contract_templates'
import { pickErrorDetail } from '../../api'
import AppCard from '../../components/ui/AppCard.vue'
import AppTable from '../../components/common/AppTable.vue'
import AppModal from '../../components/ui/AppModal.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppInput from '../../components/ui/AppInput.vue'
import AppSelect from '../../components/ui/AppSelect.vue'
import AppTextarea from '../../components/ui/AppTextarea.vue'
import AppBadge from '../../components/ui/AppBadge.vue'

// ──────────────────────────────────────────────
// 状态
// ──────────────────────────────────────────────

const items = ref([])
const total = ref(0)
const error = ref('')
const filterType = ref('')
const filterActive = ref('')

// 上传弹窗
const showUpload = ref(false)
const uploading = ref(false)
const uploadError = ref('')
const uploadForm = ref({ name: '', template_type: 'sales', description: '', file: null })

// 占位符弹窗
const showPlaceholdersModal = ref(false)
const currentTpl = ref(null)
const currentPlaceholders = ref([])

// 编辑弹窗
const showEditModal = ref(false)
const saving = ref(false)
const editError = ref('')
const editingId = ref(null)
const editForm = ref({ name: '', template_type: 'sales', description: '' })

// ──────────────────────────────────────────────
// 选项数据
// ──────────────────────────────────────────────

const typeOptions = [
  { value: '', label: '所有类型' },
  { value: 'sales', label: '销售合同' },
  { value: 'purchase', label: '采购合同' },
  { value: 'framework', label: '框架协议' },
  { value: 'quote', label: '报价单' },
  { value: 'other', label: '其他' },
]

const typeOptionsRequired = typeOptions.filter((o) => o.value !== '')

const activeOptions = [
  { value: '', label: '所有状态' },
  { value: 'true', label: '启用' },
  { value: 'false', label: '禁用' },
]

const TYPE_LABEL_MAP = {
  sales: '销售合同', purchase: '采购合同', framework: '框架协议',
  quote: '报价单', other: '其他',
}

function typeLabel(t) {
  return TYPE_LABEL_MAP[t] || t
}

/** 将占位符名称格式化为 {{name}} 展示文本。 */
function phLabel(name) {
  const lb = '\x7B\x7B'  // {{
  const rb = '\x7D\x7D'  // }}
  return lb + name + rb
}

function fmtDateTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// ──────────────────────────────────────────────
// 数据加载
// ──────────────────────────────────────────────

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

// ──────────────────────────────────────────────
// 上传
// ──────────────────────────────────────────────

function openUpload() {
  uploadForm.value = { name: '', template_type: 'sales', description: '', file: null }
  uploadError.value = ''
  showUpload.value = true
}

function closeUpload() {
  showUpload.value = false
  uploadError.value = ''
}

function onFileChange(e) {
  uploadForm.value.file = e.target.files[0] || null
}

async function handleUpload() {
  if (!uploadForm.value.file) {
    uploadError.value = '请选择 .docx 文件'
    return
  }
  if (!uploadForm.value.name.trim()) {
    uploadError.value = '请填写模板名称'
    return
  }
  uploading.value = true
  uploadError.value = ''
  try {
    await contractTemplatesApi.upload(uploadForm.value)
    closeUpload()
    await load()
  } catch (e) {
    uploadError.value = pickErrorDetail(e, '上传失败，请检查文件格式')
  } finally {
    uploading.value = false
  }
}

// ──────────────────────────────────────────────
// 占位符查看
// ──────────────────────────────────────────────

function openPlaceholders(tpl) {
  currentTpl.value = tpl
  currentPlaceholders.value = tpl.placeholders || []
  showPlaceholdersModal.value = true
}

// ──────────────────────────────────────────────
// 编辑元信息
// ──────────────────────────────────────────────

function openEdit(tpl) {
  editingId.value = tpl.id
  editForm.value = {
    name: tpl.name,
    template_type: tpl.template_type,
    description: tpl.description || '',
  }
  editError.value = ''
  showEditModal.value = true
}

async function handleUpdate() {
  saving.value = true
  editError.value = ''
  try {
    await contractTemplatesApi.update(editingId.value, editForm.value)
    showEditModal.value = false
    await load()
  } catch (e) {
    editError.value = pickErrorDetail(e, '保存失败')
  } finally {
    saving.value = false
  }
}

// ──────────────────────────────────────────────
// 启用 / 禁用
// ──────────────────────────────────────────────

async function toggleActive(tpl) {
  try {
    if (tpl.is_active) {
      await contractTemplatesApi.disable(tpl.id)
    } else {
      await contractTemplatesApi.enable(tpl.id)
    }
    await load()
  } catch (e) {
    error.value = pickErrorDetail(e, '操作失败')
  }
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

/* 弹窗表单 */
.modal-form { display: flex; flex-direction: column; gap: 14px; }
.form-field { display: flex; flex-direction: column; gap: 4px; }
.form-label { font-size: 13px; font-weight: 500; color: var(--text); }
.required { color: var(--error); margin-left: 2px; }
.form-hint { font-size: 11px; color: var(--text-muted); margin: 4px 0 0; }
.form-error {
  background: color-mix(in srgb, var(--error) 10%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
}
.file-input {
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  padding: 4px 0;
}
.hint-code {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  background: var(--elevated);
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--text-secondary);
}

/* 占位符列表 */
.placeholder-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0; }
.placeholder-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 4px;
  border-bottom: 1px solid var(--border);
}
.placeholder-item:last-child { border-bottom: none; }
.placeholder-code {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  padding: 2px 6px;
  border-radius: 4px;
}
.placeholder-meta { display: flex; align-items: center; gap: 6px; }
.ph-type { font-size: 11px; color: var(--text-muted); }
.ph-required {
  font-size: 10px;
  background: color-mix(in srgb, var(--warning) 15%, transparent);
  color: var(--warning);
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 500;
}
</style>
