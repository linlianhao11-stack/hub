import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppButton from '../AppButton.vue'

describe('AppButton', () => {
  it('renders slot content', () => {
    const wrapper = mount(AppButton, {
      slots: { default: '点击' },
    })
    expect(wrapper.text()).toContain('点击')
  })

  it('emits click event', async () => {
    const wrapper = mount(AppButton, {
      slots: { default: '按钮' },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
  })
})
