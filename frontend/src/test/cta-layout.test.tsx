import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import App from '../App'

// 只做结构核验：提交按钮在 DOM 中唯一，且 CTA 容器带移动端固定定位类
describe('CTA 结构核验', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve(null) })))
    localStorage.clear()
  })

  it('提交按钮全页唯一', () => {
    render(<App />)
    const submitButtons = screen.getAllByRole('button').filter((b) => /开始解析|解析中/.test(b.textContent || ''))
    expect(submitButtons).toHaveLength(1)
  })

  it('CTA 容器移动端 fixed、桌面端 static', () => {
    const { container } = render(<App />)
    const cta = container.querySelector('div.fixed.inset-x-0.bottom-0.z-40') as HTMLElement
    expect(cta).not.toBeNull()
    expect(cta.className).toContain('fixed')
    expect(cta.className).toContain('sm:static')
    const btn = cta.querySelector('button[type="submit"]') as HTMLButtonElement
    expect(btn).not.toBeNull()
    expect(btn.style.marginBottom).toContain('env(safe-area-inset-bottom)')
  })

  it('占位符宽度在移动端存在、桌面端隐藏', () => {
    const { container } = render(<App />)
    const spacer = container.querySelector('div[aria-hidden="true"].sm\\:hidden') as HTMLElement
    expect(spacer).not.toBeNull()
    expect(spacer.className).toContain('sm:hidden')
    expect(spacer.style.height).toContain('env(safe-area-inset-bottom)')
  })
})
