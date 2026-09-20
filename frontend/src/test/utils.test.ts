import { describe, expect, it, beforeEach, vi } from 'vitest'
import {
  RECENT_MAX_ITEMS,
  buildMatchers,
  escapeRegExp,
  listText,
  loadRecent,
  looksLikeShare,
  matchPlatform,
  orText,
  relTime,
  saveRecent,
  splitEntries,
} from '../utils'
import type { PlatformInfo, RecentItem } from '../types'

const PLATFORMS: PlatformInfo[] = [
  { key: 'kuaishou', name: '快手', hosts: ['v.kuaishou.com', 'www.kuaishou.com'], kinds: ['video', 'images'] },
  { key: 'douyin', name: '抖音', hosts: ['v.douyin.com', 'www.douyin.com'], kinds: ['video'] },
  { key: 'bilibili', name: 'B站', hosts: ['b23.tv', 'bilibili.com'], kinds: ['video'] },
]

describe('escapeRegExp', () => {
  it('转义正则元字符', () => {
    expect(escapeRegExp('a.b*c')).toBe('a\\.b\\*c')
    expect(escapeRegExp('a+b?c')).toBe('a\\+b\\?c')
    expect(escapeRegExp('(x)[y]{z}')).toBe('\\(x\\)\\[y\\]\\{z\\}')
    expect(escapeRegExp('a|b')).toBe('a\\|b')
    expect(escapeRegExp('^$')).toBe('\\^\\$')
  })

  it('普通字符原样保留', () => {
    expect(escapeRegExp('v.kuaishou.com')).toBe('v\\.kuaishou\\.com')
  })
})

describe('buildMatchers', () => {
  it('每个平台生成一个匹配器', () => {
    const matchers = buildMatchers(PLATFORMS)
    expect(matchers).toHaveLength(3)
    expect(matchers[0].platform.key).toBe('kuaishou')
  })

  it('匹配器能命中该平台的任一域名', () => {
    const matchers = buildMatchers(PLATFORMS)
    expect(matchers[0].pattern.test('https://v.kuaishou.com/xxx')).toBe(true)
    expect(matchers[0].pattern.test('https://www.kuaishou.com/xxx')).toBe(true)
    expect(matchers[0].pattern.test('https://v.douyin.com/xxx')).toBe(false)
  })

  it('域名中的点在正则里被转义，不会匹配到任意字符', () => {
    const matchers = buildMatchers([
      { key: 'x', name: 'X', hosts: ['a.b'], kinds: ['video'] },
    ])
    // 'a.b' 若未转义，'aXb' 也会命中
    expect(matchers[0].pattern.test('aXb')).toBe(false)
    expect(matchers[0].pattern.test('a.b')).toBe(true)
  })

  it('大小写不敏感', () => {
    const matchers = buildMatchers(PLATFORMS)
    expect(matchers[0].pattern.test('HTTPS://V.KUAISHOU.COM/X')).toBe(true)
  })

  it('空平台列表返回空数组', () => {
    expect(buildMatchers([])).toEqual([])
  })
})

describe('matchPlatform', () => {
  const matchers = buildMatchers(PLATFORMS)

  it('识别出对应平台', () => {
    expect(matchPlatform('看看这个 https://v.douyin.com/abc', matchers)?.name).toBe('抖音')
    expect(matchPlatform('https://b23.tv/xyz', matchers)?.name).toBe('B站')
  })

  it('未命中任何平台返回 null', () => {
    expect(matchPlatform('https://youtube.com/watch?v=1', matchers)).toBeNull()
    expect(matchPlatform('随便一段文字', matchers)).toBeNull()
    expect(matchPlatform('', matchers)).toBeNull()
  })

  it('平台清单为空时返回 null', () => {
    expect(matchPlatform('https://v.douyin.com/x', [])).toBeNull()
  })

  it('按注册顺序返回首个命中（清单顺序即优先级）', () => {
    const text = 'https://v.kuaishou.com/a https://v.douyin.com/b'
    expect(matchPlatform(text, matchers)?.name).toBe('快手')
  })
})

describe('looksLikeShare', () => {
  const matchers = buildMatchers(PLATFORMS)

  it('含 http(s) 链接即认为是分享', () => {
    expect(looksLikeShare('https://example.com/x', matchers)).toBe(true)
    expect(looksLikeShare('http://example.com', matchers)).toBe(true)
  })

  it('命中平台域名即认为是分享（即使无协议前缀）', () => {
    expect(looksLikeShare('v.douyin.com/abc', matchers)).toBe(true)
  })

  it('空或纯文本不算分享', () => {
    expect(looksLikeShare('', matchers)).toBe(false)
    expect(looksLikeShare('   ', matchers)).toBe(false)
    expect(looksLikeShare('今天天气不错', matchers)).toBe(false)
  })
})

