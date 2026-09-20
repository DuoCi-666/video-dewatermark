import { RotateCcw, AlertCircle } from 'lucide-react'
import { CARD } from './ui'
import { orText } from '../utils'
import type { PlatformInfo } from '../types'

/** 空态引导：把「怎么用」讲清楚，减少第一次使用的困惑 */
export function Guide({ platforms }: { platforms: PlatformInfo[] }) {
  const names = platforms.map((item) => item.name)
  const steps = [
    names.length > 0
      ? `打开${orText(names)}，点作品分享，复制链接或口令`
      : '在 App 里点作品分享，复制链接或口令',
    '粘贴到上方输入框，点「开始解析」',
    '在线预览，一键下载无水印视频或图片',
  ]
  return (
    <section className={`${CARD} p-4 sm:p-5`}>
      <h2 className="mb-3 text-sm font-bold">三步搞定</h2>
      <ol className="space-y-2.5">
        {steps.map((step, index) => (
          <li key={step} className="flex items-start gap-3 text-sm leading-6 text-[var(--muted)]">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-xs font-semibold text-white">
              {index + 1}
            </span>
            {step}
          </li>
        ))}
      </ol>
    </section>
  )
}

/** 单条解析失败提示：给出常见原因与重试入口 */
export function ErrorAlert({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] px-3.5 py-2.5 text-sm leading-6 text-[var(--error-text)]"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="space-y-1.5">
        <p>{message}</p>
        <p className="text-xs leading-5 opacity-90">
          常见原因：口令已过期或作品未公开——回到 App 重新复制一条即可。
        </p>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="touch-control inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-xl border border-[var(--error-border)] bg-[var(--card)] px-3.5 text-sm font-medium text-[var(--error-text)] transition-all duration-150 hover:-translate-y-0.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--error-border)] active:translate-y-0"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            重试一次
          </button>
        ) : null}
      </div>
    </div>
  )
}

/** 解析中的骨架卡：与结果卡同构，降低等待焦虑 */
export function ResultSkeleton() {
  return (
    <section aria-hidden="true" className={`${CARD} animate-pulse space-y-4 p-4 sm:p-5`}>
      <div className="space-y-2">
        <div className="h-5 w-2/3 rounded bg-[var(--skeleton)]" />
        <div className="h-4 w-1/3 rounded bg-[var(--skeleton-soft)]" />
      </div>
      <div className="aspect-video w-full rounded-xl border border-[var(--card-border)] bg-[var(--surface)]" />
      <div className="h-12 w-full rounded-xl border border-[var(--card-border)] bg-[var(--surface)]" />
    </section>
  )
}
