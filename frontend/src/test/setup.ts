import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  // 每个用例之间清掉持久化偏好，避免互相串味
  window.localStorage.clear()
  document.documentElement.classList.remove('dark')
  vi.restoreAllMocks()
})

// jsdom 未实现 matchMedia；主题 hook 依赖它，这里给出可控的替身。
// 测试里可用 window.__setSystemTheme('dark') 切换系统偏好。
//
// 注意：真实 MediaQueryList 的 `matches` 是动态属性（读取时反映当前状态），
// 因此这里必须用 getter 而非固定值——hook 的 change 回调正是通过读它取新值的。
type Listener = (event: { matches: boolean; media: string }) => void

const listeners = new Set<Listener>()
let systemDark = false

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => {
    const isDarkQuery = query.includes('dark')
    return {
      get matches() {
        return isDarkQuery ? systemDark : !systemDark
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, cb: Listener) => listeners.add(cb),
      removeEventListener: (_: string, cb: Listener) => listeners.delete(cb),
      addListener: (cb: Listener) => listeners.add(cb),
      removeListener: (cb: Listener) => listeners.delete(cb),
      dispatchEvent: () => false,
    }
  },
})

/** 测试辅助：切换「系统」的深浅色偏好并通知监听者 */
;(window as unknown as { __setSystemTheme: (t: 'dark' | 'light') => void }).__setSystemTheme = (
  theme,
) => {
  systemDark = theme === 'dark'
  listeners.forEach((cb) => cb({ matches: systemDark, media: '(prefers-color-scheme: dark)' }))
}

// jsdom 未实现 scrollIntoView；App 在解析完成后会调用它滚动到结果处。
// 不补这个桩会抛出未捕获异常（虽然不影响断言，但会污染测试输出）。
Element.prototype.scrollIntoView = vi.fn()
