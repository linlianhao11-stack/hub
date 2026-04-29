<template>
  <div class="setup-wizard">
    <header class="setup-wizard__header">
      <div class="setup-wizard__brand">
        <div class="setup-wizard__logo">H</div>
        <div>
          <div class="setup-wizard__title">HUB 初始化向导</div>
          <div class="setup-wizard__subtitle">第一次启动需配置下游 + 钉钉 + AI</div>
        </div>
      </div>
      <ol class="setup-wizard__steps">
        <li
          v-for="s in stepsMeta"
          :key="s.idx"
          class="setup-wizard__step"
          :class="{
            'setup-wizard__step--active': currentIdx === s.idx,
            'setup-wizard__step--done': currentIdx > s.idx,
          }"
        >
          <span class="setup-wizard__step-num">{{ s.idx }}</span>
          <span class="setup-wizard__step-name">{{ s.name }}</span>
        </li>
      </ol>
    </header>

    <main class="setup-wizard__body">
      <Welcome v-if="currentIdx === 1" @next="goNext" />
      <ConnectErp v-else-if="currentIdx === 2" :session="session" @next="goNext" />
      <CreateAdmin v-else-if="currentIdx === 3" :session="session" @next="goNext" />
      <ConnectDingtalk v-else-if="currentIdx === 4" :session="session" @next="goNext" />
      <ConnectAi v-else-if="currentIdx === 5" :session="session" @next="goNext" @skip="goNext" />
      <Complete v-else-if="currentIdx === 6" :session="session" />
    </main>
  </div>
</template>

<script setup>
import { computed, ref, watchEffect, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Welcome from './steps/Welcome.vue'
import ConnectErp from './steps/ConnectErp.vue'
import CreateAdmin from './steps/CreateAdmin.vue'
import ConnectDingtalk from './steps/ConnectDingtalk.vue'
import ConnectAi from './steps/ConnectAi.vue'
import Complete from './steps/Complete.vue'

const router = useRouter()
const route = useRoute()

const stepsMeta = [
  { idx: 1, name: '系统自检' },
  { idx: 2, name: '连接 ERP' },
  { idx: 3, name: '创建管理员' },
  { idx: 4, name: '注册钉钉' },
  { idx: 5, name: '配置 AI' },
  { idx: 6, name: '完成' },
]

const currentIdx = computed(() => {
  const raw = Number(route.params.step || 1)
  return Math.max(1, Math.min(6, Number.isNaN(raw) ? 1 : raw))
})

// setup session：步骤 1.5 verify-token 之后由 Welcome 写入 sessionStorage
const session = ref(sessionStorage.getItem('hub_setup_session') || '')

watchEffect(() => {
  if (session.value) sessionStorage.setItem('hub_setup_session', session.value)
})

provide('hubSetupSession', { value: session, set: (v) => { session.value = v } })

function goNext() {
  const next = currentIdx.value + 1
  if (next > 6) {
    sessionStorage.removeItem('hub_setup_session')
    router.replace('/login')
    return
  }
  router.push(`/setup/${next}`)
}
</script>

<style scoped>
.setup-wizard {
  min-height: 100vh;
  background: var(--background);
  display: flex;
  flex-direction: column;
}
.setup-wizard__header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.setup-wizard__brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.setup-wizard__logo {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--primary);
  color: var(--on-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 16px;
}
.setup-wizard__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}
.setup-wizard__subtitle {
  font-size: 12px;
  color: var(--text-muted);
}
.setup-wizard__steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.setup-wizard__step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--elevated);
  border: 1px solid var(--border);
}
.setup-wizard__step-num {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: var(--surface);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 11px;
}
.setup-wizard__step--active {
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 10%, transparent);
  border-color: color-mix(in srgb, var(--primary) 40%, transparent);
}
.setup-wizard__step--active .setup-wizard__step-num {
  background: var(--primary);
  color: var(--on-primary);
  border-color: var(--primary);
}
.setup-wizard__step--done {
  color: var(--success);
}
.setup-wizard__step--done .setup-wizard__step-num {
  background: var(--success);
  color: #fff;
  border-color: var(--success);
}
.setup-wizard__body {
  flex: 1;
  padding: 24px;
  display: flex;
  justify-content: center;
}
</style>
