<template>
  <div class="hub-page">
    <h1 class="hub-page__title">角色与权限</h1>
    <p class="hub-page__hint">系统内置角色（C 阶段不支持自定义编辑），点击展开看权限列表。</p>

    <div v-if="error" class="hub-page__error">{{ error }}</div>

    <div class="hub-roles">
      <article v-for="r in roles" :key="r.id" class="hub-roles__item">
        <header class="hub-roles__head" @click="toggle(r.id)">
          <div class="hub-roles__title">
            <strong>{{ r.name }}</strong>
            <AppBadge v-if="r.is_builtin" variant="gray">内置</AppBadge>
          </div>
          <div class="hub-roles__sub">{{ r.description || '—' }}</div>
          <span class="hub-roles__count">{{ r.permissions.length }} 项权限</span>
          <component :is="expanded[r.id] ? ChevronDown : ChevronRight" :size="14" />
        </header>
        <div v-if="expanded[r.id]" class="hub-roles__body">
          <ul class="hub-roles__perms">
            <li v-for="p in r.permissions" :key="p.code">
              <strong>{{ p.name }}</strong>
            </li>
          </ul>
        </div>
      </article>
      <div v-if="!roles.length && !error" class="hub-page__hint">加载中…</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'
import { listRoles } from '../../api/roles'
import { pickErrorDetail } from '../../api'
import AppBadge from '../../components/ui/AppBadge.vue'

const roles = ref([])
const error = ref('')
const expanded = reactive({})

async function load() {
  try {
    const data = await listRoles()
    roles.value = data.items || []
  } catch (e) {
    error.value = pickErrorDetail(e, '加载失败')
  }
}

function toggle(id) {
  expanded[id] = !expanded[id]
}

onMounted(load)
</script>

<style scoped>
.hub-page { display: flex; flex-direction: column; gap: 12px; }
.hub-page__title { font-size: 18px; font-weight: 600; color: var(--text); margin: 0; }
.hub-page__hint { font-size: 12px; color: var(--text-muted); margin: 0; }
.hub-page__error {
  background: color-mix(in srgb, var(--error) 12%, transparent);
  color: var(--error);
  border: 1px solid color-mix(in srgb, var(--error) 30%, transparent);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
}
.hub-roles { display: flex; flex-direction: column; gap: 8px; }
.hub-roles__item {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.hub-roles__head {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) 2fr auto auto;
  align-items: center;
  padding: 12px 14px;
  cursor: pointer;
  gap: 12px;
}
.hub-roles__head:hover { background: var(--elevated); }
.hub-roles__title { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.hub-roles__sub { font-size: 12px; color: var(--text-muted); }
.hub-roles__count { font-size: 11px; color: var(--text-muted); }
.hub-roles__body {
  border-top: 1px solid var(--border);
  padding: 10px 14px 14px;
}
.hub-roles__perms {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 6px;
}
.hub-roles__perms li {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 6px 10px;
  background: var(--elevated);
  border-radius: 6px;
}
.hub-roles__perms strong { color: var(--text); font-weight: 500; }
</style>
