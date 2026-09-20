import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ResultCard } from '../components/ResultCard'
import { Guide, ErrorAlert, ResultSkeleton } from '../components/Guide'
import { RecentList } from '../components/RecentList'
import { ThemeToggle } from '../components/ThemeToggle'
import { buildMatchers } from '../utils'
import type { Matcher, ParseOk, PlatformInfo, RecentItem } from '../types'

const PLATFORMS: PlatformInfo[] = [
  { key: 'kuaishou', name: '快手', hosts: ['v.kuaishou.com'], kinds: ['video', 'images'] },
  { key: 'douyin', name: '抖音', hosts: ['v.douyin.com'], kinds: ['video'] },
  { key: 'jimeng', name: '即梦AI', hosts: ['jimeng.jianying.com'], kinds: ['video'] },
]
const MATCHERS: Matcher[] = buildMatchers(PLATFORMS)

const videoResult = (over: Partial<ParseOk> = {}): ParseOk => ({
  ok: true,
  type: 'video',
  title: '测试视频',
  author: '测试作者',
  coverUrl: null,
  videoUrl: '/api/media/default',
  downloadUrl: '/api/download/default',
  variants: [],
  images: [],
  ...over,
})

const imagesResult = (count: number): ParseOk => ({
  ok: true,
  type: 'images',
  title: '测试图集',
  author: '图集作者',
  coverUrl: null,
  videoUrl: null,
  downloadUrl: null,
  images: Array.from({ length: count }, (_, i) => ({
    previewUrl: `/api/media/img-${i}`,
    downloadUrl: `/api/download/img-${i}`,
  })),
})

