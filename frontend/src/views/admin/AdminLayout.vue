<template>
  <div class="hub-admin">
    <aside class="hub-admin__sidebar">
      <div class="hub-admin__brand">
        <div class="hub-admin__logo">H</div>
        <div>
          <div class="hub-admin__brand-title">HUB 后台</div>
          <div class="hub-admin__brand-sub">v0.1</div>
        </div>
      </div>

      <nav class="hub-admin__nav">
        <template v-for="group in groups" :key="group.title">
          <div class="hub-admin__nav-group" v-if="visibleItems(group).length">
            <div class="hub-admin__nav-title">{{ group.title }}</div>
            <ul>
              <li v-for="item in visibleItems(group)" :key="item.to">
                <router-link :to="item.to" :class="{ 'is-active': isActive(item.to) }">
                  <component :is="item.icon" :size="15" :stroke-width="1.6" />
                  <span>{{ item.label }}</span>
                </router-link>
              </li>
            </ul>
          </div>
        </template>
      </nav>
    </aside>

    <div class="hub-admin__main">
      <header class="hub-admin__topbar">
        <div class="hub-admin__crumbs">{{ pageTitle }}</div>
        <div class="hub-admin__user">
          <button class="hub-admin__theme-btn" @click="toggleTheme" :title="theme === 'dark' ? '切换到浅色' : '切换到深色'">
            <component :is="theme === 'dark' ? Sun : Moon" :size="14" :stroke-width="1.8" />
          </button>
          <span class="hub-admin__username">{{ auth.erpUser?.display_name || auth.erpUser?.username || '未登录' }}</span>
          <AppButton variant="ghost" size="sm" @click="onLogout">注销</AppButton>
        </div>
      </header>

      <main class="hub-admin__content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  LayoutDashboard, Users, Shield, KeyRound, Network,
  MessageSquare, Brain, Settings, Activity, ListChecks,
  History, ClipboardList, HeartPulse, ChevronRight, Sun, Moon,
  FileText,
} from 'lucide-vue-next'
import { useAuthStore } from '../../stores/auth'
import { useAppStore } from '../../stores/app'
import AppButton from '../../components/ui/AppButton.vue'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const appStore = useAppStore()

const theme = ref(localStorage.getItem('hub-theme') || 'light')

onMounted(() => {
  document.documentElement.dataset.theme = theme.value
})

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  appStore.setTheme(theme.value)
}

const groups = [
  {
    title: '总览',
    items: [
      { to: '/admin', label: '仪表盘', icon: LayoutDashboard, perm: null, exact: true },
      { to: '/admin/health', label: '健康巡检', icon: HeartPulse, perm: null },
    ],
  },
  {
    title: '用户与权限',
    items: [
      { to: '/admin/users', label: '用户列表', icon: Users, perm: 'platform.users.write' },
      { to: '/admin/roles', label: '角色与权限', icon: Shield, perm: 'platform.users.write' },
      { to: '/admin/user-roles', label: '用户角色分配', icon: ClipboardList, perm: 'platform.users.write' },
      { to: '/admin/account-links', label: 'ERP 账号关联', icon: KeyRound, perm: 'platform.users.write' },
      { to: '/admin/permissions', label: '权限说明', icon: Shield, perm: 'platform.users.write' },
    ],
  },
  {
    title: '集成',
    items: [
      { to: '/admin/downstreams', label: '下游系统', icon: Network, perm: 'platform.apikeys.write' },
      { to: '/admin/channels', label: '渠道应用', icon: MessageSquare, perm: 'platform.apikeys.write' },
      { to: '/admin/ai', label: 'AI 提供商', icon: Brain, perm: 'platform.apikeys.write' },
      { to: '/admin/config', label: '系统配置', icon: Settings, perm: 'platform.flags.write' },
    ],
  },
  {
    title: '业务配置',
    items: [
      { to: '/admin/contract-templates', label: '合同模板', icon: FileText, perm: 'usecase.contract_templates.write' },
    ],
  },
  {
    title: '运行时',
    items: [
      { to: '/admin/tasks', label: '任务列表', icon: ListChecks, perm: 'platform.tasks.read' },
      // ↓ 后端 /admin/conversation/live + /history 要 platform.conversation.monitor，
      //   之前误用 tasks.read 会让 platform_ops/viewer 看到入口但点了 403
      { to: '/admin/conversation/live', label: '实时会话', icon: Activity, perm: 'platform.conversation.monitor' },
      { to: '/admin/conversation/history', label: '历史会话', icon: History, perm: 'platform.conversation.monitor' },
      { to: '/admin/audit', label: '审计日志', icon: ClipboardList, perm: 'platform.audit.read' },
    ],
  },
]

function visibleItems(group) {
  return group.items.filter((it) => !it.perm || auth.hasPerm(it.perm))
}

function isActive(to) {
  if (to === '/admin') return route.path === '/admin' || route.path === '/admin/'
  return route.path === to || route.path.startsWith(to + '/')
}

const pageTitle = computed(() => {
  for (const g of groups) {
    for (const item of g.items) {
      if (isActive(item.to)) return item.label
    }
  }
  return ''
})

async function onLogout() {
  await auth.logout()
  router.replace('/login')
}
</script>

<style scoped>
.hub-admin {
  display: flex;
  min-height: 100vh;
  background: var(--background);
}
.hub-admin__sidebar {
  width: 240px;
  flex-shrink: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 18px 14px;
  gap: 18px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
.hub-admin__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 6px;
}
.hub-admin__logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--primary);
  color: var(--on-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}
.hub-admin__brand-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.01em;
}
.hub-admin__brand-sub {
  font-size: 11px;
  color: var(--text-muted);
}
.hub-admin__nav {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
}
.hub-admin__nav-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hub-admin__nav-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0 8px;
}
.hub-admin__nav ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.hub-admin__nav a {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  text-decoration: none;
  transition: background 120ms ease, color 120ms ease;
}
.hub-admin__nav a:hover {
  background: var(--elevated);
  color: var(--text);
}
.hub-admin__nav a.is-active {
  background: color-mix(in srgb, var(--primary) 12%, transparent);
  color: var(--primary);
  font-weight: 500;
}

.hub-admin__main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.hub-admin__topbar {
  height: 52px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}
.hub-admin__crumbs {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.hub-admin__user {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--text-secondary);
}
.hub-admin__username {
  color: var(--text);
  font-weight: 500;
}
.hub-admin__theme-btn {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.hub-admin__theme-btn:hover {
  background: var(--elevated);
  color: var(--text);
}
.hub-admin__content {
  flex: 1;
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 16px;
}
</style>
