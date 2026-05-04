import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '../api'

/**
 * HUB 后台鉴权 store：
 * - me() 用 cookie 校验当前用户 + 拉取 permissions
 * - login() / logout() 转发到 /admin/login / /admin/logout
 */
export const useAuthStore = defineStore('hub-auth', () => {
  const erpUser = ref(null)
  const hubUserId = ref(null)
  const permissions = ref([])
  const meChecked = ref(false)

  const isLoggedIn = computed(() => !!erpUser.value)

  async function fetchMe() {
    try {
      const { data } = await api.get('/admin/me')
      erpUser.value = data.erp_user
      hubUserId.value = data.hub_user_id
      permissions.value = data.permissions || []
      return true
    } catch (e) {
      erpUser.value = null
      hubUserId.value = null
      permissions.value = []
      return false
    } finally {
      meChecked.value = true
    }
  }

  async function login(username, password) {
    await api.post('/admin/login', { username, password })
    await fetchMe()
  }

  async function logout() {
    try {
      await api.post('/admin/logout')
    } catch (e) {
      // ignore
    }
    erpUser.value = null
    hubUserId.value = null
    permissions.value = []
  }

  function hasPerm(code) {
    if (!code) return true
    // 支持 | OR 语法：code 含 | 时，任一权限满足即返回 true
    if (code.includes('|')) {
      return code.split('|').some((c) => permissions.value.includes(c.trim()))
    }
    return permissions.value.includes(code)
  }

  return { erpUser, hubUserId, permissions, meChecked, isLoggedIn, fetchMe, login, logout, hasPerm }
})
