import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BatchResult } from '../components/BatchResult'
import { buildMatchers } from '../utils'
import type { BatchJob, Matcher, PlatformInfo } from '../types'

const PLATFORMS: PlatformInfo[] = [
  { key: 'kuaishou', name: '快手', hosts: ['v.kuaishou.com'], kinds: ['video', 'images'] },
  { key: 'douyin', name: '抖音', hosts: ['v.douyin.com'], kinds: ['video'] },
]
const MATCHERS: Matcher[] = buildMatchers(PLATFORMS)

const ENTRIES = ['https://v.kuaishou.com/a', 'https://v.douyin.com/b', 'https://v.douyin.com/c']

const job = (over: Partial<BatchJob> = {}): BatchJob => ({
  id: 'job-1',
  status: 'done',
  total: 3,
  done: 3,
  okCount: 2,
  failCount: 1,
  createdAt: '',
  updatedAt: '',
  items: [
    {
      index: 0,
      status: 'done',
      ok: true,
      code: '',
      message: '',
      platform: 'kuaishou',
      ms: 100,
      result: {
        ok: true,
        type: 'video',
        title: '成功的视频',
        author: '作者A',
        coverUrl: null,
        videoUrl: '/api/media/v1',
        downloadUrl: '/api/download/v1',
        variants: [],
        images: [],
      },
    },
    {
      index: 1,
      status: 'done',
      ok: true,
      code: '',
      message: '',
      platform: 'douyin',
      ms: 120,
      result: {
        ok: true,
        type: 'images',
        title: '成功的图集',
        author: '作者B',
        coverUrl: null,
        videoUrl: null,
        downloadUrl: null,
        variants: [],
        images: [{ previewUrl: '/api/media/i1', downloadUrl: '/api/download/i1' }],
      },
    },
    {
      index: 2,
      status: 'done',
      ok: false,
      code: 'PARSE_FAILED',
      message: '该作品无法解析',
      platform: 'douyin',
      ms: 80,
      result: null,
    },
  ],
  ...over,
})

describe('BatchResult', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('进行中显示进度与「完成中」文案', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job({ status: 'running', done: 1, okCount: 1, failCount: 0 })}
        batchRunning
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByText('完成 1/3')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1')
    expect(screen.getByText('正在解析，逐条打勾…')).toBeInTheDocument()
  })

  it('完成后显示成功 / 失败计数', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByText('2 成功 · 1 失败')).toBeInTheDocument()
  })

  it('全部成功时文案为「全部解析完成」', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job({ okCount: 3, failCount: 0 })}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByText('全部解析完成')).toBeInTheDocument()
  })

  it('进度条按比例设置宽度', () => {
    const { container } = render(
      <BatchResult
        entries={ENTRIES}
        batch={job({ done: 2 })}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    const bar = container.querySelector('[role="progressbar"] > div')
    expect(bar).toHaveStyle({ width: '67%' })
  })

  it('逐条渲染序号与平台徽标', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    // 序号 1/2/3
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    // 抖音出现 3 次：第 2/3 条各自的列表头徽标，加上第 2 条结果卡内的徽标
    // （第 3 条解析失败，只有列表头一个）
    expect(screen.getAllByText('抖音')).toHaveLength(3)
    // 快手出现 2 次：第 1 条列表头 + 第 1 条结果卡内的徽标
    expect(screen.getAllByText('快手')).toHaveLength(2)
  })

  it('成功条目渲染结果卡，失败条目只标该条', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByText('成功的视频')).toBeInTheDocument()
    expect(screen.getByText('成功的图集')).toBeInTheDocument()
    // 失败项展示错误信息与原始链接，且不影响其他条目
    expect(screen.getByText('该作品无法解析')).toBeInTheDocument()
    expect(screen.getByText('https://v.douyin.com/c')).toBeInTheDocument()
  })

  it('排队中的条目显示「排队中…」', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job({
          status: 'running',
          done: 1,
          items: [
            { ...job().items[0], status: 'running', ok: null, result: null },
            { ...job().items[1], status: 'pending', ok: null, result: null },
            { ...job().items[2], status: 'pending', ok: null, result: null },
          ],
        })}
        batchRunning
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByText('解析中…')).toBeInTheDocument()
    expect(screen.getAllByText('排队中…')).toHaveLength(2)
  })

  it('批量整体错误显示横幅', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={null}
        batchRunning={false}
        batchError="批量任务不存在或已过期"
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByText('批量任务不存在或已过期')).toBeInTheDocument()
  })

  it('batch 为 null 时用 entries 占位渲染条目数', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={null}
        batchRunning
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '3')
  })

  it('无成功条目时不显示打包按钮', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job({ okCount: 0, failCount: 3 })}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /打包下载 ZIP/ })).not.toBeInTheDocument()
  })

  it('有成功条目且已完成时显示打包按钮', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /打包下载 ZIP/ })).toBeInTheDocument()
  })

  it('点击打包：轮询到 done 后出现下载链接', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'POST' && String(url).endsWith('/zip')) {
        return Promise.resolve(new Response('{}', { status: 200 }))
      }
      // 轮询状态：第一次就返回 done
      return Promise.resolve(
        new Response(JSON.stringify({ pack: { status: 'done', canDownload: true } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /打包下载 ZIP/ }))

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /下载 ZIP（2 条成功媒体）/ })).toHaveAttribute(
        'href',
        '/api/batch/job-1/zip/download',
      )
    }, { timeout: 3000 })
  })

  it('打包失败时展示后端返回的错误', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'POST' && String(url).endsWith('/zip')) {
        return Promise.resolve(new Response('{}', { status: 200 }))
      }
      return Promise.resolve(
        new Response(JSON.stringify({ pack: { status: 'failed', error: '媒体全部抓取失败' } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /打包下载 ZIP/ }))

    await waitFor(() => {
      expect(screen.getByText('媒体全部抓取失败')).toBeInTheDocument()
    }, { timeout: 3000 })
  })

  it('打包触发接口非 2xx 时给出提示', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ message: '任务已过期' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    render(
      <BatchResult
        entries={ENTRIES}
        batch={job()}
        batchRunning={false}
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /打包下载 ZIP/ }))

    await waitFor(() => {
      expect(screen.getByText('任务已过期')).toBeInTheDocument()
    }, { timeout: 3000 })
  })

  it('进行中不显示打包区（避免拿到不完整结果）', () => {
    render(
      <BatchResult
        entries={ENTRIES}
        batch={job({ status: 'running', done: 1, okCount: 1 })}
        batchRunning
        batchError=""
        matchers={MATCHERS}
        onToast={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /打包下载 ZIP/ })).not.toBeInTheDocument()
  })
})
