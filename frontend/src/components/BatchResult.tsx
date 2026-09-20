import { useState } from 'react'
import { AlertCircle, Download, LoaderCircle } from 'lucide-react'
import { ResultCard } from './ResultCard'
import { CARD, PlatformBadge, PRIMARY_BUTTON, SECONDARY_BUTTON } from './ui'
import { matchPlatform } from '../utils'
import type { BatchItemDto, BatchJob, Matcher } from '../types'

/** 批量解析结果：顶部进度条 + 逐条排队/解析/结果卡片 */
export function BatchResult({
  entries,
  batch,
  batchRunning,
  batchError,
  matchers,
  onToast,
}: {
  entries: string[]
  batch: BatchJob | null
  batchRunning: boolean
  batchError: string
  matchers: Matcher[]
  onToast: (message: string) => void
}) {
  const [packing, setPacking] = useState(false)
  const [packReady, setPackReady] = useState(false)
  const [packError, setPackError] = useState('')

  const total = batch?.total ?? entries.length
  const done = batch?.done ?? 0
  const okCount = batch?.okCount ?? 0
  const failCount = batch?.failCount ?? 0
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0

  // 批量完成后触发/轮询「打包下载 ZIP」：POST 后台打包，轮询直到 done/failed。
  // 只打包成功条目里的主媒体（视频默认档 + 图片全原图），失败项自动跳过。
  async function startPack() {
    const jobId = batch?.id
    if (!jobId) return
    setPacking(true)
    setPackReady(false)
    setPackError('')
    try {
      const trigger = await fetch(`/api/batch/${jobId}/zip`, { method: 'POST' })
      if (!trigger.ok) {
        const data = (await trigger.json().catch(() => ({}))) as { message?: string }
        setPackError(data.message || '打包失败，请稍后重试')
        return
      }
      for (let attempt = 0; attempt < 300; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1000))
        const res = await fetch(`/api/batch/${jobId}/zip`)
        if (!res.ok) {
          setPackError('打包状态获取失败，请稍后重试')
          return
        }
        const data = (await res.json()) as {
          pack: { status: string; canDownload?: boolean; error?: string }
        }
        if (data.pack.status === 'done') {
          setPackReady(true)
          return
        }
        if (data.pack.status === 'failed') {
          setPackError(data.pack.error || '打包失败，请稍后重试')
          return
        }
      }
      setPackError('打包超时，请稍后重试')
    } finally {
      setPacking(false)
    }
  }

  const items =
    batch?.items ??
    entries.map((_, index) => ({
      index,
      status: (batchRunning ? 'pending' : 'done') as BatchItemDto['status'],
      ok: null,
      code: '',
      message: '',
      platform: '',
      ms: 0,
      result: null,
    }))

  return (
    <section className={`${CARD} animate-fade-up space-y-5 p-4 sm:p-5`}>
      <header className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-bold">批量解析</h2>
          <span className="font-mono text-xs font-bold text-[var(--muted)]">
            {batchRunning ? `完成 ${done}/${total}` : `${okCount} 成功 · ${failCount} 失败`}
          </span>
        </div>
        {batchRunning ? (
          <p className="text-xs font-bold text-[var(--muted)]">正在解析，逐条打勾…</p>
        ) : (
          <p className="text-xs font-bold text-[var(--muted)]">
            {okCount === total ? '全部解析完成' : '解析完成'}
          </p>
        )}
        <div
          role="progressbar"
          aria-valuenow={done}
          aria-valuemin={0}
          aria-valuemax={total}
          className="h-3 w-full overflow-hidden rounded-full bg-[var(--surface)]"
        >
          <div
            className="h-full rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] transition-all duration-300"
            style={{ width: `${percent}%` }}
          />
        </div>
      </header>

      {!batchRunning && batch && okCount > 0 ? (
        <div className="border-t border-dashed border-[var(--card-border)] pt-3">
          {packReady ? (
            <a
              href={`/api/batch/${batch.id}/zip/download`}
              className={`${PRIMARY_BUTTON} w-full px-3 py-2 text-sm`}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              下载 ZIP（{okCount} 条成功媒体）
            </a>
          ) : packing ? (
            <div className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--card-border)] bg-[var(--surface)] px-3 py-2 text-sm font-medium text-[var(--muted)]">
              <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
              正在汇总打包…
            </div>
          ) : (
            <button type="button" onClick={startPack} className={`${SECONDARY_BUTTON} w-full px-3 py-2 text-sm`}>
              <Download className="h-4 w-4" aria-hidden="true" />
              打包下载 ZIP
            </button>
          )}
          {packError ? (
            <p className="mt-2 text-xs font-bold text-[var(--error-text)]">{packError}</p>
          ) : null}
        </div>
      ) : null}

      {batchError ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] px-3.5 py-2.5 text-sm leading-6 text-[var(--error-text)]"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>{batchError}</p>
        </div>
      ) : null}

      <ol className="space-y-4">
        {items.map((item) => {
          const platform = matchPlatform(entries[item.index] ?? '', matchers)
          return (
            <li key={item.index}>
              <div className="flex items-center gap-2 pb-2">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-xs font-semibold text-white">
                  {item.index + 1}
                </span>
                {platform ? <PlatformBadge name={platform.name} /> : null}
                {item.status === 'pending' || item.status === 'running' ? (
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold text-[var(--muted)]">
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    {item.status === 'running' ? '解析中…' : '排队中…'}
                  </span>
                ) : null}
              </div>
              {item.status === 'done' && item.ok && item.result ? (
                <ResultCard
                  key={item.index}
                  result={item.result}
                  sourceText={entries[item.index] ?? ''}
                  matchers={matchers}
                  onToast={onToast}
                />
              ) : item.status === 'done' && !item.ok ? (
                <BatchFailItem
                  sourceText={entries[item.index] ?? ''}
                  message={item.message || '解析失败'}
                />
              ) : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/** 批量中单条解析失败：只标这一条，不影响其它条目 */
function BatchFailItem({ sourceText, message }: { sourceText: string; message: string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] px-3.5 py-2.5 text-sm leading-6 text-[var(--error-text)]"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        <p>{message}</p>
        <p className="line-clamp-1 break-all font-mono text-xs font-normal opacity-80">{sourceText}</p>
      </div>
    </div>
  )
}
