<template>
  <!-- 上传模板弹窗 -->
  <AppModal
    :visible="showUpload"
    title="上传合同模板"
    size="md"
    @update:visible="(v) => { if (!v) emit('close') }"
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
          ref="fileInput"
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
      <AppButton variant="secondary" size="sm" @click="emit('close')">取消</AppButton>
      <AppButton variant="primary" size="sm" :loading="uploading" type="submit" form="upload-form">
        {{ uploading ? '上传中…' : '上传' }}
      </AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import { ref, useTemplateRef, watch } from 'vue'
import { contractTemplatesApi } from '../../../api/contract_templates'
import { pickErrorDetail } from '../../../api'
import AppModal from '../../../components/ui/AppModal.vue'
import AppButton from '../../../components/ui/AppButton.vue'
import AppInput from '../../../components/ui/AppInput.vue'
import AppSelect from '../../../components/ui/AppSelect.vue'
import AppTextarea from '../../../components/ui/AppTextarea.vue'

const props = defineProps({
  showUpload: Boolean,
})

const emit = defineEmits(['close', 'uploaded'])

const uploading = ref(false)
const uploadError = ref('')
const uploadForm = ref({ name: '', template_type: 'sales', description: '', file: null })
const fileInput = useTemplateRef('fileInput')

const typeOptionsRequired = [
  { value: 'sales', label: '销售合同' },
  { value: 'purchase', label: '采购合同' },
  { value: 'framework', label: '框架协议' },
  { value: 'quote', label: '报价单' },
  { value: 'other', label: '其他' },
]

watch(() => props.showUpload, (val) => {
  if (val) {
    uploadForm.value = { name: '', template_type: 'sales', description: '', file: null }
    uploadError.value = ''
    if (fileInput.value) fileInput.value.value = ''
  }
})

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
    emit('uploaded')
  } catch (e) {
    uploadError.value = pickErrorDetail(e, '上传失败，请检查文件格式')
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
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
</style>
