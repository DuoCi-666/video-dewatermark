import { useState } from 'react'
import { Download, Image as ImageIcon, Video } from 'lucide-react'
import { Lightbox } from './Lightbox'
import { CARD, PlatformBadge, PRIMARY_BUTTON, SECONDARY_BUTTON } from './ui'
import { matchPlatform } from '../utils'
import type { Matcher, ParseOk } from '../types'

/** 单条解析结果卡：视频播放 + 清晰度切换，或图片网格 + 单图放大 */
export function ResultCard({
  result,
  sourceText,
  matchers,
  onToast,
}: {
  result: ParseOk
  sourceText: string
  matchers: Matcher[]
  onToast: (message: string) => void
}) {
  const variants = result.variants ?? []
  const [variantIdx, setVariantIdx] = useState(0)
  const [zoom, setZoom] = useState<number | null>(null)
  const active = variants[variantIdx]
  const videoSrc = active?.mediaUrl ?? result.videoUrl
  const videoDownload = active?.downloadUrl ?? result.downloadUrl
  const platform = matchPlatform(sourceText, matchers)

  return (
    <section className={`${CARD} animate-fade-up space-y-5 p-4 sm:p-5`}>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {platform ? <PlatformBadge name={platform.name} /> : null}
          <span className="inline-flex items-center gap-1 rounded-full bg-[var(--surface)] px-2 py-0.5 text-xs font-medium text-[var(--muted)]">
            {result.type === 'video' ? (
              <>
                <Video className="h-3.5 w-3.5" aria-hidden="true" />
                视频
              </>
            ) : (
              <>
                <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />
                图文 · {result.images.length} 图
              </>
            )}
          </span>
        </div>
        <div>
          <h2 className="line-clamp-3 text-lg font-bold leading-7">{result.title}</h2>
          <p className="mt-1 text-sm text-[var(--muted)]">{result.author}</p>
          {platform?.key === 'jimeng' ? (
            /* 即梦原片自带片尾品牌角标，烧录在编码里无法去除，按平台如实标注 */
            <p className="mt-1.5 inline-flex items-center gap-1 rounded border border-[var(--subtle)] bg-[var(--surface)] px-1.5 py-0.5 font-mono text-xs text-[var(--muted)]">
              原片含片尾标识
            </p>
          ) : null}
        </div>
      </div>

      {result.type === 'video' && videoSrc ? (
        <div className="space-y-4">
          <video
            className="aspect-video w-full rounded-xl border border-[var(--card-border)] bg-black"
            src={videoSrc}
            poster={result.coverUrl || undefined}
            controls
            playsInline
            preload="metadata"
          />
          {/* 清晰度切换：多档位时显示，预览与下载跟随选中档位 */}
          {variants.length > 1 ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-bold">清晰度</span>
              {variants.map((variant, index) => (
                <button
                  key={`${variant.label}-${index}`}
                  type="button"
                  onClick={() => setVariantIdx(index)}
                  aria-pressed={index === variantIdx}
                  className={
                    (index === variantIdx
                      ? 'bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] text-white shadow-[0_6px_18px_-6px_var(--glow)]'
                      : 'bg-[var(--card)] text-[var(--fg)] hover:bg-[var(--surface)]') +
                    ' inline-flex min-h-11 cursor-pointer items-center rounded-xl border border-[var(--card-border)] px-3.5 text-sm font-medium transition-all duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]'
                  }
                >
                  {variant.label}
                </button>
              ))}
            </div>
          ) : null}

          {videoDownload ? (
            /* 结果页第一主操作：全宽大按钮落在拇指热区，长档位标签省略号截断不撑破按钮 */
            <a
              href={videoDownload}
              onClick={() => onToast('开始下载，请留意浏览器下载提示')}
              className={`${PRIMARY_BUTTON} min-h-[52px] w-full px-4 text-base`}
            >
              <Download className="h-5 w-5 shrink-0" aria-hidden="true" />
              <span className="truncate">
                下载无水印视频
                {active ? <span className="font-mono text-sm opacity-80"> · {active.label}</span> : null}
              </span>
            </a>
          ) : null}
        </div>
      ) : null}

      {result.type === 'images' ? (
        result.images.length === 1 ? (
          /* 单图帖：全宽完整展示（contain 不裁切），点击看原图 */
          <figure className="space-y-2">
            <button
              type="button"
              onClick={() => setZoom(0)}
              aria-label="放大查看图片"
              className="block w-full cursor-zoom-in rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
            >
              <img
                src={result.images[0].previewUrl}
                alt={result.title}
                loading="lazy"
                className="mx-auto max-h-[60vh] w-full rounded-xl min-[640px]:max-h-[70vh] border border-[var(--card-border)] bg-[var(--surface)] object-contain transition hover:opacity-95"
              />
            </button>
            <a
              href={result.images[0].downloadUrl}
              onClick={() => onToast('开始保存原图')}
              className={`${SECONDARY_BUTTON} min-h-11 w-full px-2 text-sm`}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              保存原图
            </a>
          </figure>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {result.images.map((image, index) => (
              <figure key={image.previewUrl} className="space-y-2">
                <button
                  type="button"
                  onClick={() => setZoom(index)}
                  aria-label={`放大查看第 ${index + 1} 张图片`}
                  className="block w-full cursor-zoom-in rounded-xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
                >
                  <img
                    src={image.previewUrl}
                    alt={`${result.title} ${index + 1}`}
                    loading="lazy"
                    className="aspect-[3/4] w-full rounded-xl border border-[var(--card-border)] bg-[var(--surface)] object-cover transition hover:opacity-95"
                  />
                </button>
                <a
                  href={image.downloadUrl}
                  onClick={() => onToast(`开始保存第 ${index + 1} 张`)}
                  className={`${SECONDARY_BUTTON} min-h-11 w-full px-2 text-sm`}
                >
                  <Download className="h-4 w-4" aria-hidden="true" />
                  保存图片 {index + 1}
                </a>
              </figure>
            ))}
          </div>
        )
      ) : null}

      {zoom !== null && result.images[zoom] ? (
        <Lightbox
          src={result.images[zoom].downloadUrl}
          alt={`${result.title} ${zoom + 1}`}
          onClose={() => setZoom(null)}
        />
      ) : null}
    </section>
  )
}
