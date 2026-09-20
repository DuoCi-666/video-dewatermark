import { CARD, PlatformBadge } from './ui'
import { relTime } from '../utils'
import type { RecentItem } from '../types'

/** 最近解析：复访用户一步回到上次的作品，不用重贴链接 */
export function RecentList({
  items,
  onPick,
  onClear,
}: {
  items: RecentItem[]
  onPick: (input: string) => void
  onClear: () => void
}) {
  return (
    <section className={`${CARD} p-4 sm:p-5`}>
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-bold">最近解析</h2>
        <button
          type="button"
          onClick={onClear}
          className="touch-control -mr-1 inline-flex min-h-11 cursor-pointer items-center rounded-md px-2 font-mono text-[11px] font-bold text-[var(--muted)] transition hover:text-[var(--fg)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--fg)]"
        >
          清空
        </button>
      </div>
      <ul className="space-y-2">
        {items.map((item, index) => (
          <li
            key={item.input}
            className="animate-fade-up"
            style={{ animationDelay: `${Math.min(index, 6) * 45}ms` }}
          >
            <button
              type="button"
              onClick={() => onPick(item.input)}
              title={item.input}
              className={`flex min-h-11 w-full cursor-pointer items-center gap-2 rounded-xl border border-[var(--card-border)] bg-[var(--card)] px-3 py-2 text-left shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:bg-[var(--surface)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] active:translate-y-0`}
            >
              {item.platform ? (
                <PlatformBadge
                  name={item.platform}
                  className="shrink-0 px-1.5 py-0.5 text-[10px]"
                />
              ) : null}
              <span className="line-clamp-1 min-w-0 flex-1 text-sm font-medium">
                {item.title || item.input}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-[var(--subtle)]">
                {relTime(item.ts)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
