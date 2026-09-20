import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { ClipboardPaste, Link2, LoaderCircle, RotateCcw } from 'lucide-react'

import { ThemeToggle } from './components/ThemeToggle'
import { ResultCard } from './components/ResultCard'
import { BatchResult } from './components/BatchResult'
import { RecentList } from './components/RecentList'
import { Guide, ErrorAlert, ResultSkeleton } from './components/Guide'
import { CARD, GHOST_BUTTON, PRIMARY_BUTTON, SECONDARY_BUTTON } from './components/ui'
import { useTheme } from './hooks/useTheme'
import {
  RECENT_MAX_ITEMS,
  buildMatchers,
  canPaste,
  loadRecent,
  looksLikeShare,
  matchPlatform,
  saveRecent,
  splitEntries,
} from './utils'
import type { BatchJob, ParseErr, ParseOk, PlatformInfo, RecentItem, Status } from './types'

export default function App() {
  const [input, setInput] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState('')
  const [result, setResult] = useState<ParseOk | null>(null)
  const [source, setSource] = useState('')
  const [lastSubmitted, setLastSubmitted] = useState('')
  const [hint, setHint] = useState('')
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([])
  // 批量解析：entryTexts 是对应当前 batch 的原始条目，用于渲染平台徽标与进度
  const [entryTexts, setEntryTexts] = useState<string[]>([])
  const [batch, setBatch] = useState<BatchJob | null>(null)
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchError, setBatchError] = useState('')
  const [recent, setRecent] = useState<RecentItem[]>(() => loadRecent())
  const [toast, setToast] = useState('')
  const resultRef = useRef<HTMLDivElement | null>(null)
  const matchers = useMemo(() => buildMatchers(platforms), [platforms])
  const platformNames = useMemo(() => platforms.map((item) => item.name), [platforms])
  const { pref, theme, cycle } = useTheme()

  // 平台清单来自后端：新增平台无需改动前端
  useEffect(() => {
    let alive = true
    fetch('/api/platforms')
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (alive && data?.ok && Array.isArray(data.platforms)) {
          setPlatforms(data.platforms as PlatformInfo[])
        }
      })
      .catch(() => {
        /* 拿不到清单就只隐藏徽标，不影响解析功能 */
      })
    return () => {
      alive = false
    }
  }, [])

  // 轻提示 3.5s 自动消失
  useEffect(() => {
    if (!hint) return
    const timer = window.setTimeout(() => setHint(''), 3500)
    return () => window.clearTimeout(timer)
  }, [hint])

  // 操作回声（顶部 toast）2.2s 自动消失
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 2200)
    return () => window.clearTimeout(timer)
  }, [toast])

  // 单条解析（run 已判定为单条才走到这）
  async function submit(text: string) {
    setStatus('loading')
    setError('')
    requestAnimationFrame(() => {
      resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })

    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch('/api/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: text }),
        signal: controller.signal,
      })
      const data = (await response.json()) as ParseOk | ParseErr
      if (!data.ok) {
        setResult(null)
        setStatus('error')
        setError(data.message || '解析失败，请稍后重试')
        return
      }
      setResult(data)
      setSource(text)
      setStatus('success')
      pushRecent({
        input: text,
        title: data.title || '',
        platform: matchPlatform(text, matchers)?.name ?? '',
        kind: data.type,
        ts: Date.now(),
      })
      requestAnimationFrame(() => {
        resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    } catch (err) {
      setResult(null)
      setStatus('error')
      setError(
        err instanceof DOMException && err.name === 'AbortError'
          ? '解析超时，请稍后重试'
          : '解析失败，请稍后重试',
      )
    } finally {
      window.clearTimeout(timer)
    }
  }

  // 批量解析：提交后台 job 后轮询，按条拉进度与结果
  async function submitBatch(entries: string[]) {
    setEntryTexts(entries)
    setBatchError('')
    setError('')
    setResult(null)
    setBatchRunning(true)
    setBatch({
      id: '',
      status: 'running',
      total: entries.length,
      done: 0,
      okCount: 0,
      failCount: 0,
      createdAt: '',
      updatedAt: '',
      items: entries.map((_, index) => ({
        index,
        status: 'pending',
        ok: null,
        code: '',
        message: '',
        platform: '',
        ms: 0,
        result: null,
      })),
    })
    requestAnimationFrame(() => {
      resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    try {
      const response = await fetch('/api/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: entries }),
      })
      const data = (await response.json()) as { ok: boolean; jobId?: string; message?: string }
      if (!data.ok || !data.jobId) {
        setBatchError(data.message || '批量解析提交失败，请稍后重试')
        return
      }
      await pollBatch(data.jobId)
    } catch {
      setBatchError('批量解析提交失败，请稍后重试')
    } finally {
      setBatchRunning(false)
    }
  }

  // 轮询批量 job：前 1 分钟用 1.2s 细粒度跟进度，之后放宽到 5s；
  // 总窗口约 30 分钟，与后端 BATCH_JOB_TTL_SEC 默认值对齐 —— 否则慢批量会被
  // 提前判成「耗时较长」，而后台其实还在跑。
  async function pollBatch(jobId: string) {
    for (let attempt = 0; attempt < 400; attempt++) {
      const response = await fetch(`/api/batch/${jobId}`)
      if (!response.ok) {
        setBatchError('批量解析状态获取失败，请稍后重试')
        return
      }
      const data = (await response.json()) as { ok: boolean; job?: BatchJob }
      const job = data.job
      if (!job) {
        setBatchError('批量任务不存在或已过期')
        return
      }
      setBatch(job)
      if (job.status === 'done') return
      const delay = attempt < 50 ? 1200 : 5000
      await new Promise((resolve) => window.setTimeout(resolve, delay))
    }
    setBatchError('解析耗时较长，结果请在稍后刷新查看')
  }

  // 统一入口：按非空行拆分，多行为批量，单行走单条
  function run(inputText: string) {
    const entries = splitEntries(inputText)
    if (entries.length === 0) {
      setStatus('error')
      setError('请粘贴分享链接')
      setResult(null)
      return
    }
    setLastSubmitted(inputText)
    setBatch(null)
    setBatchRunning(false)
    if (entries.length === 1) {
      void submit(entries[0])
    } else {
      void submitBatch(entries)
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void run(input.trim())
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault()
      void run(input.trim())
    }
  }

  function onRetry() {
    if (lastSubmitted) {
      void run(lastSubmitted)
    }
  }

  // 记一条最近解析：同链接去重后置顶，最多 RECENT_MAX_ITEMS 条
  function pushRecent(item: RecentItem) {
    setRecent((prev) => {
      const next = [item, ...prev.filter((old) => old.input !== item.input)].slice(
        0,
        RECENT_MAX_ITEMS,
      )
      saveRecent(next)
      return next
    })
  }

  function clearRecent() {
    setRecent([])
    saveRecent([])
    setToast('已清空记录')
  }

  // 点历史记录 = 填回输入框并重新解析（结果不落盘，重解析拿新直链）
  function pickRecent(inputText: string) {
    setInput(inputText)
    void run(inputText)
  }

  async function onPasteAndParse() {
    let text = ''
    try {
      text = (await navigator.clipboard.readText()).trim()
    } catch {
      setToast('无法读取剪贴板，请点击输入框长按粘贴')
      return
    }
    if (!text) {
      setToast('剪贴板为空')
      return
    }
    setInput(text)
    if (looksLikeShare(text, matchers)) {
      void run(text)
    } else {
      setToast('已粘贴，但未识别到分享链接')
    }
  }

  // 粘贴即解析：paste 事件不受 https 限制，移动端长按粘贴也能触发
  function onTextareaPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const text = event.clipboardData.getData('text').trim()
    if (looksLikeShare(text, matchers) && status !== 'loading') {
      event.preventDefault()
      setInput(text)
      void run(text)
    }
  }

  const loading = status === 'loading'
  const busy = loading || batchRunning

  /**
   * 主 CTA：全站唯一的提交按钮。
   * - 移动端：整个 CTA 区（按钮 + 状态文案）固定在视口底部、安全区之上 —— 拇指可达，
   *   键盘弹出时也不会被顶走；定位交给外层 wrapper，文案才会跟着按钮一起贴底。
   * - 桌面端：sm:static 回到表单流内、右对齐（鼠标路径短）。
   * 只渲染一个 <button type="submit">，避免测试选择器与读屏软件读到重复按钮。
   */
  function renderSubmitCta() {
    return (
      <div className="fixed inset-x-0 bottom-0 z-40 flex flex-col gap-1 border-t border-[var(--card-border)] bg-[var(--card)] px-4 pt-3 shadow-[0_-8px_24px_-16px_rgba(15,23,42,0.3)] sm:static sm:items-end sm:border-0 sm:bg-transparent sm:px-0 sm:pt-0 sm:shadow-none">
        <button
          type="submit"
          disabled={busy}
          className={`${PRIMARY_BUTTON} min-h-[52px] w-full px-5 text-base sm:min-h-11 sm:w-auto sm:text-sm`}
          style={{ marginBottom: 'calc(0.5rem + env(safe-area-inset-bottom))' }}
        >
          {busy ? (
            <>
              <LoaderCircle className="h-5 w-5 animate-spin sm:h-4 sm:w-4" aria-hidden="true" />
              解析中…
            </>
          ) : (
            <>
              <Link2 className="h-5 w-5 sm:h-4 sm:w-4" aria-hidden="true" />
              开始解析
            </>
          )}
        </button>
        {loading ? (
          <p role="status" className="pb-2 text-center text-xs leading-5 text-[var(--muted)] sm:hidden">
            正在解析，通常 2~5 秒…
          </p>
        ) : null}
        {batchRunning ? (
          <p role="status" className="pb-2 text-center text-xs leading-5 text-[var(--muted)] sm:hidden">
            正在批量解析 {entryTexts.length} 条…
          </p>
        ) : null}
      </div>
    )
  }

  return (
    <div className="min-h-dvh text-[var(--fg)]">
      {toast ? (
        <div
          role="status"
          aria-live="polite"
          className="pointer-events-none fixed left-1/2 top-4 z-50 w-max max-w-[calc(100vw-2rem)] -translate-x-1/2"
        >
          <p className="animate-toast-in rounded-full bg-[var(--fg)] px-4 py-2 text-xs font-semibold text-[var(--bg)] shadow-lg">
            {toast}
          </p>
        </div>
      ) : null}

      {/* 间距节奏：移动端 20px、桌面 28px，避免旧版 20→36 的跳变；底部留白统一交给末尾的 CTA 占位块兜底 */}
      <main className="relative mx-auto flex w-full max-w-2xl flex-col gap-5 px-4 pt-6 pb-2 sm:gap-7 sm:px-6 sm:pt-12 sm:pb-10">
        <header className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <h1 className="text-2xl font-extrabold leading-snug tracking-tight sm:text-4xl">
              粘贴链接，
              <span className="bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] bg-clip-text text-transparent">
                拿无水印素材
              </span>
            </h1>
            <ThemeToggle pref={pref} theme={theme} onCycle={cycle} />
          </div>
          {platforms.length > 0 ? (
            <p className="font-mono text-[11px] leading-5 font-bold tracking-wide text-[var(--muted)] sm:text-xs">
              支持 {platformNames.join(' · ')}
            </p>
          ) : null}
        </header>

        <form
          onSubmit={onSubmit}
          className={`${CARD} space-y-3.5 p-4 sm:p-5`}
        >
          <div className="flex items-baseline justify-between gap-2">
            <label htmlFor="share-input" className="block text-sm font-bold">
              分享链接或口令
            </label>
            <span className="hidden font-mono text-[11px] font-bold text-[var(--subtle)] sm:inline">
              ⌘ / Ctrl + Enter 快速解析
            </span>
          </div>
          <textarea
            id="share-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onInputKeyDown}
            onPaste={onTextareaPaste}
            rows={2}
            placeholder={
              platforms.length > 1
                ? `粘贴分享口令，例如：https://${platforms[0].hosts[0]}/xxxx 或 https://${platforms[platforms.length - 1].hosts[0]}/xxxx`
                : '粘贴分享口令'
            }
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            className="min-h-24 w-full resize-y rounded-xl border border-[var(--card-border)] bg-[var(--surface)] px-4 py-3 text-base text-[var(--fg)] outline-none transition placeholder:text-[var(--subtle)] focus:border-[var(--accent)] focus:ring-4 focus:ring-[var(--glow)]"
          />

          {(canPaste || input || hint) && (
            <div className="flex flex-wrap items-center gap-3">
              {canPaste && (
                <button
                  type="button"
                  onClick={onPasteAndParse}
                  className={`${SECONDARY_BUTTON} min-h-11 px-3.5 text-sm`}
                >
                  <ClipboardPaste className="h-4 w-4" aria-hidden="true" />
                  粘贴并解析
                </button>
              )}
              {input && (
                <button
                  type="button"
                  onClick={() => setInput('')}
                  className={`${GHOST_BUTTON} min-h-11`}
                >
                  <RotateCcw className="h-4 w-4" aria-hidden="true" />
                  清空
                </button>
              )}
              {hint ? (
                <p aria-live="polite" className="text-xs font-bold text-[var(--muted)]">
                  {hint}
                </p>
              ) : null}
            </div>
          )}

          {error ? (
            <ErrorAlert message={error} onRetry={lastSubmitted ? onRetry : undefined} />
          ) : null}

          {renderSubmitCta()}
        </form>

        <p className="font-mono text-[11px] leading-5 text-[var(--subtle)]">
          从 App 复制分享链接，整段贴进来即可解析、预览，并下载无水印原片与原图。
        </p>

        <div ref={resultRef} className="scroll-mt-4" aria-live="polite">
          {loading ? (
            <ResultSkeleton />
          ) : batchRunning || batch ? (
            <BatchResult
              entries={entryTexts}
              batch={batch}
              batchRunning={batchRunning}
              batchError={batchError}
              matchers={matchers}
              onToast={setToast}
            />
          ) : result ? (
            <ResultCard
              key={result.videoUrl ?? result.images[0]?.previewUrl ?? 'none'}
              result={result}
              sourceText={source}
              matchers={matchers}
              onToast={setToast}
            />
          ) : (
            <div className="space-y-5">
              {recent.length > 0 ? (
                <RecentList items={recent} onPick={pickRecent} onClear={clearRecent} />
              ) : null}
              <Guide platforms={platforms} />
            </div>
          )}
        </div>

        <footer className="space-y-1 text-center font-mono text-xs leading-5 text-[var(--subtle)]">
          <p>本项目完全免费，如有问题请联系作者</p>
          <p>作者QQ：3213991418</p>
        </footer>

        {/* 移动端为底部固定 CTA 预留高度（按钮 52px + 上下留白 + 安全区）；置于页脚之后，滚到底时正好顶开，避免在内容中间留下一块空洞 */}
        <div
          aria-hidden="true"
          className="h-[68px] sm:hidden"
          style={{ height: 'calc(68px + env(safe-area-inset-bottom))' }}
        />

      </main>
    </div>
  )
}
