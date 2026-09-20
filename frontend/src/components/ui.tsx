import type { ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'

/**
 * 全站设计令牌集中的一层薄封装。
 * 现代柔和风：圆角卡片 + 柔和投影 + 渐变主按钮 + 平滑位移反馈。
 */

/** 卡片：圆角、细描边、柔和投影 */
export const CARD =
  'rounded-2xl border border-[var(--card-border)] bg-[var(--card)] shadow-[0_8px_30px_-12px_rgba(15,23,42,0.25)]'

/** 可点击元素的统一按压反馈：悬停轻微上浮，按下归位 */
export const PRESSABLE =
  'touch-control transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_12px_36px_-12px_rgba(15,23,42,0.3)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:translate-y-0'

/** 主操作按钮：靛紫渐变 + 柔光 */
export const PRIMARY_BUTTON =
  'touch-control inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] px-5 font-semibold text-white shadow-[0_8px_24px_-8px_var(--glow)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_12px_30px_-8px_var(--glow)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0'

/** 次要按钮：白底软描边 */
export const SECONDARY_BUTTON =
  'touch-control inline-flex min-h-11 cursor-pointer items-center justify-center gap-1.5 rounded-xl border border-[var(--card-border)] bg-[var(--card)] px-4 font-medium text-[var(--fg)] transition-all duration-150 hover:-translate-y-0.5 hover:bg-[var(--surface)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:translate-y-0'

/** 幽灵按钮：低权重动作 */
export const GHOST_BUTTON =
  'touch-control inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-xl px-3.5 text-sm font-medium text-[var(--muted)] transition-colors hover:text-[var(--fg)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]'

/** 平台徽标：柔和渐变小标签 */
export function PlatformBadge({ name, className = '' }: { name: string; className?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full bg-[var(--surface)] px-2 py-0.5 text-[11px] font-semibold text-[var(--accent)] ${className}`}
    >
      {name}
    </span>
  )
}

/** 错误横幅：统一红色语义 + 图标 */
export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] px-3.5 py-2.5 text-sm leading-6 text-[var(--error-text)]"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 space-y-1">{children}</div>
    </div>
  )
}
