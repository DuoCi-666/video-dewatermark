import { describe, expect, it, beforeEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useTheme } from '../hooks/useTheme'

/** 切换 jsdom 里模拟的「系统」偏好（见 test/setup.ts） */
const setSystemTheme = (theme: 'dark' | 'light') => {
  ;(window as unknown as { __setSystemTheme: (t: 'dark' | 'light') => void }).__setSystemTheme(
    theme,
  )
}

const htmlHasDark = () => document.documentElement.classList.contains('dark')
const storedPref = () => window.localStorage.getItem('vd:theme')

describe('useTheme', () => {
  beforeEach(() => {
    setSystemTheme('light')
    window.localStorage.clear()
  })

  it('无历史偏好时默认「跟随系统」', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.pref).toBe('system')
  })

  it('跟随系统：系统深色则生效主题为深色', () => {
    setSystemTheme('dark')
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('dark')
  })

  it('跟随系统：系统浅色则生效主题为浅色', () => {
    setSystemTheme('light')
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('light')
  })

  it('会把生效主题落到 <html class="dark">', () => {
    setSystemTheme('dark')
    renderHook(() => useTheme())
    expect(htmlHasDark()).toBe(true)
  })

  it('浅色时移除 dark class', () => {
    setSystemTheme('light')
    document.documentElement.classList.add('dark')
    renderHook(() => useTheme())
    expect(htmlHasDark()).toBe(false)
  })

  it('切换顺序为 system → light → dark → system', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.pref).toBe('system')

    act(() => result.current.cycle())
    expect(result.current.pref).toBe('light')

    act(() => result.current.cycle())
    expect(result.current.pref).toBe('dark')

    act(() => result.current.cycle())
    expect(result.current.pref).toBe('system')
  })

  it('选择深色后即使系统是浅色，也保持深色', () => {
    setSystemTheme('light')
    const { result } = renderHook(() => useTheme())

    act(() => result.current.setPref('dark'))
    expect(result.current.theme).toBe('dark')
    expect(htmlHasDark()).toBe(true)
  })

  it('选择浅色后即使系统是深色，也保持浅色', () => {
    setSystemTheme('dark')
    const { result } = renderHook(() => useTheme())

    act(() => result.current.setPref('light'))
    expect(result.current.theme).toBe('light')
    expect(htmlHasDark()).toBe(false)
  })

  it('偏好写入 localStorage 持久化', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setPref('dark'))
    expect(storedPref()).toBe('dark')

    act(() => result.current.setPref('light'))
    expect(storedPref()).toBe('light')
  })

  it('刷新（重新挂载）后读回已保存的偏好', () => {
    window.localStorage.setItem('vd:theme', 'dark')
    setSystemTheme('light')
    const { result } = renderHook(() => useTheme())
    expect(result.current.pref).toBe('dark')
    expect(result.current.theme).toBe('dark')
  })

  it('损坏的持久化值回落为「跟随系统」', () => {
    window.localStorage.setItem('vd:theme', '紫色')
    const { result } = renderHook(() => useTheme())
    expect(result.current.pref).toBe('system')
  })

  it('跟随系统时，系统偏好变化会实时联动', () => {
    setSystemTheme('light')
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('light')

    act(() => setSystemTheme('dark'))
    expect(result.current.theme).toBe('dark')
    expect(htmlHasDark()).toBe(true)

    act(() => setSystemTheme('light'))
    expect(result.current.theme).toBe('light')
    expect(htmlHasDark()).toBe(false)
  })

  it('手动选择后，系统变化不再影响页面（不被覆盖）', () => {
    setSystemTheme('dark')
    const { result } = renderHook(() => useTheme())

    // 用户手动选浅色（此时系统是深色，两者不一致才测得出覆盖问题）
    act(() => result.current.setPref('light'))
    expect(result.current.theme).toBe('light')
    expect(htmlHasDark()).toBe(false)

    // 系统在深/浅之间来回切换，用户的手动选择都不应被覆盖
    act(() => setSystemTheme('dark'))
    expect(result.current.theme).toBe('light')
    expect(htmlHasDark()).toBe(false)

    act(() => setSystemTheme('light'))
    expect(result.current.theme).toBe('light')

    // 手动选深色（系统此时为浅色），同样不受系统影响
    act(() => result.current.setPref('dark'))
    expect(result.current.theme).toBe('dark')
    act(() => setSystemTheme('dark'))
    expect(result.current.theme).toBe('dark')
    act(() => setSystemTheme('light'))
    expect(result.current.theme).toBe('dark')
    expect(htmlHasDark()).toBe(true)
  })

  it('从手动切回「跟随系统」后，立刻采用当前系统偏好', () => {
    setSystemTheme('dark')
    const { result } = renderHook(() => useTheme())

    act(() => result.current.setPref('light'))
    expect(result.current.theme).toBe('light')

    act(() => result.current.setPref('system'))
    expect(result.current.theme).toBe('dark')
  })

  it('同步 colorScheme 到 <html>，让原生控件跟着变', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setPref('dark'))
    expect(document.documentElement.style.colorScheme).toBe('dark')

    act(() => result.current.setPref('light'))
    expect(document.documentElement.style.colorScheme).toBe('light')
  })

  it('localStorage 不可用时仍能正常切换（隐私模式）', () => {
    const { result } = renderHook(() => useTheme())
    // setPref 内部会尝试写 localStorage；用 spy 让它抛错
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(() => act(() => result.current.setPref('dark'))).not.toThrow()
    expect(result.current.theme).toBe('dark')
    spy.mockRestore()
  })
})
