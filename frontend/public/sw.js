/**
 * Service Worker —— 让站点可安装、可离线打开外壳。
 *
 * 缓存策略（按请求性质区分，宁可不缓存也不缓存错的）：
 *   1. /api/**             → 直接走网络，绝不缓存
 *      （解析结果、媒体流、批量任务全是实时数据；媒体还是流式大文件，
 *        进缓存会迅速撑爆配额且毫无意义）
 *   2. 构建产物（带 hash）→ cache-first
 *      （/assets/*.js|css 文件名含内容 hash，内容变了文件名就变，可安全常驻）
 *   3. 导航请求（HTML）   → network-first + 离线回退
 *      （保证用户总是拿到最新版本；断网时回退缓存的外壳）
 *   4. 其他同源静态资源   → stale-while-revalidate
 *
 * 版本升级：改动 CACHE_VERSION 会让旧缓存整体作废并清理。
 */

const CACHE_VERSION = 'v1'
const SHELL_CACHE = `vd-shell-${CACHE_VERSION}`
const ASSET_CACHE = `vd-assets-${CACHE_VERSION}`

/** 离线外壳：断网时至少能打开界面（内容由后端实时提供，不预先缓存） */
const SHELL_URLS = ['/', '/manifest.webmanifest', '/favicon.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE)
      // 单个资源失败不应让整个 SW 安装失败
      await Promise.allSettled(SHELL_URLS.map((url) => cache.add(new Request(url, { cache: 'reload' }))))
      await self.skipWaiting()
    })(),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // 清理上一版本的缓存
      const keys = await caches.keys()
      await Promise.all(
        keys
          .filter((key) => key.startsWith('vd-') && key !== SHELL_CACHE && key !== ASSET_CACHE)
          .map((key) => caches.delete(key)),
      )
      await self.clients.claim()
    })(),
  )
})

/** 是否为带内容 hash 的构建产物（可长期缓存） */
function isHashedAsset(url) {
  return url.pathname.startsWith('/assets/') && /-[A-Za-z0-9_]{8,}\.(js|css)$/.test(url.pathname)
}

self.addEventListener('fetch', (event) => {
  const { request } = event

  // 只处理 GET；跨域请求一律放行（媒体 CDN、外部资源交给浏览器默认行为）
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  // 1. 接口：绝不缓存
  if (url.pathname.startsWith('/api/')) return

  // 2. 构建产物：cache-first
  if (isHashedAsset(url)) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(ASSET_CACHE)
        const hit = await cache.match(request)
        if (hit) return hit
        const response = await fetch(request)
        if (response.ok) cache.put(request, response.clone())
        return response
      })(),
    )
    return
  }

  // 3. 页面导航：network-first，断网回退外壳
  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request)
          if (response.ok) {
            const cache = await caches.open(SHELL_CACHE)
            cache.put('/', response.clone())
          }
          return response
        } catch {
          const cache = await caches.open(SHELL_CACHE)
          const shell = (await cache.match('/')) || (await cache.match(request))
          return (
            shell ||
            new Response('离线状态，请联网后重试。', {
              status: 503,
              headers: { 'Content-Type': 'text/plain; charset=utf-8' },
            })
          )
        }
      })(),
    )
    return
  }

  // 4. 其他同源静态资源：stale-while-revalidate
  event.respondWith(
    (async () => {
      const cache = await caches.open(SHELL_CACHE)
      const hit = await cache.match(request)
      const network = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(request, response.clone())
          return response
        })
        .catch(() => hit)
      return hit || network
    })(),
  )
})
