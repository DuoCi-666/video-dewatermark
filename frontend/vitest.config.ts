import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

/**
 * 测试专用配置（与 vite.config.ts 分离）。
 *
 * 刻意不引入 tailwindcss 插件：组件测试只关心 DOM 结构与行为，
 * 不需要真正生成样式，少一个插件也少一处构建期的不确定性。
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.{test,spec}.{ts,tsx}',
        'src/test/**',
        'src/main.tsx',
        'src/types.ts',
      ],
    },
  },
})
