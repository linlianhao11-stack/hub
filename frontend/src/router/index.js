import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { getStatus } from '../api/setup'

const routes = [
  {
    path: '/',
    name: 'root',
    component: () => import('../views/RootRedirect.vue'),
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
  },
  {
    path: '/setup/:step?',
    name: 'setup',
    component: () => import('../views/setup/SetupWizard.vue'),
  },
  {
    path: '/admin',
    component: () => import('../views/admin/AdminLayout.vue'),
    beforeEnter: async (to, from, next) => {
      const auth = useAuthStore()
      const ok = await auth.fetchMe()
      if (!ok) return next('/login')
      return next()
    },
    children: [
      { path: '', name: 'dashboard', component: () => import('../views/admin/DashboardView.vue') },
      { path: 'users', name: 'users', component: () => import('../views/admin/UsersView.vue') },
      { path: 'roles', name: 'roles', component: () => import('../views/admin/RolesView.vue') },
      { path: 'user-roles', name: 'user-roles', component: () => import('../views/admin/UserRolesView.vue') },
      { path: 'account-links', name: 'account-links', component: () => import('../views/admin/AccountLinksView.vue') },
      { path: 'permissions', name: 'permissions', component: () => import('../views/admin/PermissionsView.vue') },
      { path: 'downstreams', name: 'downstreams', component: () => import('../views/admin/DownstreamsView.vue') },
      { path: 'channels', name: 'channels', component: () => import('../views/admin/ChannelsView.vue') },
      { path: 'ai', name: 'ai', component: () => import('../views/admin/AIProvidersView.vue') },
      { path: 'config', name: 'config', component: () => import('../views/admin/SystemConfigView.vue') },
      { path: 'tasks', name: 'tasks', component: () => import('../views/admin/TasksView.vue') },
      { path: 'tasks/:taskId', name: 'task-detail', component: () => import('../views/admin/TaskDetailView.vue') },
      { path: 'conversation/live', name: 'conv-live', component: () => import('../views/admin/ConversationLiveView.vue') },
      { path: 'conversation/history', name: 'conv-history', component: () => import('../views/admin/ConversationHistoryView.vue') },
      { path: 'audit', name: 'audit', component: () => import('../views/admin/AuditView.vue') },
      { path: 'health', name: 'health', component: () => import('../views/admin/HealthView.vue') },
      { path: 'contract-templates', name: 'contract-templates', component: () => import('../views/admin/ContractTemplatesView.vue') },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

/**
 * 全局守卫只负责把用户从 / 入口送到正确位置：
 * - system_initialized=false → /setup
 * - 否则 → /admin（再由 beforeEnter 走 /me，未登录跳 /login）
 */
router.beforeEach(async (to) => {
  if (to.name === 'root') {
    try {
      const status = await getStatus()
      return status.initialized ? '/admin' : '/setup'
    } catch (e) {
      return '/login'
    }
  }
  return true
})

export default router
