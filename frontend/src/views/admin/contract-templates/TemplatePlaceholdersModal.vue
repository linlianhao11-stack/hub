<template>
  <!-- 占位符编辑弹窗（admin 给每个 {{xxx}} 起中文显示名）-->
  <AppModal
    :visible="showModal"
    :title="`「${template?.name || ''}」占位符设置`"
    size="lg"
    @update:visible="(v) => { if (!v) emit('close') }"
  >
    <div v-if="!editPlaceholders.length" class="text-muted text-sm">该模板未识别到占位符</div>
    <div v-else>
      <p class="form-hint" style="margin-bottom: 12px;">
        给每个占位符起一个中文显示名 — 用户在钉钉 / 预览页看到的是中文名,合同 docx 里仍然写 {{xxx}}。漏起名的会回退用代码。
      </p>
      <table class="ph-edit-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>中文显示名</th>
            <th>类型</th>
            <th>必填</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(ph, idx) in editPlaceholders" :key="ph.name">
            <td><code class="placeholder-code">{{ phLabel(ph.name) }}</code></td>
            <td>
              <AppInput v-model="editPlaceholders[idx].label" size="sm" placeholder="如：客户名" />
            </td>
            <td>
              <AppSelect v-model="editPlaceholders[idx].type" size="sm" :options="phTypeOptions" />
            </td>
            <td style="text-align: center;">
              <input type="checkbox" v-model="editPlaceholders[idx].required" />
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="phEditError" class="form-error" style="margin-top: 8px;">{{ phEditError }}</div>
    </div>
    <template #footer>
      <AppButton variant="secondary" size="sm" @click="emit('close')">取消</AppButton>
      <AppButton
        v-if="editPlaceholders.length"
        variant="primary"
        size="sm"
        :loading="phSaving"
        @click="handleSavePlaceholders"
      >保存</AppButton>
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

const props = defineProps({
  showModal: Boolean,
  template: Object,
})

const emit = defineEmits(['close', 'saved'])

const editPlaceholders = ref([])
const phSaving = ref(false)
const phEditError = ref('')
const phTypeOptions = [
  { value: 'string', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'date', label: '日期' },
  { value: 'money', label: '金额' },
  { value: 'phone', label: '电话' },
]

/** 将占位符名称格式化为 {{name}} 展示文本。 */
function phLabel(name) {
  const lb = '\x7B\x7B'  // {{
  const rb = '\x7D\x7D'  // }}
  return lb + name + rb
}

watch(() => props.showModal, (val) => {
  if (val && props.template) {
    // 深拷贝 — 编辑时不脏 list 数据,取消时直接关 modal 即可
    editPlaceholders.value = (props.template.placeholders || []).map((p) => ({
      name: p.name,
      label: p.label || p.name,  // 后端 _enrich_placeholders 应该已经填了,这里兜底
      type: p.type || 'string',
      required: p.required !== false,
    }))
    phEditError.value = ''
  }
})

async function handleSavePlaceholders() {
  if (!props.template) return
  phEditError.value = ''
  phSaving.value = true
  try {
    const res = await contractTemplatesApi.updatePlaceholders(
      props.template.id,
      editPlaceholders.value,
    )
    // 同步列表里这条记录的 placeholders
    const updated = res.data?.placeholders || editPlaceholders.value
    emit('saved', updated)
  } catch (e) {
    phEditError.value = pickErrorDetail(e, '保存失败')
  } finally {
    phSaving.value = false
  }
}
</script>

<style scoped>
.form-hint { font-size: 11px; color: var(--text-muted); margin: 4px 0 0; }
.form-error {
  background: color-mix(in srgb, var(--error) 10%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 25%, transparent);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 12px;
}
.placeholder-code {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 8%, transparent);
  padding: 2px 6px;
  border-radius: 4px;
}

/* 占位符编辑表格 */
.ph-edit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.ph-edit-table th {
  background: var(--bg-secondary, #f7f7f8);
  text-align: left;
  padding: 8px 10px;
  font-weight: 600;
  font-size: 12px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border, #e5e7eb);
}
.ph-edit-table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--border, #f0f0f0);
  vertical-align: middle;
}
.ph-edit-table tr:last-child td { border-bottom: none; }
</style>
