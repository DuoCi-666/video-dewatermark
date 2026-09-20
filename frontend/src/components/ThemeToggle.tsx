import { Monitor, Moon, Sun } from 'lucide-react'
import type { ThemePref } from '../hooks/useTheme'

const LABELS: Record<ThemePref, string> = {
  light: '浅色',
  dark: '深色',
  system: '跟随系统',
}

/**
 * 主题循环切换按钮：浅色 → 深色 → 跟随系统 → 浅色。
 * 显示「当前生效」的图标（跟随系统时看系统解析结果），点按切换偏好。
 */
export function ThemeToggle({
  pref,
  theme,
  onCycle,
}: {
  pref: ThemePref
  theme: 'light' | 'dark'
  onCycle: () => void
}) {
  const Icon = pref === 'system' ? Monitor : theme === 'dark' ? Moon : Sun
  const next = pref === 'light' ? '深色' : pref === 'dark' ? '跟随系统' : '浅色'

  return (
    <button
      type="button"
      onClick={onCycle}
      aria-label={`当前${LABELS[pref]}`}
      title={`主题：${LABELS[pref]}（点击切换为${next}）`}
      className="inline-flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full border border-[var(--card-border)] bg-[var(--card)] text-[var(--fg)] shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:bg-[var(--surface)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:translate-y-0"
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </button>
  )
}
