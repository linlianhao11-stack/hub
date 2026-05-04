<template>
  <AppModal
    :visible="showRejectModal"
    title="批量拒绝"
    size="sm"
    @update:visible="(v) => { if (!v) emit('close') }"
  >
    <!-- M7: useId 生成 form id -->
    <form :id="rejectFormId" @submit.prevent="onSubmit" class="modal-form">
      <div class="form-field">
        <label class="form-label" :for="rejectFormId + '-reason'">
          拒绝原因 <span class="required">*</span>
        </label>
        <AppTextarea
          :id="rejectFormId + '-reason'"
          v-model="rejectReason"
          placeholder="请填写拒绝原因（最多 500 字）"
          :rows="3"
          maxlength="500"
          required
        />
      </div>
      <div v-if="rejectError" class="form-error">{{ rejectError }}</div>
    </form>
    <template #footer>
      <AppButton variant="secondary" size="sm" @click="emit('close')">取消</AppButton>
      <AppButton
        variant="danger"
        size="sm"
        :loading="rejecting"
        :disabled="!rejectReason.trim()"
        type="submit"
        :form="rejectFormId"
      >
        确认拒绝
      </AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import { ref, useId, watch } from 'vue'
import AppModal from '../../../components/ui/AppModal.vue'
import AppButton from '../../../components/ui/AppButton.vue'
import AppTextarea from '../../../components/ui/AppTextarea.vue'

defineProps({
  showRejectModal: { type: Boolean, required: true },
  selectedIds: { type: Array, required: true },
  currentTabLabel: { type: String, required: true },
  rejecting: { type: Boolean, required: true },
})

const emit = defineEmits(['close', 'submit'])

// M7: useId 生成稳定 form id
const rejectFormId = useId()
const rejectReason = ref('')
const rejectError = ref('')

watch(() => props.showRejectModal, (val) => {
  if (!val) {
    rejectReason.value = ''
    rejectError.value = ''
  }
})

function onSubmit() {
  if (!rejectReason.value.trim()) {
    rejectError.value = '请填写拒绝原因'
    return
  }
  rejectError.value = ''
  emit('submit', rejectReason.value)
}
</script>

<style scoped>
/* 拒绝弹窗表单 */
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.required {
  color: var(--error);
  margin-left: 2px;
}

.form-error {
  background: color-mix(in srgb, var(--error) 10%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
}
</style>