describe('splitEntries', () => {
  it('按行拆分并去掉空行', () => {
    expect(splitEntries('a\nb\nc')).toEqual(['a', 'b', 'c'])
    expect(splitEntries('a\n\n\nb')).toEqual(['a', 'b'])
  })

  it('去除每行首尾空白', () => {
    expect(splitEntries('  a  \n\t b \t')).toEqual(['a', 'b'])
  })

  it('兼容 CRLF 换行', () => {
    expect(splitEntries('a\r\nb\r\nc')).toEqual(['a', 'b', 'c'])
  })

  it('按内容去重，保留首次出现顺序', () => {
    expect(splitEntries('a\nb\na\nc\nb')).toEqual(['a', 'b', 'c'])
  })

  it('单行返回单元素数组', () => {
    expect(splitEntries('only-one')).toEqual(['only-one'])
  })

  it('空串 / 全空白返回空数组', () => {
    expect(splitEntries('')).toEqual([])
    expect(splitEntries('   \n  \n ')).toEqual([])
  })

  it('不因换行截断而误拆分享口令（口令内部的空格保留）', () => {
    // 分享口令常见形态：文案 + 链接，整体是一行
    const line = '【标题】 https://v.douyin.com/abc 复制打开抖音'
    expect(splitEntries(line)).toEqual([line])
  })
})

describe('listText / orText', () => {
  it('listText 用顿号连接', () => {
    expect(listText(['A', 'B', 'C'])).toBe('A、B、C')
    expect(listText(['A'])).toBe('A')
    expect(listText([])).toBe('')
  })

  it('orText 只在末项前加「或」', () => {
    // 末项以字母开头会补空格（见下一条用例的设计意图）
    expect(orText(['A', 'B', 'C'])).toBe('A、B或 C')
    expect(orText(['甲', '乙', '丙'])).toBe('甲、乙或丙')
    expect(orText(['A', 'B'])).toBe('A或 B')
  })

  it('orText 单个 / 空数组不产生多余的「或」', () => {
    expect(orText(['A'])).toBe('A')
    expect(orText([])).toBe('')
  })

  it('末项以字母开头时补空格，避免「A或B站」挤在一起', () => {
    expect(orText(['快手', 'B站'])).toBe('快手或 B站')
    expect(orText(['快手', '抖音'])).toBe('快手或抖音')
  })
})

describe('relTime', () => {
  const now = Date.now()

  beforeEach(() => {
    // 固定「当前时间」，避免用例在边界时刻抖动
    vi.setSystemTime(now)
  })

  it('1 分钟内显示「刚刚」', () => {
    expect(relTime(now)).toBe('刚刚')
    expect(relTime(now - 59_000)).toBe('刚刚')
  })

  it('1 小时内显示分钟', () => {
    expect(relTime(now - 60_000)).toBe('1 分钟前')
    expect(relTime(now - 59 * 60_000)).toBe('59 分钟前')
  })

  it('1 天内显示小时', () => {
    expect(relTime(now - 60 * 60_000)).toBe('1 小时前')
    expect(relTime(now - 23 * 60 * 60_000)).toBe('23 小时前')
  })

  it('30 天内显示天数', () => {
    expect(relTime(now - 24 * 60 * 60_000)).toBe('1 天前')
    expect(relTime(now - 29 * 24 * 60 * 60_000)).toBe('29 天前')
  })

  it('超过 30 天回落为具体日期', () => {
    const old = now - 40 * 24 * 60 * 60_000
    expect(relTime(old)).toBe(new Date(old).toLocaleDateString())
  })
})

describe('最近解析记录（localStorage）', () => {
  const item = (over: Partial<RecentItem> = {}): RecentItem => ({
    input: 'https://v.douyin.com/abc',
    title: '标题',
    platform: '抖音',
    kind: 'video',
    ts: 1_700_000_000_000,
    ...over,
  }) as RecentItem

  it('无记录时返回空数组', () => {
    expect(loadRecent()).toEqual([])
  })

  it('写入后可读回', () => {
    saveRecent([item()])
    const loaded = loadRecent()
    expect(loaded).toHaveLength(1)
    expect(loaded[0].input).toBe('https://v.douyin.com/abc')
  })

  it('损坏的 JSON 不影响使用（回落空数组）', () => {
    window.localStorage.setItem('vd:recent', '{不是合法 JSON')
    expect(loadRecent()).toEqual([])
  })

  it('非数组内容被拒绝', () => {
    window.localStorage.setItem('vd:recent', '{"a":1}')
    expect(loadRecent()).toEqual([])
    window.localStorage.setItem('vd:recent', '"字符串"')
    expect(loadRecent()).toEqual([])
  })

  it('过滤掉结构不完整的条目', () => {
    window.localStorage.setItem(
      'vd:recent',
      JSON.stringify([
        item({ input: 'ok' }),
        { title: '缺 input' },
        { input: '缺 ts' },
        null,
        'string',
      ]),
    )
    const loaded = loadRecent()
    expect(loaded).toHaveLength(1)
    expect(loaded[0].input).toBe('ok')
  })

  it('超出上限的旧记录被截断', () => {
    const many = Array.from({ length: RECENT_MAX_ITEMS + 5 }, (_, i) =>
      item({ input: `https://x.com/${i}` }),
    )
    window.localStorage.setItem('vd:recent', JSON.stringify(many))
    expect(loadRecent()).toHaveLength(RECENT_MAX_ITEMS)
  })

  it('localStorage 抛异常时静默失败（隐私模式）', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => saveRecent([item()])).not.toThrow()
    spy.mockRestore()

    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(() => loadRecent()).not.toThrow()
    expect(loadRecent()).toEqual([])
    getSpy.mockRestore()
  })
})
