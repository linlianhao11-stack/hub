<template>
  <!-- 编辑元信息弹窗 -->
  <AppModal
    :visible="showModal"
    title="编辑模板信息"
    size="md"
    @update:visible="(v) => { if (!v) emit('close') }"
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
      <AppButton variant="secondary" size="sm" @click="emit('close')">取消</AppButton>
      <AppButton variant="primary" size="sm" :loading="saving" type="submit" form="edit-form">保存</AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import { ref, watch } from 'vue'
import { contractTemplatesApi } from '../../../api/contract_templates'
import { pickErrorDetail } from '../../../api'
import AppModal from '../../../components/ui/AppModal.vue'
import AppButton from '../../../components/ui/AppButton.vue'
import AppInput from '../../../components/ui/AppInput.vue'
import AppSelect from '../../../components/ui/AppSelect.vue'
import AppTextarea from '../../../components/ui/AppTextarea.vue'

const props = defineProps({
  showModal: Boolean,
  template: Object,
})

const emit = defineEmits(['close', 'saved'])

const saving = ref(false)
const editError = ref('')
const editForm = ref({ name: '', template_type: 'sales', description: '' })

const typeOptionsRequired = [
  { value: 'sales', label: '销售合同' },
  { value: 'purchase', label: '采购合同' },
  { value: 'framework', label: '框架协议' },
  { value: 'quote', label: '报价单' },
  { value: 'other', label: '其他' },
]

watch(() => props.showModal, (val) => {
  if (val && props.template) {
    editForm.value = {
      name: props.template.name,
      template_type: props.template.template_type,
      description: props.template.description || '',
    }
    editError.value = ''
  }
})

async function handleUpdate() {
  saving.value = true
  editError.value = ''
  try {
    await contractTemplatesApi.update(props.template.id, editForm.value)
    emit('saved')
  } catch (e) {
    editError.value = pickErrorDetail(e, '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
/* 弹窗表单 */
.modal-form { display: flex; flex-direction: column; gap: 14px; }
.form-field { display: flex; flex-direction: column; gap: 4px; }
.form-label { font-size: 13px; font-weight: 500; color: var(--text); }
.form-error {
  background: color-mix(in srgb, var(--error) 10%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
}
</style>
