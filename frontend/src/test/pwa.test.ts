import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { registerServiceWorker } from '../pwa'

/**
 * pwa.ts 的作用是在生产构建下注册 Service Worker。
 * 这里用 stub 覆盖它的几条分支：不支持、非生产、注册成功、注册失败。
 */
describe('registerServiceWorker', () => {
  const original = { ...import.meta.env }

  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    Object.assign(import.meta.env, original)
    vi.unstubAllGlobals()
  })

  it('浏览器不支持 serviceWorker 时直接返回，不抛错', () => {
    const originalSW = Object.getOwnPropertyDescriptor(navigator, 'serviceWorker')
    // 删除 serviceWorker 模拟老浏览器
    delete (navigator as { serviceWorker?: unknown }).serviceWorker

    expect(() => registerServiceWorker()).not.toThrow()

    if (originalSW) Object.defineProperty(navigator, 'serviceWorker', originalSW)
  })

  it('非生产环境不注册（避免干扰热更新）', () => {
    const register = vi.fn(() => Promise.resolve())
    vi.stubGlobal('navigator', { ...navigator, serviceWorker: { register } })
    // 测试环境 import.meta.env.PROD 为 false
    registerServiceWorker()
    expect(register).not.toHaveBeenCalled()
  })

  it('生产环境在 load 之后注册，scope 为根路径', async () => {
    const register = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { register },
    })
    Object.assign(import.meta.env, { PROD: true })
    // 记录 load 监听，手动触发
    const listeners: Record<string, () => void> = {}
    const spy = vi.spyOn(window, 'addEventListener').mockImplementation((type, cb) => {
      listeners[type as string] = cb as () => void
    })

    registerServiceWorker()

    expect(register).not.toHaveBeenCalled() // 尚未触发 load
    listeners.load?.()
    expect(register).toHaveBeenCalledWith('/sw.js', { scope: '/' })

    spy.mockRestore()
  })

  it('注册失败时静默处理（隐身模式 / 策略限制）', async () => {
    const register = vi.fn(() => Promise.reject(new Error('SecurityError')))
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { register },
    })
    Object.assign(import.meta.env, { PROD: true })

    const listeners: Record<string, () => void> = {}
    const spy = vi.spyOn(window, 'addEventListener').mockImplementation((type, cb) => {
      listeners[type as string] = cb as () => void
    })

    registerServiceWorker()
    expect(() => listeners.load?.()).not.toThrow()

    // 等微任务队列排空，确认 rejection 被吞掉
    await new Promise((r) => setTimeout(r, 0))

    spy.mockRestore()
  })
})
