/**
 * Service Worker 注册。
 *
 * 设计取舍：
 * - 仅在生产构建注册：dev server 下 SW 会干扰 HMR 且缓存旧模块，得不偿失。
 * - 等 window.load 之后再注册：不抢占首屏关键路径的带宽与主线程。
 * - 静默失败：注册失败（隐身模式、旧浏览器、不支持）不影响任何正常功能。
 */
export function registerServiceWorker() {
  if (typeof window === 'undefined') return
  if (!('serviceWorker' in navigator)) return
  // dev 环境不注册，避免缓存干扰热更新
  if (!import.meta.env.PROD) return

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
      /* 隐身模式 / 策略限制：忽略，站点功能不受影响 */
    })
  })
}
