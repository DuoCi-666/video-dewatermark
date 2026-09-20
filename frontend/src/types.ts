export type ImageItem = {
  previewUrl: string
  downloadUrl: string
}

export type VariantItem = {
  label: string
  mediaUrl: string
  downloadUrl: string
}

export type ParseOk = {
  ok: true
  type: 'video' | 'images'
  title: string
  author: string
  coverUrl: string | null
  videoUrl: string | null
  downloadUrl: string | null
  variants?: VariantItem[]
  images: ImageItem[]
}

export type ParseErr = {
  ok: false
  code: string
  message: string
}

export type BatchItemDto = {
  index: number
  status: 'pending' | 'running' | 'done'
  ok: boolean | null
  code: string
  message: string
  platform: string
  ms: number
  result: ParseOk | null
}

export type BatchJob = {
  id: string
  status: 'running' | 'done'
  total: number
  done: number
  okCount: number
  failCount: number
  createdAt: string
  updatedAt: string
  items: BatchItemDto[]
}

export type Status = 'idle' | 'loading' | 'success' | 'error'

export type PlatformInfo = {
  key: string
  name: string
  hosts: string[]
  kinds: string[]
}

export type Matcher = { platform: PlatformInfo; pattern: RegExp }

export type RecentItem = {
  input: string
  title: string
  platform: string
  kind: 'video' | 'images'
  ts: number
}