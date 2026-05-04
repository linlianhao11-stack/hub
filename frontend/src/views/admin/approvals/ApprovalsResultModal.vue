<template>
  <AppModal
    v-if="resultData"
    :visible="!!resultData"
    :title="resultTitle"
    size="md"
    @update:visible="(v) => { if (!v) emit('close') }"
  >
    <div class="result-sections">
      <div class="result-section result-section--success" v-if="(resultData.approved_count || 0) > 0">
        <h4 class="result-section__title result-section__title--success">
          已通过 {{ resultData.approved_count }} 条
        </h4>
      </div>
      <div class="result-section result-section--success" v-if="(resultData.rejected_count || 0) > 0">
        <h4 class="result-section__title result-section__title--success">
          已拒绝 {{ resultData.rejected_count }} 条
        </h4>
      </div>
      <div class="result-section" v-if="resultData.in_progress?.length">
        <h4 class="result-section__title result-section__title--warning">
          处理中 {{ resultData.in_progress.length }} 条（请稍后刷新）
        </h4>
        <ul class="result-list">
          <li v-for="item in resultData.in_progress" :key="item.draft_id">
            <span class="result-id">#{{ item.draft_id }}</span>{{ item.reason || '正在被另一会话处理' }}
          </li>
        </ul>
      </div>
      <div class="result-section" v-if="resultData.creation_failed?.length">
        <h4 class="result-section__title result-section__title--error">
          创建失败 {{ resultData.creation_failed.length }} 条
        </h4>
        <ul class="result-list">
          <li v-for="item in resultData.creation_failed" :key="item.draft_id">
            <span class="result-id">#{{ item.draft_id }}</span>{{ item.reason || '创建失败' }}
          </li>
        </ul>
      </div>
      <div class="result-section" v-if="resultData.approve_failed?.length">
        <h4 class="result-section__title result-section__title--error">
          审批失败 {{ resultData.approve_failed.length }} 条
        </h4>
        <ul class="result-list">
          <li v-for="item in resultData.approve_failed" :key="item.draft_id">
            <span class="result-id">#{{ item.draft_id }}</span>{{ item.reason || '审批失败' }}
          </li>
        </ul>
      </div>
      <!-- price / stock 通用 failed 字段 -->
      <div class="result-section" v-if="resultData.failed?.length">
        <h4 class="result-section__title result-section__title--error">
          失败 {{ resultData.failed.length }} 条
        </h4>
        <ul class="result-list">
          <li v-for="item in resultData.failed" :key="item.request_id ?? item.draft_id">
            <span class="result-id">#{{ item.request_id ?? item.draft_id }}</span>{{ item.reason || '处理失败' }}
          </li>
        </ul>
      </div>
    </div>
    <template #footer>
      <AppButton variant="primary" size="sm" @click="emit('close')">知道了</AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import AppModal from '../../../components/ui/AppModal.vue'
import AppButton from '../../../components/ui/AppButton.vue'

defineProps({
  resultData: { type: Object, default: null },
  resultTitle: { type: String, required: true },
})

const emit = defineEmits(['close'])
</script>

<style scoped>
/* I3: 结果 modal */
.result-sections {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-section {
  border-radius: 6px;
  padding: 10px 12px;
  background: var(--elevated);
}

.result-section__title {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 6px;
}

.result-section__title--success { color: var(--success-emphasis); }
.result-section__title--warning { color: var(--warning-emphasis); }
.result-section__title--error   { color: var(--error); }

.result-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.result-list li {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.result-id {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  color: var(--text-muted);
  margin-right: 6px;
}
</style>