describe('ResultCard', () => {
  const onToast = vi.fn()

  it('渲染标题与作者', () => {
    render(
      <ResultCard
        result={videoResult()}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.getByText('测试视频')).toBeInTheDocument()
    expect(screen.getByText('测试作者')).toBeInTheDocument()
  })

  it('按来源链接识别平台并显示徽标', () => {
    render(
      <ResultCard
        result={videoResult()}
        sourceText="https://v.douyin.com/abc"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.getByText('抖音')).toBeInTheDocument()
  })

  it('来源无法识别时不显示平台徽标', () => {
    render(
      <ResultCard
        result={videoResult()}
        sourceText="https://unknown.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.queryByText('抖音')).not.toBeInTheDocument()
    expect(screen.getByText('视频')).toBeInTheDocument()
  })

  it('视频结果渲染 video 元素与下载按钮', () => {
    const { container } = render(
      <ResultCard
        result={videoResult()}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(container.querySelector('video')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /下载无水印视频/ })).toHaveAttribute(
      'href',
      '/api/download/default',
    )
  })

  it('无下载链接时不渲染下载按钮', () => {
    render(
      <ResultCard
        result={videoResult({ downloadUrl: null, videoUrl: '/api/media/x' })}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.queryByRole('link', { name: /下载无水印视频/ })).not.toBeInTheDocument()
  })

  it('多清晰度时渲染切换按钮，默认选中第一档', async () => {
    render(
      <ResultCard
        result={videoResult({
          variants: [
            { label: '高清', mediaUrl: '/api/media/hd', downloadUrl: '/api/download/hd' },
            { label: '标清', mediaUrl: '/api/media/sd', downloadUrl: '/api/download/sd' },
          ],
        })}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    const hd = screen.getByRole('button', { name: '高清' })
    expect(hd).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '标清' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('切换清晰度后预览源与下载链接同步变化', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <ResultCard
        result={videoResult({
          variants: [
            { label: '高清', mediaUrl: '/api/media/hd', downloadUrl: '/api/download/hd' },
            { label: '标清', mediaUrl: '/api/media/sd', downloadUrl: '/api/download/sd' },
          ],
        })}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(container.querySelector('video')).toHaveAttribute('src', '/api/media/hd')

    await user.click(screen.getByRole('button', { name: '标清' }))

    expect(container.querySelector('video')).toHaveAttribute('src', '/api/media/sd')
    expect(screen.getByRole('link', { name: /下载无水印视频/ })).toHaveAttribute(
      'href',
      '/api/download/sd',
    )
  })

  it('只有一档清晰度时不渲染切换按钮', () => {
    render(
      <ResultCard
        result={videoResult({
          variants: [{ label: '高清', mediaUrl: '/a', downloadUrl: '/b' }],
        })}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.queryByText('清晰度')).not.toBeInTheDocument()
  })

  it('即梦AI 结果标注「原片含片尾标识」', () => {
    render(
      <ResultCard
        result={videoResult()}
        sourceText="https://jimeng.jianying.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.getByText('原片含片尾标识')).toBeInTheDocument()
  })

  it('其他平台不显示即梦的标注', () => {
    render(
      <ResultCard
        result={videoResult()}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.queryByText('原片含片尾标识')).not.toBeInTheDocument()
  })

  it('多图结果渲染网格与逐张保存按钮', () => {
    render(
      <ResultCard
        result={imagesResult(3)}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.getByText(/图文 · 3 图/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '保存图片 1' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '保存图片 3' })).toBeInTheDocument()
  })

  it('单图结果展示「保存原图」而非网格', () => {
    render(
      <ResultCard
        result={imagesResult(1)}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.getByRole('link', { name: '保存原图' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /保存图片 1/ })).not.toBeInTheDocument()
  })

  it('点击图片打开灯箱，Esc 关闭', async () => {
    const user = userEvent.setup()
    render(
      <ResultCard
        result={imagesResult(3)}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '放大查看第 2 张图片' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('点击灯箱遮罩关闭预览', async () => {
    const user = userEvent.setup()
    render(
      <ResultCard
        result={imagesResult(2)}
        sourceText="https://v.kuaishou.com/x"
        matchers={MATCHERS}
        onToast={onToast}
      />,
    )
    await user.click(screen.getByRole('button', { name: '放大查看第 1 张图片' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('dialog'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('Guide', () => {
  it('渲染三步引导', () => {
    render(<Guide platforms={PLATFORMS} />)
    expect(screen.getByText('三步搞定')).toBeInTheDocument()
    expect(screen.getByText(/打开快手/)).toBeInTheDocument()
  })

  it('无平台清单时给出通用文案，不出现空列表', () => {
    render(<Guide platforms={[]} />)
    expect(screen.getByText(/在 App 里点作品分享/)).toBeInTheDocument()
  })
})

describe('ErrorAlert', () => {
  it('渲染错误信息与常见原因引导', () => {
    render(<ErrorAlert message="解析失败" />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('解析失败')).toBeInTheDocument()
    expect(screen.getByText(/常见原因/)).toBeInTheDocument()
  })

  it('传入 onRetry 时才渲染重试按钮，点击会回调', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    const { rerender } = render(<ErrorAlert message="失败" />)
    expect(screen.queryByRole('button', { name: /重试一次/ })).not.toBeInTheDocument()

    rerender(<ErrorAlert message="失败" onRetry={onRetry} />)
    await user.click(screen.getByRole('button', { name: /重试一次/ }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('ResultSkeleton', () => {
  it('对辅助技术隐藏（纯装饰）', () => {
    const { container } = render(<ResultSkeleton />)
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true')
  })
})

describe('RecentList', () => {
  const items: RecentItem[] = [
    { input: 'https://v.douyin.com/a', title: '第一个', platform: '抖音', kind: 'video', ts: Date.now() },
    { input: 'https://v.kuaishou.com/b', title: '第二个', platform: '快手', kind: 'images', ts: Date.now() },
  ]

  it('渲染记录条目与平台徽标', () => {
    render(<RecentList items={items} onPick={vi.fn()} onClear={vi.fn()} />)
    expect(screen.getByText('最近解析')).toBeInTheDocument()
    expect(screen.getByText('第一个')).toBeInTheDocument()
    expect(screen.getByText('抖音')).toBeInTheDocument()
  })

  it('标题缺失时回退展示原始链接', () => {
    render(
      <RecentList
        items={[{ ...items[0], title: '' }]}
        onPick={vi.fn()}
        onClear={vi.fn()}
      />,
    )
    expect(screen.getByText('https://v.douyin.com/a')).toBeInTheDocument()
  })

  it('点击条目回传原始输入', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<RecentList items={items} onPick={onPick} onClear={vi.fn()} />)
    await user.click(screen.getByText('第一个'))
    expect(onPick).toHaveBeenCalledWith('https://v.douyin.com/a')
  })

  it('点击清空触发回调', async () => {
    const user = userEvent.setup()
    const onClear = vi.fn()
    render(<RecentList items={items} onPick={vi.fn()} onClear={onClear} />)
    await user.click(screen.getByRole('button', { name: '清空' }))
    expect(onClear).toHaveBeenCalledTimes(1)
  })
})

describe('ThemeToggle', () => {
  it('跟随系统时显示「当前跟随系统」', () => {
    render(<ThemeToggle pref="system" theme="light" onCycle={vi.fn()} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-label', '当前跟随系统')
  })

  it('深色时显示「当前深色」', () => {
    render(<ThemeToggle pref="dark" theme="dark" onCycle={vi.fn()} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-label', '当前深色')
  })

  it('提示文案说明下一次会切成什么', () => {
    render(<ThemeToggle pref="light" theme="light" onCycle={vi.fn()} />)
    expect(screen.getByRole('button').getAttribute('title')).toContain('切换为深色')
  })

  it('点击触发切换回调', async () => {
    const user = userEvent.setup()
    const onCycle = vi.fn()
    render(<ThemeToggle pref="system" theme="light" onCycle={onCycle} />)
    await user.click(screen.getByRole('button'))
    expect(onCycle).toHaveBeenCalledTimes(1)
  })
})
