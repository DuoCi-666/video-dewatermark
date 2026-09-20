import { useCallback, useEffect, useState } from 'react'

/** 用户在 UI 上选择的主题偏好；'system' 表示跟随系统 */
export type ThemePref = 'light' | 'dark' | 'system'
/** 实际生效的主题（system 解析后的结果） */
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'vd:theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light'
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

/** 读取持久化的偏好；无记录 / 数据损坏 / 隐私模式一律回落 system */
function loadPref(): ThemePref {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') return raw
  } catch {
    /* 隐私模式：忽略 */
  }
  return 'system'
}

function savePref(pref: ThemePref) {
  try {
    window.localStorage.setItem(STORAGE_KEY, pref)
  } catch {
    /* 配额满 / 隐私模式：静默失败，不影响切换 */
  }
}

/** 把生效主题写进 <html class="dark"> 与 theme-color，供 CSS 变量与浏览器 UI 取用 */
function applyTheme(theme: ResolvedTheme) {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.style.colorScheme = theme
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (meta) meta.content = theme === 'dark' ? '#0a0a0a' : '#f7f4ee'
}

/**
 * 暗色模式：
 * - 'system' 时实时跟随系统偏好（监听 matchMedia change）
 * - 偏好持久化到 localStorage，刷新后保留
 * - 首屏由 index.html 内联脚本提前落 class，避免主题闪白（FOUC）
 */
export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(() =>
    typeof window === 'undefined' ? 'system' : loadPref(),
  )
  // 系统偏好只在 'system' 下才有意义：用状态跟踪 change 事件，避免在 effect 里同步 setState
  const [sysTheme, setSysTheme] = useState<ResolvedTheme>(() =>
    typeof window === 'undefined' ? 'light' : systemTheme(),
  )

  // 生效主题在渲染期直接派生，无需额外 state
  const theme: ResolvedTheme = pref === 'system' ? sysTheme : pref

  // 跟随系统：仅在 pref === 'system' 时监听，避免手动选择被系统切换覆盖
  useEffect(() => {
    if (pref !== 'system') return
    const media = window.matchMedia(DARK_QUERY)
    const onChange = () => setSysTheme(media.matches ? 'dark' : 'light')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [pref])

  // 同步 DOM 与外部存储：主题变化时落 class 与 localStorage
  useEffect(() => {
    applyTheme(theme)
    savePref(pref)
  }, [pref, theme])

  /** light → dark → system 循环切换，点一下有明确反馈 */
  const cycle = useCallback(() => {
    setPref((prev) => (prev === 'light' ? 'dark' : prev === 'dark' ? 'system' : 'light'))
  }, [])

  return { pref, theme, setPref, cycle }
}
