import type { Matcher, PlatformInfo, RecentItem } from './types'

export function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function buildMatchers(platforms: PlatformInfo[]): Matcher[] {
  return platforms.map((platform) => ({
    platform,
    pattern: new RegExp(platform.hosts.map(escapeRegExp).join('|'), 'i'),
  }))
}

export function matchPlatform(text: string, matchers: Matcher[]): PlatformInfo | null {
  return matchers.find((matcher) => matcher.pattern.test(text))?.platform ?? null
}

export function looksLikeShare(text: string, matchers: Matcher[]): boolean {
  if (!text.trim()) return false
  if (/https?:\/\//i.test(text)) return true
  return matchers.some((matcher) => matcher.pattern.test(text))
}

const URL_LINE_RE = /https?:\/\/[^\s"'<>]+/i

export function splitEntries(text: string): string[] {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  if (!lines.length) return []
  // 整段没有任何链接：原样当一条（让调用方给出明确报错）
  if (!lines.some((line) => URL_LINE_RE.test(line))) return [text.trim()]
  // 纯文本行（分享口令的尾巴如「拷走口令…」）并入相邻链接段，
  // 不单独成条；没有跟上链接的孤儿尾巴直接丢弃。
  const segments: string[] = []
  let current: string[] = []
  for (const line of lines) {
    current.push(line)
    if (URL_LINE_RE.test(line)) {
      segments.push(current.join('\n'))
      current = []
    }
  }
  return segments
}

export const listText = (names: string[]) => names.join('、')

export const orText = (names: string[]) => {
  if (names.length === 0) return ''
  if (names.length === 1) return listText(names)
  const last = names[names.length - 1]
  const separator = /^[A-Za-z]/.test(last) ? '或 ' : '或'
  return `${names.slice(0, -1).join('、')}${separator}${last}`
}

// ---- 最近解析记录（localStorage）----
const RECENT_KEY = 'vd:recent'
const RECENT_MAX = 8

export function loadRecent(): RecentItem[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(
        (item): item is RecentItem =>
          !!item &&
          typeof item === 'object' &&
          typeof (item as RecentItem).input === 'string' &&
          typeof (item as RecentItem).ts === 'number',
      )
      .slice(0, RECENT_MAX)
  } catch {
    return []
  }
}

export function saveRecent(items: RecentItem[]) {
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(items))
  } catch {
    /* 配额满 / 隐私模式：静默失败即可 */
  }
}

export const RECENT_MAX_ITEMS = RECENT_MAX

export function relTime(ts: number): string {
  const diff = Date.now() - ts
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`
  return new Date(ts).toLocaleDateString()
}

// 剪贴板 API 仅在 secure context（https/localhost）可用
export const canPaste =
  typeof window !== 'undefined' &&
  window.isSecureContext &&
  typeof navigator.clipboard?.readText === 'function'