import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'

/** 构造一个受控的 fetch 替身，按 URL 分派响应 */
function mockFetch(routes: Record<string, () => Response | Promise<Response>>) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method || 'GET').toUpperCase()
    for (const [key, handler] of Object.entries(routes)) {
      const [m, path] = key.split(' ')
      if (method === m && url.includes(path)) return Promise.resolve(handler())
    }
    return Promise.resolve(new Response('{}', { status: 404 }))
  })
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const PLATFORMS = {
  ok: true,
  platforms: [
    { key: 'kuaishou', name: '快手', hosts: ['v.kuaishou.com'], kinds: ['video', 'images'] },
    { key: 'douyin', name: '抖音', hosts: ['v.douyin.com'], kinds: ['video'] },
  ],
}

const VIDEO_OK = {
  ok: true,
  type: 'video',
  title: '解析出的视频',
  author: '作者',
  coverUrl: null,
  videoUrl: '/api/media/v',
  downloadUrl: '/api/download/v',
  variants: [],
  images: [],
}

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('首屏渲染标题、输入框与主按钮', async () => {
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json(PLATFORMS) }))
    render(<App />)
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(screen.getByLabelText('分享链接或口令')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /开始解析/ })).toBeInTheDocument()
  })

  it('从后端加载平台清单并展示支持列表', async () => {
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json(PLATFORMS) }))
    render(<App />)
    await waitFor(() => {
      expect(screen.getByText(/支持 快手 · 抖音/)).toBeInTheDocument()
    })
  })

  it('平台清单请求失败时仍可正常使用（只隐藏徽标）', async () => {
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json({}, 500) }))
    render(<App />)
    expect(screen.getByRole('button', { name: /开始解析/ })).toBeInTheDocument()
    // 引导卡回落为通用文案
    expect(screen.getByText(/在 App 里点作品分享/)).toBeInTheDocument()
  })

  it('空输入提交提示「请粘贴分享链接」', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json(PLATFORMS) }))
    render(<App />)

    await user.click(screen.getByRole('button', { name: /开始解析/ }))
    expect(await screen.findByText('请粘贴分享链接')).toBeInTheDocument()
  })

  it('单条链接走单条解析并渲染结果', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockFetch({
        'GET /api/platforms': () => json(PLATFORMS),
        'POST /api/parse': () => json(VIDEO_OK),
      }),
    )
    render(<App />)

    await user.type(screen.getByLabelText('分享链接或口令'), 'https://v.kuaishou.com/x')
    await user.click(screen.getByRole('button', { name: /开始解析/ }))

    expect(await screen.findByText('解析出的视频')).toBeInTheDocument()
  })

  it('解析失败时展示后端错误信息与重试入口', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockFetch({
        'GET /api/platforms': () => json(PLATFORMS),
        'POST /api/parse': () => json({ ok: false, code: 'PARSE_FAILED', message: '该链接无法解析' }),
      }),
    )
    render(<App />)

    await user.type(screen.getByLabelText('分享链接或口令'), 'https://v.kuaishou.com/bad')
    await user.click(screen.getByRole('button', { name: /开始解析/ }))

    expect(await screen.findByText('该链接无法解析')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重试一次/ })).toBeInTheDocument()
  })

  it('解析成功后写入「最近解析」，刷新后可从记录重新解析', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockFetch({
        'GET /api/platforms': () => json(PLATFORMS),
        'POST /api/parse': () => json(VIDEO_OK),
      }),
    )
    const { unmount } = render(<App />)

    await user.type(screen.getByLabelText('分享链接或口令'), 'https://v.kuaishou.com/kept')
    await user.click(screen.getByRole('button', { name: /开始解析/ }))
    await screen.findByText('解析出的视频')

    const stored = JSON.parse(window.localStorage.getItem('vd:recent') || '[]')
    expect(stored).toHaveLength(1)
    expect(stored[0].input).toBe('https://v.kuaishou.com/kept')
    expect(stored[0].platform).toBe('快手')

    // 重新挂载模拟刷新：空态应展示最近记录
    unmount()
    render(<App />)
    expect(await screen.findByText('最近解析')).toBeInTheDocument()
    expect(screen.getByText('解析出的视频')).toBeInTheDocument()
  })

  it('多行输入走批量解析，提交后轮询到完成', async () => {
    const user = userEvent.setup()
    const jobPayload = {
      ok: true,
      job: {
        id: 'job-x',
        status: 'done',
        total: 2,
        done: 2,
        okCount: 2,
        failCount: 0,
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
            ms: 10,
            result: VIDEO_OK,
          },
          {
            index: 1,
            status: 'done',
            ok: true,
            code: '',
            message: '',
            platform: 'douyin',
            ms: 12,
            result: { ...VIDEO_OK, title: '第二个视频' },
          },
        ],
      },
    }
    const fetchMock = mockFetch({
      'GET /api/platforms': () => json(PLATFORMS),
      'POST /api/batch': () => json({ ok: true, jobId: 'job-x' }),
      'GET /api/batch': () => json(jobPayload),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await user.type(
      screen.getByLabelText('分享链接或口令'),
      'https://v.kuaishou.com/a{enter}https://v.douyin.com/b',
    )
    await user.click(screen.getByRole('button', { name: /开始解析/ }))

    expect(await screen.findByText('批量解析')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('2 成功 · 0 失败')).toBeInTheDocument()
    })
    expect(screen.getByText('解析出的视频')).toBeInTheDocument()
    expect(screen.getByText('第二个视频')).toBeInTheDocument()
  })

  it('批量提交失败时展示错误横幅', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockFetch({
        'GET /api/platforms': () => json(PLATFORMS),
        'POST /api/batch': () => json({ ok: false, message: '批量提交失败' }),
      }),
    )
    render(<App />)

    await user.type(
      screen.getByLabelText('分享链接或口令'),
      'https://v.kuaishou.com/a{enter}https://v.douyin.com/b',
    )
    await user.click(screen.getByRole('button', { name: /开始解析/ }))

    expect(await screen.findByText('批量提交失败')).toBeInTheDocument()
  })

  it('Ctrl+Enter 触发解析', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockFetch({
        'GET /api/platforms': () => json(PLATFORMS),
        'POST /api/parse': () => json(VIDEO_OK),
      }),
    )
    render(<App />)

    const input = screen.getByLabelText('分享链接或口令')
    await user.type(input, 'https://v.kuaishou.com/x')
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(await screen.findByText('解析出的视频')).toBeInTheDocument()
  })

  it('「清空」按钮清空输入框', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json(PLATFORMS) }))
    render(<App />)

    const input = screen.getByLabelText('分享链接或口令') as HTMLTextAreaElement
    await user.type(input, '一些内容')
    expect(input.value).toBe('一些内容')

    await user.click(screen.getByRole('button', { name: /清空/ }))
    expect(input.value).toBe('')
  })

  it('清空最近记录后空态回落到引导卡', async () => {
    // 预置一条记录，进入页面即为空态（有记录 → 展示「最近解析」）
    window.localStorage.setItem(
      'vd:recent',
      JSON.stringify([
        {
          input: 'https://v.kuaishou.com/old',
          title: '旧记录',
          platform: '快手',
          kind: 'video',
          ts: Date.now(),
        },
      ]),
    )
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json(PLATFORMS) }))
    render(<App />)

    expect(await screen.findByText('最近解析')).toBeInTheDocument()
    expect(screen.getByText('旧记录')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '清空' }))

    await waitFor(() => {
      expect(screen.queryByText('最近解析')).not.toBeInTheDocument()
    })
    // 记录清空后落盘为空数组
    expect(window.localStorage.getItem('vd:recent')).toBe('[]')
    // 引导卡仍在
    expect(screen.getByText('三步搞定')).toBeInTheDocument()
  })

  it('主题切换按钮存在且可点击', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockFetch({ 'GET /api/platforms': () => json(PLATFORMS) }))
    render(<App />)

    const toggle = screen.getByRole('button', { name: /^当前/ })
    expect(toggle).toBeInTheDocument()
    await user.click(toggle)
    expect(window.localStorage.getItem('vd:theme')).toBe('light')
  })

  it('解析中禁用主按钮，避免重复提交', async () => {
    const user = userEvent.setup()
    // 用一个手动可控的 Promise 卡住解析请求，观察请求挂起期间的状态
    let release = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/platforms')) return Promise.resolve(json(PLATFORMS))
        return gate.then(() => json(VIDEO_OK))
      }),
    )
    render(<App />)

    await user.type(screen.getByLabelText('分享链接或口令'), 'https://v.kuaishou.com/x')
    await user.click(screen.getByRole('button', { name: /开始解析/ }))

    // 请求挂起期间按钮应为禁用且显示「解析中…」
    const busyBtn = await screen.findByRole('button', { name: /解析中/ })
    expect(busyBtn).toBeDisabled()

    release()
    await screen.findByText('解析出的视频')
  })
})
