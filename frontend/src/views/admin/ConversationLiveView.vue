<template>
  <div class="hub-page">
    <div class="hub-page__head">
      <h1 class="hub-page__title">实时会话</h1>
      <div class="hub-conv__status">
        <span class="hub-conv__dot" :class="{ 'is-on': connected, 'is-off': !connected }"></span>
        <span class="hub-conv__label">{{ connected ? '已连接' : '未连接' }}</span>
        <AppButton v-if="!connected" variant="primary" size="xs" @click="connect">连接</AppButton>
        <AppButton v-else variant="secondary" size="xs" @click="disconnect">断开</AppButton>
      </div>
    </div>
    <p class="hub-page__hint">敏感字段已在后端做脱敏；会话上限保留最近 200 条，超出自动丢弃。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <ul class="hub-conv__list">
      <li v-for="(ev, i) in events" :key="i" class="hub-conv__row">
        <div class="hub-conv__row-head">
          <span class="hub-conv__row-time">{{ fmtTime(ev.received_at) }}</span>
          <AppBadge :variant="ev.kind === 'request' ? 'info' : 'success'">{{ ev.kind === 'request' ? '入站' : '回复' }}</AppBadge>
          <span class="hub-conv__row-user std-num">{{ ev.channel_userid || '-' }}</span>
          <span v-if="ev.task_id" class="hub-conv__row-task std-num">{{ shortId(ev.task_id) }}</span>
        </div>
        <pre class="hub-conv__row-body">{{ ev.preview || ev.text || JSON.stringify(ev, null, 2) }}</pre>
      </li>
      <li v-if="!events.length" class="hub-page__hint">{{ connected ? '等待消息…' : '尚未连接' }}</li>
    </ul>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { openConversationLive } from '../../api/conversation'
import AppButton from '../../components/ui/AppButton.vue'
import AppBadge from '../../components/ui/AppBadge.vue'

const events = ref([])
const error = ref('')
const connected = ref(false)
let es = null

function fmtTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function shortId(id) {
  return id && id.length > 8 ? `${id.slice(0, 8)}…` : (id || '')
}

function connect() {
  if (es) return
  error.value = ''
  try {
    es = openConversationLive()
  } catch (e) {
    error.value = '浏览器不支持 EventSource'
    return
  }
  es.onopen = () => {
    connected.value = true
  }
  es.onmessage = (e) => {
    let parsed
    try {
      parsed = JSON.parse(e.data)
    } catch {
      parsed = { kind: 'raw', text: e.data, received_at: new Date().toISOString() }
    }
    if (!parsed.received_at) parsed.received_at = new Date().toISOString()
    events.value = [parsed, ...events.value].slice(0, 200)
  }
  es.onerror = () => {
    connected.value = false
    if (es) {
      es.close()
      es = null
    }
    error.value = 'SSE 连接断开。可点击「连接」重试。'
  }
}

function disconnect() {
  if (es) {
    es.close()
    es = null
  }
  connected.value = false
}

onMounted(connect)
onBeforeUnmount(disconnect)
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 16px; flex: 1; }
.hub-page__head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__hint { font-size: 12px; color: var(--text-muted); margin: 0; padding: 6px 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-conv__status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.hub-conv__dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--text-muted);
}
.hub-conv__dot.is-on { background: var(--success); box-shadow: 0 0 0 4px color-mix(in srgb, var(--success) 18%, transparent); }
.hub-conv__dot.is-off { background: var(--text-muted); }
.hub-conv__label { color: var(--text-muted); }

.hub-conv__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.hub-conv__row {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.hub-conv__row-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.hub-conv__row-time { font-family: var(--font-mono); color: var(--text-muted); }
.hub-conv__row-user { color: var(--text-secondary); }
.hub-conv__row-task { color: var(--text-muted); margin-left: auto; }
.hub-conv__row-body {
  background: var(--elevated);
  border-radius: 6px;
  padding: 8px 10px;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
