<template>
  <!-- docx 可视化预览（mammoth.js 转 HTML，占位符自动高亮）-->
  <AppModal
    :visible="showModal"
    :title="`「${template?.name || ''}」模板预览`"
    size="lg"
    @update:visible="(v) => { if (!v) emit('close') }"
  >
    <div class="preview-toolbar">
      <span class="preview-stat">
        识别到 <strong>{{ currentPlaceholders.length }}</strong> 个占位符
        <span v-if="currentPlaceholders.length" class="preview-stat-note">— 文档里黄底高亮的就是占位符位置</span>
      </span>
      <span v-if="previewError" class="preview-error">{{ previewError }}</span>
    </div>
    <div v-if="previewLoading" class="preview-loading">加载中…</div>
    <div
      v-else-if="previewHtml"
      class="preview-content"
      v-html="sanitizedPreviewHtml"
    ></div>
    <div v-else class="preview-empty">没有内容</div>
    <template #footer>
      <AppButton variant="secondary" size="sm" @click="emit('close')">关闭</AppButton>
    </template>
  </AppModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { contractTemplatesApi } from '../../../api/contract_templates'
import { pickErrorDetail } from '../../../api'
import AppModal from '../../../components/ui/AppModal.vue'
import AppButton from '../../../components/ui/AppButton.vue'

const props = defineProps({
  showModal: Boolean,
  template: Object,
})

const emit = defineEmits(['close'])

const previewHtml = ref('')
const previewLoading = ref(false)
const previewError = ref('')

const currentPlaceholders = computed(() => props.template?.placeholders || [])

// DOMPurify 过滤 mammoth 输出，防止恶意 docx 注入 HTML/JS。
// title/data-name 必须保留 — 占位符高亮 span 需要 title 显示原代码,data-name 给 JS 反查。
const sanitizedPreviewHtml = computed(() => {
  if (!previewHtml.value) return ''
  return DOMPurify.sanitize(previewHtml.value, {
    ALLOWED_TAGS: ['p', 'br', 'b', 'i', 'u', 'strong', 'em', 'span', 'table', 'tr', 'td', 'th', 'thead', 'tbody', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a', 'div', 'mark'],
    ALLOWED_ATTR: ['class', 'style', 'href', 'target', 'title', 'data-name'],
  })
})

/** 把渲染后的 HTML 中所有 `{{xxx}}` 文本节点替换为高亮 span。
 *  span 显示**中文 label**（admin 起的中文显示名）,鼠标 hover 在 title 里显示原代码。
 *  `labels` 是 `{name → label}` 查表,缺则回退到代码 `{{name}}`。
 */
function _highlightPlaceholders(html, labels = {}) {
  return html.replace(
    /\{\{([\w一-龥]+)\}\}/g,
    (match, name) => {
      const label = labels[name] || name  // 没 label 用 name
      // 显示中文 label（不带 {{ }}）;hover 显示代码,方便 admin 核对
      return `<span class="ph-highlight" data-name="${name}" title="占位符代码: ${match}">${label}</span>`
    },
  )
}

watch(() => props.showModal, async (val) => {
  if (val && props.template) {
    previewError.value = ''
    previewHtml.value = ''
    previewLoading.value = true
    try {
      // 拿 docx 字节流
      const resp = await contractTemplatesApi.getFile(props.template.id)
      const arrayBuffer = resp.data  // axios responseType=arraybuffer 直接给 ArrayBuffer
      // 懒加载 mammoth（避免初始 bundle 加 ~200KB）
      const mammoth = (await import('mammoth')).default || (await import('mammoth'))
      const result = await mammoth.convertToHtml({ arrayBuffer })
      // 构造 name→label 查表,占位符高亮显示中文 label 而不是代码
      const labels = {}
      for (const ph of currentPlaceholders.value) {
        if (ph.name) labels[ph.name] = ph.label || ph.name
      }
      previewHtml.value = _highlightPlaceholders(result.value || '', labels)
      if (result.messages?.length) {
        console.warn('mammoth 转换警告:', result.messages)
      }
    } catch (e) {
      previewError.value = pickErrorDetail(e, '加载预览失败')
    } finally {
      previewLoading.value = false
    }
  } else if (!val) {
    previewHtml.value = ''
    previewError.value = ''
  }
})
</script>

<style scoped>
/* docx 可视化预览 */
.preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  margin-bottom: 12px;
  background: var(--bg-secondary, #f7f7f8);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
  font-size: 12px;
}
.preview-stat strong { color: var(--brand, #4a5568); font-size: 14px; }
.preview-stat-note { color: var(--text-muted); margin-left: 6px; }
.preview-error { color: var(--error); }
.preview-loading {
  padding: 40px;
  text-align: center;
  color: var(--text-muted);
}
.preview-empty {
  padding: 40px;
  text-align: center;
  color: var(--text-muted);
}
.preview-content {
  max-height: 65vh;
  overflow-y: auto;
  padding: 24px 32px;
  background: white;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
  font-family: var(--font-primary);
  font-size: 14px;
  line-height: 1.7;
  color: #111;
}
/* mammoth 输出的元素基本样式（保留 docx 大致排版感）*/
.preview-content :deep(h1) { font-size: 20px; font-weight: 600; margin: 16px 0 12px; }
.preview-content :deep(h2) { font-size: 17px; font-weight: 600; margin: 14px 0 10px; }
.preview-content :deep(h3) { font-size: 15px; font-weight: 600; margin: 12px 0 8px; }
.preview-content :deep(p) { margin: 8px 0; }
.preview-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
  font-size: 13px;
}
.preview-content :deep(td),
.preview-content :deep(th) {
  border: 1px solid #ccc;
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
}
.preview-content :deep(th) { background: #f3f4f6; font-weight: 600; }
.preview-content :deep(strong) { font-weight: 600; }
.preview-content :deep(em) { font-style: italic; }

/* 占位符高亮：黄底圆角,鼠标悬停 cursor 变 help */
.preview-content :deep(.ph-highlight) {
  background: #fef3c7;
  color: #92400e;
  padding: 1px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 0.95em;
  cursor: help;
  border: 1px solid #fde68a;
}
.preview-content :deep(.ph-highlight:hover) {
  background: #fde68a;
}
</style>
