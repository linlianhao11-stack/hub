<template>
  <label class="toggle" :class="{ 'opacity-50 pointer-events-none': disabled }">
    <input
      type="checkbox"
      :checked="modelValue"
      :disabled="disabled"
      @change="$emit('update:modelValue', ($event.target).checked)"
    />
    <span class="slider" />
  </label>
</template>

<script setup>
defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

defineEmits(['update:modelValue'])
</script>

<style scoped>
.toggle {
  width: 40px;
  height: 22px;
}
.toggle .slider {
  border-radius: 22px;
}
.toggle .slider::before {
  height: 18px;
  width: 18px;
  border-radius: 50%;
}
.toggle input:checked + .slider::before {
  transform: translateX(18px);
}
</style>
