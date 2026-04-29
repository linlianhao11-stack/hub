<template>
  <AppBadge :variant="badgeVariant" :label="displayLabel" />
</template>

<script setup>
import { computed } from 'vue'
import AppBadge from './AppBadge.vue'
import {
  orderTypeBadges, orderTypeNames,
  logTypeBadges, logTypeNames,
  purchaseStatusBadges, purchaseStatusNames,
  shipmentStatusBadges, shipmentStatusNames,
  shippingStatusBadges, shippingStatusNames,
  dropshipStatusBadges, dropshipStatusNames
} from '../../utils/constants'

const props = defineProps({
  type: {
    type: String,
    required: true
    // 'orderType' | 'logType' | 'purchaseStatus' | 'shippingStatus' | 'shipmentStatus' | 'dropshipStatus'
  },
  status: {
    type: String,
    required: true
  },
  label: {
    type: String,
    default: ''
  }
})

/* 映射表：CSS class string -> AppBadge variant */
const classToVariant = {
  'badge badge-green': 'success',
  'badge badge-yellow': 'warning',
  'badge badge-red': 'error',
  'badge badge-blue': 'info',
  'badge badge-purple': 'purple',
  'badge badge-orange': 'orange',
  'badge badge-gray': 'gray'
}

const badgeMap = {
  orderType: orderTypeBadges,
  logType: logTypeBadges,
  purchaseStatus: purchaseStatusBadges,
  shipmentStatus: shipmentStatusBadges,
  shippingStatus: shippingStatusBadges,
  dropshipStatus: dropshipStatusBadges
}

const nameMap = {
  orderType: orderTypeNames,
  logType: logTypeNames,
  purchaseStatus: purchaseStatusNames,
  shipmentStatus: shipmentStatusNames,
  shippingStatus: shippingStatusNames,
  dropshipStatus: dropshipStatusNames
}

const badgeVariant = computed(() => {
  const cssClass = badgeMap[props.type]?.[props.status] || 'badge badge-gray'
  return classToVariant[cssClass] || 'gray'
})

const displayLabel = computed(() => {
  return props.label || nameMap[props.type]?.[props.status] || props.status
})
</script>
