<template>
  <AppCard padding="compact">
    <div class="kpi-card">
      <div class="kpi-header">
        <div v-if="iconComponent" class="kpi-icon" :class="`kpi-icon-${iconColor}`">
          <component :is="iconComponent" :size="18" aria-hidden="true" />
        </div>
        <span class="kpi-label">{{ label }}</span>
      </div>
      <div class="kpi-value">{{ value }}</div>
    </div>
  </AppCard>
</template>

<script setup>
import { computed } from 'vue'
import {
  TrendingUp, TrendingDown, DollarSign, ShoppingCart,
  Package, Users, BarChart2, AlertTriangle,
  CheckCircle, Clock, Activity, Zap,
} from 'lucide-vue-next'

// 仅包含 KpiCard icon 属性实际使用的图标
const icons = {
  TrendingUp, TrendingDown, DollarSign, ShoppingCart,
  Package, Users, BarChart2, AlertTriangle,
  CheckCircle, Clock, Activity, Zap,
}
import AppCard from './AppCard.vue'

const props = defineProps({
  label: {
    type: String,
    required: true
  },
  value: {
    type: [String, Number],
    required: true
  },
  icon: {
    type: String,
    default: ''
  },
  iconColor: {
    type: String,
    default: 'primary',
    validator: (v) => ['primary', 'success', 'warning', 'error'].includes(v)
  }
})

const iconComponent = computed(() => {
  if (!props.icon) return null
  const name = props.icon
    .split('-')
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join('')
  return icons[name] || null
})
</script>

<style scoped>
.kpi-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kpi-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kpi-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--r-md);
  flex-shrink: 0;
}

.kpi-icon-primary {
  background: var(--primary-muted);
  color: var(--primary);
}

.kpi-icon-success {
  background: var(--success-subtle);
  color: var(--success);
}

.kpi-icon-warning {
  background: var(--warning-subtle);
  color: var(--warning);
}

.kpi-icon-error {
  background: var(--error-subtle);
  color: var(--error);
}

.kpi-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  letter-spacing: -0.01em;
}

.kpi-value {
  font-size: 24px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  font-family: var(--font-mono);
  line-height: 1.2;
}
</style>
