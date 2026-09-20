<div align="center">

# 🎬 video-dewatermark

**聚合视频去水印解析工具站 — 快手 · 抖音 · 小红书 · 豆包 · B站 · 腾讯频道 · 米游社 · 微博 · 即梦AI · 皮皮虾 · 皮皮搞笑 · 最右 · 今日头条 · 微视 · 央视网 · A站 · 腾讯视频 · 搜狐视频 · 梨视频 · 虎牙 · 知乎 · 好看视频**

_粘贴分享链接 → 一键解析 → 在线预览 → 下载无水印原片与原图_

`FastAPI` · `React 19` · `Vite 8` · `Tailwind CSS 4` · `httpx` · `curl_cffi`

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

</div>

---

## ✨ 功能特性

- **二十二平台支持** —— 快手（视频 / 图集）、抖音（视频 / 图集）、小红书（图文 / 视频笔记）、豆包（AI 图文 / 视频）、B站（视频 / 图文专栏）、腾讯频道（视频）、米游社（视频 / 图文）、微博（视频 / 图集）、即梦AI（AI 视频）、皮皮虾（视频 / 图集）、皮皮搞笑（视频 / 图集）、最右（视频 / 图集）、今日头条（微头条图文）、微视（视频）、央视网（视频）、A站（视频）、腾讯视频（视频）、搜狐视频（视频）、梨视频（视频）、虎牙（视频）、知乎（视频）、好看视频（视频），整段分享口令直接粘贴，自动识别平台
- **无水印下载** —— 视频取无水印原片直链（抖音 `playwm → play`），图片取高清原图
- **多清晰度选择** —— 自动解析全部可用档位（如高清 / 标清），预览与下载跟随切换，每档位内置多 CDN 候选
- **在线预览** —— 浏览器直接播放与查看，支持拖动进度条（HTTP Range / 206 断点拉流）
- **隐私代理** —— 所有媒体请求经本站代理转发，浏览器不直连平台 CDN；服务端不保存任何作品
- **HLS 转封装** —— A站、央视网长节目等只提供 HLS（m3u8）源的平台，由媒体代理调用 ffmpeg 转封装成 mp4（`-c copy`，不重编码）；预览首次流式保证快速起播、同时后台转存完整 mp4（`+faststart`），转完后即可**拖动进度条**；下载则等待转存完成、拿到完整 mp4。可用 `HLS_TRANSCODE=0` 关闭
- **健壮性设计** —— 多 CDN 自动 fallback、60s 解析缓存（同链接去重）、媒体 token 落盘（2 小时有效，访问自动续期，重启不丢）、平台风控页识别、解析失败自动重试、短时效签名直链失效后自动重新解析续期
- **移动优先 UI** —— 全宽拇指区 CTA、safe-area 适配、一键粘贴、Neubrutalism 工具风
- **暗色模式** —— 浅色 / 深色 / 跟随系统三态循环切换，偏好持久化；首屏由内联脚本预置主题，刷新无闪白
- **可安装 PWA** —— 手机浏览器「添加到桌面」后像 App 一样全屏打开；Service Worker 缓存外壳与构建产物，断网也能打开界面（解析接口永不缓存，保证结果实时）
- **批量解析** —— 一次粘贴多行链接，后台有界并发逐个解析（复用单条解析路径，全平台自动覆盖），前端进度条 + 逐条结果卡片；单条失败只标该项，不影响其他条目
- **一键打包下载 ZIP** —— 批量解析完成后把成功条目的主媒体（视频默认档 + 图片全原图）后台有界并发抓取、`ZIP_STORED` 打包成一键下载的 zip；逐条拉取失败只跳过该文件、候选重试 + 短签名自动刷新，单条 / 整体体积与保留时限硬限制防撑爆资源
- **接口防护** —— nginx 频率 / 连接限制 + 应用层按 IP 令牌桶与并发上限，统一结构化错误，异常不泄漏内部信息（批量接口走独立的批量配额，不挤占单条用户）
- **主页链接识别** —— 快手主页分享（典型：快手极速版口令的 `v.kuaishou.com` 短链 302 到 `/fw/user/<encId>`）落点是作者主页而非作品页，主页作品列表接口需客户端 sig4 加签、桌面版 graphql 也要过验证码，服务端拿不到单一作品；解析器按落地 URL 提前识别，返回 `PROFILE_LINK` 与可执行引导（点开具体作品再分享），不笼统报「解析失败」，失败样本定性为 `input` 便于统计
- **抖音原生详情兜底** —— 分享页未内嵌作品数据时，可配置部署者自己的登录 Cookie（`DOUYIN_COOKIE`）改走抖音 web 详情接口（内置自实现的 `a_bogus` 签名算法）兜底解析；未配置时该路径不启用，行为不变
- **调用统计** —— 按「天 × 平台」记录每次真实解析的调用量、成功率与耗时（平均 / P95），巡检流量单独归类互不污染
- **失败样本流水** —— 每一例解析失败都留档（链接 / 原始输入 / 错误码 / 耗时 + 定性分类），修好后自动移出，形成"查看 → 复现 → 修复 → 看统计回升"的闭环
- **一键诊断报告** —— `/api/stats/report` 把「统计 + 失败样本」合成一份可直接投喂 AI 的文本，一次拿全复盘上下文
- **成功样本池** —— 用户成功解析过的链接自动沉淀为巡检候选（入库前剥离分享凭证、只落本机、绝不进仓库），`python3 -m app.samples -o ...` 一键导出清单，省去手工收集巡检样本
- **平台清单单一来源** —— 后端下发平台清单，界面徽标、文案、识别规则全部派生；新增平台只改后端

## 🧭 支持平台

| 平台 | 内容类型 | 解析方式 |
|------|----------|----------|
| 快手 | 视频 / 图集 | 分享页 Apollo JSON，多档位（`hd15` / `_b_`）分组；主页链接（短链 302 到 `/fw/user/` 或 `/profile/`）返回 `PROFILE_LINK` 引导，请分享具体作品 |
| 抖音 | 视频 / 图集 | 分享页 `_ROUTER_DATA` + `play` 接口无水印直链；分享页未内嵌数据且配置了 `DOUYIN_COOKIE` 时，改走 web 详情接口（自实现 `a_bogus` 签名）兜底 |
| 小红书 | 图文 / 视频笔记 | 分享页 `__INITIAL_STATE__`，`imageList` / `h264` 直链 |
| 豆包 | AI 图文 / 视频 | 分享页 data-fn-args JSON 递归提取 + get_video_share_info API（main/backup 双 CDN） |
| B站 | 视频 / 图文专栏 | 短链 `b23.tv` 解析；视频走 view + playurl（多档 MP4）；图文走 `__INITIAL_STATE__` 原图 |
| 腾讯频道 | 视频 | Nuxt 3 SSR 的 `__NUXT_DATA__`（devalue 池）只取分享帖 `feedDetail`；`curl_cffi` 过 EdgeOne 指纹校验 |
| 米游社 | 视频 / 图文 | 帖子 ID 走 `getPostFull`；视频按清晰度分档（含 backup CDN），图文取 `image_list` 原图 |
| 微博 | 视频 / 图集 | `m.weibo.cn/statuses/show`；分享 bid 转十进制 mid，访客 Cookie（`genvisitor`→`incarnate` 拿 SUB/SUBP）绕登录；视频取 `page_info.urls` 多档位，图集取 `pics[].large` 原图 |
| 即梦AI | AI 视频 | 短链 302 取 `published_item_id`；`POST /mweb/v1/get_item_info` 取 `origin_video.video_url` 原视频（片尾烧录角标，如实标注为「原视频」而非「去水印」） |
| 皮皮虾 | 视频 / 图集 | 短链 302 取 `item_id`；分享页 `RENDER_DATA`（URL 编码 JSON）→ `ppxItemDetail.item`；视频取 `video.video_download.url_list` 多 CDN，图集取 `note.multi_image[].url_list` 原图 |
| 皮皮搞笑 | 视频 / 图集 | 分享链取 `pid`；`POST api.ippzone.com/share/fetch_content`（`text/plain` 请求体带 `h_app/h_ts/pid/type`）→ `data.post`；视频取 `videos[].qualities[].urls[]` 多清晰度，图集取 `imgs[].urls.origin.urls[]` 原图 |
| 最右 | 视频 / 图集 | 分享页 SSR 内嵌 `window.APP_INITIAL_STATE`（`share.xiaochuankeji.cn`）；帖子取 `sharePost.postDetail.post`，失效看 `postFailure`；视频取 `videos[].url` 直链，图集取 `imgs[].urls.origin.urls[]` 原图（`video:1` 首帧图跳过） |
| 微视 | 视频 | 短链 `video.weishi.qq.com/<code>` 302 跳到分享页取 `id=<feedid>`；`WSH5GetPlayPage` API → `rsp_body.feeds[0]`；多档位签名直链在 `video_spec_urls`（`haveWatermark=0`），主字段 `video_url` 302 不可用 |
| 今日头条 | 微头条图文 | 短链 `m.toutiao.com/is/<code>` 302 跳到 `/w/<id>/`；页面 SSR 内嵌 `RENDER_DATA`（URL 编码 JSON）→ `articleInfo.thread.threadBase`；标题取 `title`，作者取 `user.info.name`，图集取 `largeImageList[].url`（1280 宽无水印大图） |
| 央视网 | 视频 | 分享页正则 `var guid = "..."` 提取 GUID；`vdn.apps.cntv.cn/api/getHttpVideoInfo.do` 取分片 MP4 直链（新闻片段等短视频为单段原片），MP4 直链为空时回退 `hls_url`（HLS 流，由媒体代理 ffmpeg 转封装为 mp4）；画面台标为节目固有内容、非平台水印 |
| A站 | 视频 | 桌面 UA 请求 PC 页，取内嵌 `window.videoInfo` 的 `currentVideoInfo.ksPlayJson`（JSON 字符串）→ `adaptationSet[].representation[]`；主 url 为 HLS m3u8（多档清晰度 `1080P+` / `1080P` / `720P`…，带 `pkey` 短时效签名），同档位备用 CDN 作 fallback；HLS 源由媒体代理 ffmpeg 转封装为 mp4 |
| 腾讯视频 | 视频 | 从链接提取 `vid`（`/x/page/{vid}.html`、`/x/cover/{cid}/{vid}.html`、`m.v.qq.com` 的 `vid=` 参数）；`vv.video.qq.com/getinfo`（JSONP）取 `vl.vi[0]` 的 `ul.ui[0].url` + `fn` + `?vkey=fvkey` 拼直链（`em=0` 才算成功），封面 `puui.qpic.cn/vpic_cover/{vid}/{vid}_hz.jpg`；专辑/剧集主页返回 `PROFILE_LINK` 引导；长视频正片多为 VIP，仅免费/公开内容可解析 |
| 搜狐视频 | 视频 | 从链接提取数字 `vid`（`/v/{base64}.html` 解码出 `us/{uid}/{vid}.shtml`，或直接 `us/{uid}/{vid}.shtml`）；`api.tv.sohu.com/v4/video/info/{vid}.json`（带固定 `api_key`）取 `data.url_high_mp4` / `download_url` 直链、`video_name`、`originalCutCover`、`user.nickname` |
| 梨视频 | 视频 | 从链接提取 `contId`（`/detail_{id}` 或 `/video_{id}`）；`pearvideo.com/videoStatus.jsp?contId=`（**必须带站内 Referer**）取 `videoInfo.videos.srcUrl`，把其中的 `systemTime` 替换为 `cont-{contId}` 才是无水印可下载地址（原始带时间戳地址 403） |
| 虎牙 | 视频 | 从链接提取 `vid`（`/v.huya.com/{vid}.html`）；`liveapi.huya.com/moment/getMomentContent`（需带站内 Referer）取 `data.moment.videoInfo` 的 `definitions[0].url`（多档位直链，回退 `videoUrl`）、`videoTitle`、`videoCover`、`nickName` |
| 知乎 | 视频 | API `GET /api/v4/zvideos/{id}` 直接返回 JSON，无需 Cookie；`video.playlist` 包含 LD/SD/HD/FHD 多清晰度直链 |
| 好看视频 | 视频 | 从链接提取 `vid`；`haokan.baidu.com/haokan/ui-web/video/detail` JSON 取 `clarityUrl` 多档签名直链（标清 / 超清），PC UA 会被风控、需移动 UA；短签名失效自动重解析 |
>
> 腾讯频道分享页前置了 EdgeOne Bot 防护，且拦截依据是 **TLS 握手指纹（JA3）**：同样的 UA 与出口 IP，
> `httpx` 会被拦成挑战页，`curl_cffi` 用真实浏览器指纹即可正常取页。因此该解析器**不复用共享 httpx 客户端**，
> 按请求使用 `curl_cffi`（版本已锁在 `backend/requirements.txt`）。另外页面里混着"频道推荐流"（也带 mp4），
> 只从 `pinia.feedStore.feedDetail` 取分享的那条，避免下到别人的视频。

> 皮皮虾的分享页是 SSR + 前端 hydration，作品数据在 `<script id="RENDER_DATA" type="application/json">`
> 里，内容是**整段 URL 编码**的 JSON（需先 `unquote` 再 `json.loads`），顶层取 `ppxItemDetail.item`。
> 视频直链在 `video.video_download.url_list`（多 CDN 候选）；图集在 `note.multi_image[].url_list`，
> 注意该字段是 `...noop-v4...` 的**无水印原图**，而 `download_list` 反而是带站点水印的 `...logo...`，
> 故图文一律取 `url_list`。CDN（`*.ppxvod.com` / `*.ppx-sign.byteimg.com`）不校验 Referer，直链带
> 短时效签名，失效后由媒体代理自动重解析换新地址（平台注册为 `signed_media=True`）。

> 皮皮搞笑（`ippzone.com`，最右旗下，App `cn.xiaochuankeji.zuiyouLite`）的分享页是纯前端壳，
> 作品数据走接口 `POST https://api.ippzone.com/share/fetch_content`，`Content-Type: text/plain`，
> 请求体为 JSON 字符串（`h_app=zuiyou_lite` / `h_ts` / `pid` / `type=post` 等），返回 `{"ret":1,"data":{"post":...}}`。
> 视频在 `post.videos[].qualities[].urls[]`（按 `resolution` 分档、多 CDN），图集在 `post.imgs[].urls.origin.urls[]`。
> 图片 CDN（`*.ippzone.com`）与视频 CDN（`*.szsttkj.com`）不校验 Referer。**注意：与「皮皮虾」（`pipix.com`）是两个不同的站。**

> 最右（`share.xiaochuankeji.cn`，App `cn.xiaochuankeji.zuiyou`）是**另一个站**（最右平台本体，
> 皮皮搞笑是它旗下的阉割版 `zuiyouLite`），勿与 `ippzone.com` 混用：两者分享域、接口、媒体 CDN
> 完全不同。最右的分享链形如 `/hybrid/share/post?pid=<pid>`，页面是 SSR，作品数据内嵌在
> `<script id="appState">window.APP_INITIAL_STATE={...}</script>`：帖子主体
> `sharePost.postDetail.post` 的结构与皮皮搞笑 `fetch_content` 返回同源，但封面/图集都在
> `post.imgs[]`（图集块 `urls.origin.urls[]`，`video:1` 戳的是视频首帧、作封面用不进入图集）。
> 视频取 `post.videos[].url` 主直链（无清晰度分档）。媒体直链带短时效签名（注册
> `signed_media=True`，失效自动重解析续期）；CDN（`*.izuiyou.com`）不校验 Referer。

> 今日头条的分享短链（`m.toutiao.com/is/<code>`）经 302 跳到具体页：
> 微头条图文 → `/w/<id>/`，短视频 → `/video/<id>/`。**本解析器只支持微头条图文**：图文页是 SSR，
> 作品数据在 `<script id="RENDER_DATA" type="application/json">`（URL 编码 JSON，需先 `unquote` 再解析），
> 顶层取 `articleInfo.thread.threadBase`；图片取 `largeImageList[].url`（`~tplv-shrink:1280:xxxx.jpeg`，
> 1280 宽无水印大图，签名与 URL 绑定、去掉后缀会 403，故直接用带签名直链）。
> **短视频不支持**：`/video/<id>/` 页在网页端不渲染真实视频（页面主体是与目标视频无关的全站推荐流，
> 且带字节 `byted_acrawler` 反爬签名），点击「立即播放」也不会发起视频请求，只引导「打开 APP」，
> 属于 App 独占内容；对这类链接会明确返回「今日头条短视频仅支持在 App 内观看」。
> 图片 CDN（`*.toutiaoimg.com`）不校验 Referer，直链带短时效签名（注册 `signed_media=True`）。

> 微视（`video.weishi.qq.com`，腾讯旗下，与「腾讯频道」`pd.qq.com` 是两个不同的平台）的分享短链
> 形如 `https://video.weishi.qq.com/dpIibFVG`，经 302 跳到 H5 分享页
> `isee.weishi.qq.com/ws/app-pages/share/index.html?id=<feedid>`（Vue SPA，页面无内嵌数据），
> feedid 从 `?id=` 参数取。数据走 `POST api.weishi.qq.com/trpc.weishi.weishi_h5_proxy.weishi_h5_proxy/
> WSH5GetPlayPage`（请求体带 `feedid/recommendtype/datalvl/_weishi_mapExt`），取
> `rsp_body.feeds[0]`。**视频直链必须用 `video_spec_urls`**（dict，key 为 `"0"`/`"999"`/`"45"`/
> `"46"` 等档位，每项含 `url`/`width`/`height`/`videoQuality`/`videoCoding`/`haveWatermark`）——
> 主字段 `video_url` 会 302 到不可用地址；按 `(height, videoQuality)` 分档成多清晰度
> （高清 / 标准 / 流畅），同档多 CDN 候选，`haveWatermark=1` 的水印档剔除。腾讯的云剪贴板
> 会给真实发布链接，分享短链理论上会过期，若要长期可用需从 `https://weishi.qq.com` 重新生成。
> **CDN 反 Referer**：视频 CDN（`v.weishi.qq.com`）携带 Referer 会返回 302 拒绝，不带 Referer
> 才返回 206 —— 媒体代理取流时不带 Referer；直链带短时效签名（注册 `signed_media=True`，失效自动
> 重新解析续期）。封面 CDN（`xp.qpic.cn`）两种方式都可用。

> 分享口令示例：`【怎么不算夹摇-哔哩哔哩】 https://b23.tv/xxxx` —— 直接整段粘贴即可。
>
> 平台清单的**唯一来源**是 `backend/app/parsers/extract.py` 里的 `PLATFORMS` 注册表。
> 界面的徽标、文案、域名识别规则全部由 `GET /api/platforms` 派生，因此**新增平台只需改这一处**
> （加上对应的 parser），不会再出现"后端已支持但界面没显示"的不一致。

## 🚀 快速开始

### 本地开发

```bash
git clone https://gitee.com/its-liangchen-a/video-dewatermark.git
cd video-dewatermark

# 后端（Python 3.10+）
pip install -r backend/requirements.txt

# 前端（Node 20.19+）
cd frontend && npm install && cd ..

# 一键启动：后端 127.0.0.1:3001 + 前端 dev server 0.0.0.0:5173
./start.sh
```

打开 <http://localhost:5173> 即可使用。

### 运行测试

```bash
# 后端（pytest，438 项）
cd backend && python -m pytest -q

# 前端（vitest，113 项）
cd frontend && npm run test          # 单次运行
cd frontend && npm run test:watch    # 监听模式
cd frontend && npm run test:coverage # 带覆盖率报告
```

前端测试覆盖：工具函数（`utils.ts` 100%）、主题 hook（`useTheme.ts` 100%）、
组件渲染分支（`ResultCard` / `BatchResult` / `RecentList` / `Lightbox` / `Guide`）、
`App` 主流程（单条 / 批量解析、错误处理、最近记录、快捷键）。

### 生产部署（单机实战方案）

<details>
<summary><b>展开：systemd + nginx 部署步骤</b></summary>

```bash
# 1. 后端运行时（任意 Python 3.10+ 环境，此处用 Miniconda 隔离）
bash Miniconda3-py311_23.11.0-2-Linux-x86_64.sh -b -p /opt/miniconda3
/opt/miniconda3/bin/pip install -r backend/requirements.txt

# 2. 后端 systemd 常驻（127.0.0.1:3001，仅本机监听）
cat > /etc/systemd/system/video-dewatermark.service <<'EOF'
[Unit]
Description=Video Dewatermark Backend (FastAPI)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/video-dewatermark/backend
Environment=PYTHONPATH=/opt/video-dewatermark/backend
ExecStart=/opt/miniconda3/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 3001
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl enable --now video-dewatermark

# 3. 前端构建 + nginx 对外（8080）
cd frontend && npm run build && cd ..
# nginx server：root 指向 frontend/dist，/api 反代 http://127.0.0.1:3001
#   建议开启 proxy_buffering off（流式转发媒体）
systemctl reload nginx
```

</details>

### 生产部署（Docker 一键方案）

与上面的 systemd + nginx 方案**并存**：两者共用同一份代码、同一套数据存储接口，且对外端口
也用同一个默认值 `8080`，二选一即可，互不冲突。Docker 适合"想要一条命令起整个站"的场景。

<details>
<summary><b>展开：docker compose 一键部署步骤</b></summary>

```bash
# 1.（可选）复制配置模板并调整（海外机房设代理、对外端口、告警等）
cp docker/.env.example .env

# 2. 一键构建并启动：backend(FastAPI:3001) + frontend(Nginx:8080)
#    首次会自动构建镜像（后端 = python + fastapi，前端 = node 构建 → nginx 托管）
docker compose up -d --build

# 3. 查看状态（backend 健康后才拉起 frontend，依赖自动排序）
docker compose ps
```

- 对外访问：<http://localhost:8080>
- **数据持久化**：`vd_data` 卷保存 `stats / tokens / failures / packs`，重构建/重启不丢。
- **健康检查**：backend 轮询 `/api/health`，通过后 frontend 才启动；两者各自带 docker
  healthcheck，`docker compose ps` 可看 `healthy/unhealthy`。
- **海外出口代理**：解析器按平台走 `<平台大写>_PROXY`，兜底 `PARSE_PROXY`，在 `.env` 里配
  即可，无需改代码（与 systemd 部署的 `proxies.py` 行为一致）。
- 停止：`docker compose down`；停用并删数据卷：`docker compose down -v`（不可恢复）。
</details>

### 一键部署与健康巡检

<details>
<summary><b>展开：日常运维命令</b></summary>

```bash
# 常规部署：拉取 → 装依赖 → 构建前端 → 重启后端 → 健康检查
./ops/deploy.sh

# 强制重新部署当前版本（即使 commit 没变）
./ops/deploy.sh --force

# 部署后顺带跑一次全平台解析巡检
./ops/deploy.sh --deep

# 手动巡检：真实链接（多平台）能否解析 + 媒体代理 Range 是否可用
/opt/miniconda3/bin/python ops/healthcheck.py

# 安装 / 更新定时巡检（默认每 30 分钟一次）
sudo bash ops/install-timer.sh
```

**deploy.sh**：任一步骤失败都不会把坏版本留在线上 —— 脚本会打印失败步骤的输出
（完整日志在 `ops/logs/`），随后自动回到部署前的 commit、重新安装依赖与构建、重启服务。
实测：前端构建失败、后端启动失败两种场景均能在约 30 秒内自愈。

**healthcheck.py**：逐个平台调用 `/api/parse`，校验解析结果、媒体条数，并对
`/api/media/{token}` 发 Range 请求确认 `206` 可达。结果写入 `ops/health_status.json`
（最新状态 + 连续失败次数）与 `ops/health.log`（流水）；任一平台失败时退出码为 1。
在 `/etc/video-dewatermark/health.env` 里填 `ALERT_WEBHOOK`（企业微信 / 钉钉机器人地址）
即可在异常时收到推送。

**成功率趋势（health_trend.py）**：只记「连续失败次数」抓不到缓慢劣化 —— 它可以
在成功后清零，于是「这两天成功率从 98% 掉到 60%」这种渐进风控收紧会被漏掉。
为此每次巡检额外追加一行精简记录到 `ops/health_history.jsonl`（只到平台粒度，
默认保留最近 500 行），据此算出每平台最近 20 次巡检的成功率：

- **间歇退化告警**：本轮**全部通过**、但窗口成功率低于 80% 的平台会单独告警
  （`ALERT_DEGRADE=0` 可关）。这是原有「连续失败」机制抓不到的那类问题；
- **多样本区分故障类型**：同一平台配多条样本链接后，自动区分两种失败 ——
  **全部样本失败** = 解析器 / 平台侧问题（按故障告警）；**仅部分样本失败** =
  那条链接已过期（单独提示"建议更换链接"，**不按故障告警**，不会半夜把人叫起来）。
  报告里的 `sampleCoverage` 区块给出 `allDown` / `partial` / `healthy` 三组结论；
- 报告里的 `trend` 区块给出口径互斥的三个计数：`healthy` / `degraded`（间歇退化）
  / `hardDown`（连续失败，走原有告警），三数相加等于 `evaluated`。

> 巡检链接会失效（短链过期 / 作品删除）。连续失败次数是为此设计的：偶发 1 次多半是抖动，
> 持续失败则要么是解析器坏了，要么是链接该换了 —— 改 `ops/links.json` 即可。

**私有样本（`VD_LINKS_EXTRA`）**：平台分享口令常带 `xsec_token` / `share_id` 等
**分享凭证**，把它写进公开仓库等于公开自己账号的凭证，所以仓库里的 `ops/links.json`
只放公共样本。要补充自己的链接时，另建一份私有清单并在 `health.env` 里指向它：

```bash
# /etc/video-dewatermark/health.env
VD_LINKS_EXTRA=/etc/video-dewatermark/my-links.json
```

```jsonc
// /etc/video-dewatermark/my-links.json —— 格式与 ops/links.json 相同
{
  "xiaohongshu-mine": { "platform": "xiaohongshu", "expect": "video",
                        "url": "https://xhslink.cn/o/xxxx" }
}
```

巡检会把两份清单**合并**执行；同名条目以私有清单为准（便于替换掉公共样本里
已失效的那条）。私有清单缺失不影响运行，但写坏会直接报错退出 ——
避免「清单坏了巡检却静默跳过」这种最坏情况。

> 同一平台有 ≥2 条样本时，「多样本判定」才会生效（区分解析器故障与链接过期），
> 所以建议重点平台各补 2–3 条。

**自动样本池（省去手工收集）**：挑巡检样本是持续负担 —— 链接会失效、平台要覆盖，
新平台上线时手头又常没有可用链接。而每次解析成功，后端其实都经手过一条**确实能用**
的链接：把它沉淀下来，巡检样本就能被真实使用"养"出来。

只要有人成功解析过一条链接，它就会被记进 `backend/data/samples.jsonl`
（按 `平台 + 链接` 去重、累加命中次数）。随时导出成巡检清单：

```bash
cd backend

# 看一眼样本池概况（有哪些平台、各几条）
python3 -m app.samples --report

# 导出候选清单，直接喂给巡检
python3 -m app.samples -o /etc/video-dewatermark/samples.json

# 只取「反复成功」的链接（命中 ≥2 次），更可信
python3 -m app.samples --min-count 2 -o /etc/video-dewatermark/samples.json
```

```bash
# /etc/video-dewatermark/health.env —— 让巡检自动读它
VD_LINKS_EXTRA=/etc/video-dewatermark/samples.json
```

形成闭环：**用户解析成功 → 样本自动沉淀 → 导出候选 → 巡检守着这批真实链接**。
命中次数越多说明越可靠（配合 `--min-count` 过滤偶发链接）。

隐私与安全：
- 入库前会**剥离分享凭证**（`xsec_token` / `share_id` / `u_code` / `share_sign` 等），
  即便用户粘的是带参长链也只留纯净链接 —— 巡检不需要凭证，凭证也没必要留存；
- 数据只落本机 `backend/data/`（已在 `.gitignore`），**绝不进仓库**；
- 收录默认开启，`SAMPLES_ENABLE=0` 可关闭；
- 管理接口 `GET /api/stats/samples`、`/api/stats/samples/export` 只对**未经代理直连**
  的本机开放（同其他统计接口），经代理的请求一律 404。

容量三重封顶：默认最多 200 条 / 保留 30 天 / 文件上限 1 MiB。

</details>

### 接口防护

公开部署的接口有两层保护：

| 层 | 手段 | 作用 |
|----|------|------|
| nginx | `limit_req`（`/api/parse` 每 IP 120 次/分、突发 30）+ `limit_conn`（`/api/` 每 IP 并发连接 16 / 32） | 在进入 Python 之前挡掉刷量，最省资源；保护媒体转发的带宽 |
| 应用 | 按 IP 令牌桶（默认 120 次/分、突发 30）+ 解析全局并发上限 8 + 批量独立配额 | 精细控制、随代码走 git；超限返回 429 / 503 并带 `Retry-After` |

批量是异步 job，一次提交会放大 N 倍上游请求，所以它**不占用**单条解析的全局并发(8)，
走独立配额：批量提交限流 `BATCH_RATE_PER_MIN` / `BATCH_BURST`，批量解析自身的并发由
`BATCH_CONCURRENCY`（默认 3）单独控制。

阈值全部可用环境变量覆盖，不必改代码：

```bash
GUARD_ENABLED=0          # 关闭应用层限流
PARSE_RATE_PER_MIN=120   # 每 IP 每分钟配额
PARSE_BURST=30           # 突发额度（令牌桶容量）
PARSE_CONCURRENCY=8      # 解析全局并发上限
BATCH_RATE_PER_MIN=20    # 每 IP 每分钟可提交的批量 job 数（批量独立配额）
BATCH_BURST=5            # 批量提交突发额度
BATCH_CONCURRENCY=3      # 批量解析的全局并发上限（内部逐条）
BATCH_ITEMS_MAX=50       # 单次批量最多条目数
BATCH_JOBS_MAX=50        # 内存中同时保留的 job 数（超出淘汰最老已完成）
BATCH_JOB_TTL_SEC=1800   # job 保留时长（秒），过期的已完成 job 自动清除
```

`/api/health` 会带上实时防护状态（当前并发、已拦截次数、跟踪的 IP 数），排查很方便：

```bash
curl -s http://127.0.0.1:3001/api/health
# {... "guard":{"enabled":true,"active":0,"rate_per_min":120,"rejected_rate":0,...}}
```

> **媒体 / 下载只限并发连接、不限频率** —— 视频流本身就是一个长连接，按频率限流会误伤正常播放。
> 限流状态存内存（单进程 uvicorn），重启即清零；令牌桶会惰性回收 5 分钟未活动的 IP，不会无限增长。

> **裸部署注意（无反向代理时必看）** —— 应用层限流按「客户端 IP」建桶，而 IP 取自
> `X-Real-IP` / `X-Forwarded-For`（**默认信任**）。项目推荐的部署形态是 nginx 反代：
> nginx 用 `proxy_set_header` 覆盖该头，客户端自带的伪造值会被覆盖掉。
> 若**前面没有反向代理**（直接暴露后端端口），请设 `GUARD_TRUST_PROXY_HEADERS=0`，
> 否则客户端可伪造 `X-Real-IP` 把限流桶键变成自己可控的值，从而绕过按 IP 限流。

### 海外部署：地域风控与出口代理

部分平台会**按出口 IP 地理位置**做限制，海外机器上直接解析失败，而同一份代码在国内机房一切正常：

| 平台 | 海外机器的表现 |
|------|----------------|
| B 站 | `api.bilibili.com` 返回 `HTTP 412`：`{"code":-412,"message":"request was banned"}` |
| 米游社 | `bbs-api.miyoushe.com` TCP 建连即挂死，解析报 `PARSE_TIMEOUT`（504） |

解法一样：给该平台单独指定一个**国内出口的 HTTP 代理**。环境变量按平台命名：

```bash
# 约定：<平台大写>_PROXY —— 只对该平台生效
# 支持 http:// 、https:// 以及 socks5:// / socks5h://（SOCKS5 由依赖 socksio 提供）
BILI_PROXY=http://<国内出口>:31280
MIYOUSHE_PROXY=http://<国内出口>:31280
# SOCKS5 出口（socks5h 表示域名交给代理远端解析）：
# MIYOUSHE_PROXY=socks5h://<国内出口>:1080
# 兜底：PARSE_PROXY —— 对所有平台生效（一般只在整机在海外、所有平台都要代理时用）
```

推荐用 systemd drop-in 注入，以后换代理地址不用动主 unit：

```bash
mkdir -p /etc/systemd/system/video-dewatermark.service.d
cat > /etc/systemd/system/video-dewatermark.service.d/proxy.conf <<'EOF'
[Service]
Environment=BILI_PROXY=http://<国内出口>:31280
Environment=MIYOUSHE_PROXY=http://<国内出口>:31280
EOF
systemctl daemon-reload && systemctl restart video-dewatermark
```

行为说明：

- **只让配置了的平台走代理**，其余平台直连 —— 不占代理带宽，代理抖动也不波及别的平台
- **什么都不配时行为与旧版完全一致**（国内部署无需任何改动）
- 媒体下载 CDN（如 `bilivideo.com`）海外直连通常可用，**无需代理**；若也被拒再考虑纳入
- 代理挂了只有对应平台解析失败，`ops/healthcheck.py` 会按连续失败次数报出来

自建代理（在有国内出口的机器上，CentOS 7 为例）：

```bash
yum -y install tinyproxy epel-release
# /etc/tinyproxy/tinyproxy.conf 关键项：Port 31280 / Allow <海外机IP> / ConnectPort 443
# 注意：EPEL 的 tinyproxy 1.8.4 未编译 BasicAuth，访问控制请用 iptables 白名单
iptables -I INPUT -p tcp --dport 31280 -s <海外机IP> -j ACCEPT
iptables -A INPUT -p tcp --dport 31280 -j DROP
service iptables save        # 持久化（需 iptables-services）
```

验证（在海外机器上执行，`code:0` / `200` 即通）：

```bash
curl -x http://<国内出口>:31280 \\
  -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \\
  -H "Referer: https://www.bilibili.com/" \\
  "https://api.bilibili.com/x/web-interface/view?bvid=<任意BV号>"
```

> ⚠️ 代理端口不要裸奔：至少做**来源 IP 白名单**（iptables 比应用层密码更硬）。
> 走 `CONNECT 443` 时内容是 TLS 加密的，但**别用不可信的公共代理**。

### 调用统计

`/api/stats` 按「天 × 平台」汇总**每一次真实解析请求**：调用量、成功率、耗时（平均 / P95 / 最大）、错误码分布、来源分布。

```bash
# 直连后端查看（默认只对本机开放）
curl -s "http://127.0.0.1:3001/api/stats?fmt=text"
```

```
解析统计  2026-09-12T09:04:30   保留 7 天
覆盖日期: 2026-09-12

平台                   调用     成功     失败     缓存      成功率       平均      P95       最大
----------------------------------------------------------------------------------
全部平台                 12     12      0      4   100.0%    682ms    1687ms    1687ms
kuaishou              4      4      0      2   100.0%    938ms    1106ms    1106ms
bilibili              2      2      0      1   100.0%     296ms     296ms     296ms

（非法/不支持的输入 1 次，未计入成功率）
    UNSUPPORTED_PLATFORM: 1

来源：healthcheck=16  web=6
```

```bash
# 机器可读（默认 json），也可按天筛选
curl -s http://127.0.0.1:3001/api/stats
curl -s "http://127.0.0.1:3001/api/stats?days=1"
curl -s "http://127.0.0.1:3001/api/stats?date=2026-09-12"
```

**和巡检的分工**：`ops/healthcheck.py` 是定点抽查（每 30 分钟拿固定链接各试一次），负责**立刻发现"挂了"**；
其趋势模块（`ops/health_trend.py`）看同一批链接在**最近 20 次巡检**里的成功率，负责**发现"正在变差"**；
统计记录每一次真实请求，负责**看出"哪个平台在悄悄变差"**（成功率下滑、耗时变长）。
三者口径不同可以互相印证：巡检趋势是"我主动试的"，统计是"用户真实遇到的"。
巡检请求带 `X-VD-Source: healthcheck`，与用户流量分开统计。

**为什么对外是 404**：`/api/stats` 只接受**未经反向代理直连**的请求（即本机运维）。nginx 用
`proxy_set_header` 强制写入 `X-Real-IP`，因此经 nginx 的请求一律 404 —— 扫描器自行伪造该头也无效
（伪造只会让它更像"来自代理"）。需要远程查看时设置 `STATS_TOKEN`，再带 `X-Stats-Token` 头访问。

**落盘与口径**：内存计数，每 30 秒（及进程退出时）原子写入 `backend/data/stats.json`，只保留最近
`STATS_RETENTION_DAYS`（默认 7）天。耗时指标只统计**未命中缓存**的请求（缓存命中的耗时不具参考价值），
P95 用蓄水池抽样把样本量控制在 2000 以内。

### 失败样本（可直接丢给 AI 去修）

统计告诉你"哪个平台在变差"，**失败样本告诉你"具体是哪条链接、错在哪一环"**。
`/api/stats/failures` 为每一例失败留档，串成一条修复闭环：

```
用户解析失败 → 落盘 → 隔段时间查看 → 丢给 AI 复现 → 修解析器 → 看 /api/stats 成功率回升
```

```bash
# 直接看（已按"该不该修"排序，最该看的在最前）
curl -s "http://127.0.0.1:3001/api/stats/failures?fmt=text"

# 只看值得修的，忽略"作品已失效"
curl -s "http://127.0.0.1:3001/api/stats/failures?kind=input,parser_bug&fmt=text"

# 原始流水：JSONL，一行一条，可以直接 cat / grep
cat backend/data/failures.jsonl
```

输出自带字段说明与定性分类，**可以整段复制给 AI**：

```
[1] kind=input  _rejected  出现 3 次  最近 2026-09-12T09:38:33
    code=UNSUPPORTED_PLATFORM  message=暂支持快手、抖音、小红书、豆包、B站、腾讯频道、微博链接  耗时=0ms
    url: https://www.weibo.com/1234567890/abcdef
    input: https://www.weibo.com/1234567890/abcdef
```

`kind` 定性决定复查优先级：

| kind | 含义 | 要不要修 |
|------|------|----------|
| `input` | 输入未被识别（新平台 / 新分享格式） | ✅ **最该看** —— "复制链接不兼容"最先在这里冒出来 |
| `parser_bug` | 解析失败 / 超时（对方页面结构变了） | ✅ 需要适配 |
| `risk` | 触发平台风控 | ⚠️ 可能要换策略 |
| `gone` | 作品不可用 | ❌ 内容已删，不用管 |

四个设计点：

- **同一条链接反复失败只累加 `count`，不重复写行** —— 否则一条失效链接就能刷满整个文件，把真正的新问题挤掉
- **修好就自动移出** —— 同一条链接后来解析成功了会**立即**从文件里移除。所以这个文件始终是一份
  「当前还没修好的」待办清单，复查时不会被已修复的条目占掉注意力
- **平台不支持时也会单独把链接摘出来** —— 那条链接正是复现与接入新平台的起点
- **三重封顶**：条数 300 / 保留 7 天 / 文件 2 MB；裁剪时优先丢 `gone` 与最久未出现的
  （`FAILURES_MAX` / `FAILURES_RETENTION_DAYS` / `FAILURES_MAX_BYTES` 可调）

**和它的对称面 —— 成功样本池**：失败样本回答"**该修什么**"，成功样本回答"**拿什么去巡检**"。
每条被成功解析的链接都会进 `backend/data/samples.jsonl`（按「平台 + 链接」去重、累加命中次数），
可直接导成巡检清单，省去手工收集真实链接：

```bash
cd backend
python3 -m app.samples --report                        # 概况：哪些平台、各几条
python3 -m app.samples --min-count 2 -o /tmp/s.json    # 只取反复成功（≥2 次）的，更可信
```

入库前会**剥离分享凭证**（`xsec_token` / `share_id` / `u_code` / `share_sign` 等），
即便用户粘的是带参长链也只留纯净链接；数据只落本机、绝不进仓库，`SAMPLES_ENABLE=0` 可关闭。
详见上文「自动样本池」。

### 一键诊断报告 `/api/stats/report`

前两个接口各看一半：`/api/stats` 告诉你"**哪个平台在变差**"，`/api/stats/failures` 告诉你
"**具体哪条链接、错在哪一环**"。排查时要来回取两次、还要手工拼上下文。

`/api/stats/report` 把两者**合成一份可直接投喂给 AI 的文本**：一次拿到「现状（成功率 / 耗时）+
待修样本（含原始输入）」，末尾还附上三个提问（哪些平台该警惕 / 每条样本根因 / 修复优先级），
复制给模型即可完成「定位 → 修解析器 → 看统计回升」的闭环。

```bash
# 一份完整的诊断报告（统计 + 待修样本），整段复制给 AI
curl -s http://127.0.0.1:3001/api/stats/report

# 只看最近 3 天统计 + 值得修的样本（忽略"作品已失效"）
curl -s "http://127.0.0.1:3001/api/stats/report?days=3&kind=input,parser_bug"
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `days` | 统计窗口（最近 N 天） | 全部保留期 |
| `limit` | 失败样本条数上限 | 50 |
| `kind` | 只看指定定性（逗号分隔），如 `input,parser_bug` | 全部 |

> 与上面两个接口一致，`/api/stats/report` **只对本机开放**（经 nginx 一律 404）。

> 与 `/api/stats` 一样，这两个接口**只对本机开放**（经 nginx 一律 404）。

## 📡 API

### `POST /api/parse` — 解析分享链接

```bash
curl -X POST http://localhost:3001/api/parse \
  -H "Content-Type: application/json" \
  -d '{"input": "https://v.kuaishou.com/xxxx"}'
```

响应（视频，含多清晰度）：

```json
{
  "ok": true,
  "type": "video",
  "title": "作品标题",
  "author": "作者名",
  "coverUrl": "/api/media/{token}",
  "videoUrl": "/api/media/{token}",
  "downloadUrl": "/api/download/{token}",
  "variants": [
    { "label": "高清", "mediaUrl": "/api/media/{token}", "downloadUrl": "/api/download/{token}" },
    { "label": "标清", "mediaUrl": "/api/media/{token}", "downloadUrl": "/api/download/{token}" }
  ],
  "images": []
}
```

响应（图文，`type = "images"` 时）：`images: [{ previewUrl, downloadUrl }, ...]`

| 端点 | 说明 |
|------|------|
| `GET /api/media/{token}` | 预览媒体（支持 Range / 206） |
| `GET /api/download/{token}` | 附件形式下载 |
| `GET /api/health` | 健康检查 |

### `POST /api/batch` — 批量解析（异步 job）

一次提交多条链接，立即返回 `job_id`，后台有界并发逐条解析（复用单条解析路径），
前端轮询进度、逐条展示结果。逐条错误隔离：单条失败只标记该项，不影响其他条目。

```bash
curl -X POST http://localhost:3001/api/batch \
  -H "Content-Type: application/json" \
  -d '{"items": ["https://v.kuaishou.com/xxx", "https://www.douyin.com/video/xxx"]}'
# {"ok":true,"jobId":"c98b6a9983b473fa47a11ef7","total":2}
```

### `GET /api/batch/{job_id}` — 查询批量 job

返回 job 状态（`running` / `done`）、总数 / 已完成 / 成功 / 失败计数，以及逐条
`items`（`status`、`ok`、错误码/文案，成功含与单条相同的 `result` 结构）：

```json
{
  "ok": true,
  "job": {
    "id": "c98b6a9983b473fa47a11ef7",
    "status": "done",
    "total": 2, "done": 2, "okCount": 1, "failCount": 1,
    "items": [
      { "index": 0, "status": "done", "ok": true, "ms": 812, "result": { "ok": true, "type": "video", "title": "..." } },
      { "index": 1, "status": "done", "ok": false, "code": "INVALID_LINK", "message": "未识别到有效链接", "result": null }
    ]
  }
}
```

> job 存内存（单实例通用），默认保留 `BATCH_JOB_TTL_SEC`（1800 秒），超出
> `BATCH_JOBS_MAX`（50）会优先淘汰最老的**已完成** job，运行中的不会被动。

> 媒体 token 落盘保存，默认 2 小时有效；预览或下载会自动续期。进程重启后仍可使用未过期 token，无需重新解析。环境变量 `TOKEN_TTL_MS`、`TOKEN_STORE_PATH` 可覆盖默认值。

## 🏗️ 架构

```
浏览器 ──HTTP──▶ nginx :8080
                  ├── /            静态资源（frontend/dist，SPA）
                  └── /api/* ────▶ uvicorn :3001（FastAPI）
                                      ├── 平台识别（快手 / 抖音 / 小红书 / 豆包 / B站 / 腾讯频道 / 米游社 / 微博 / 即梦AI / 皮皮虾 / 皮皮搞笑 / 最右 / 今日头条 / 微视）
                                      ├── 解析器（parsers/kuaishou | douyin | xhs | doubao | bilibili | qqchannel | miyoushe | weibo | jimeng | pipix | pipigaoxiao | zuiyou | toutiao | weishi）
                                     ├── 媒体代理（多 CDN fallback + Range 透传）
                                      └── 解析缓存（内存 60s）+ 媒体 token（落盘，2h / 访问续期）
                                        └────▶ 平台 CDN（代抓取，作品不落盘）
```

## 📁 项目结构

```
video-dewatermark/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口：parse / batch / media / download
│   │   ├── batch.py           # 批量解析 job（有界内存存储 + 并发信号量）
│   │   ├── parsers/
│   │   │   ├── extract.py     # 链接提取 + 平台识别
│   │   │   ├── kuaishou.py    # 快手解析（档位分组 / CDN 收集）
│   │   │   ├── douyin.py      # 抖音解析（_ROUTER_DATA / 无水印）
│   │   │   ├── xhs.py         # 小红书解析（__INITIAL_STATE__）
│   │   │   ├── doubao.py      # 豆包解析（data-fn-args / 分享 API）
│   │   │   ├── bilibili.py    # B站解析（view/playurl + opus 图文）
│   │   │   ├── qqchannel.py   # 腾讯频道解析（Nuxt devalue 池 + curl_cffi 过 EdgeOne）
│   │   │   ├── miyoushe.py    # 米游社解析（getPostFull 多档视频 / 图文原图）
│   │   │   ├── weibo.py       # 微博解析（bid→mid / 访客 Cookie / 视频多档位 / 图集原图）
│   │   │   ├── jimeng.py      # 即梦AI解析（短链 302 + get_item_info 原视频）
│   │   │   ├── pipix.py       # 皮皮虾解析（RENDER_DATA / 视频多 CDN / 图集原图）
│   │   │   ├── pipigaoxiao.py # 皮皮搞笑解析（fetch_content 接口 / 视频多清晰度 / 图集原图）
│   │   │   ├── zuiyou.py      # 最右解析（sharePage SSR APP_INITIAL_STATE / 视频直链 / 图集原图）
│   │   │   ├── toutiao.py     # 今日头条解析（微头条 RENDER_DATA / 大图无水印；短视频网页端不可用）
│   │   │   └── weishi.py      # 微视解析（短链 302 取 feedid + WSH5GetPlayPage / video_spec_urls 多档签名直链）
│   │   ├── guard.py           # 请求防护（IP 限流 / 并发上限 / 错误兜底）
│   │   ├── stats.py           # 调用统计（按天 × 平台聚合 + 落盘 + 本机鉴权）
│   │   ├── failures.py        # 失败样本流水（去重 + 定性分类 + JSONL 落盘）
│   │   ├── samples.py         # 成功样本池（沉淀可用链接，导出巡检候选；剥离凭证）
│   │   ├── tokens.py          # 媒体 token（多候选 URL + 落盘 + TTL 续期）
│   │   └── errors.py          # 统一错误码
│   ├── data/                  # token 落盘目录（gitignore，不入库）
│   └── tests/                 # pytest
├── frontend/
│   ├── src/App.tsx            # 单页应用（Neubrutalism 风格）
│   └── src/index.css
├── ops/                       # 运维脚本
│   ├── deploy.sh              # 一键部署（健康检查 + 失败自动回滚）
│   ├── healthcheck.py         # 全平台解析健康巡检
│   ├── health_trend.py        # 巡检成功率趋势与退化判定
│   ├── links.json             # 巡检用的真实分享链接
│   ├── install-timer.sh       # 安装巡检 systemd timer
│   ├── nginx/                 # nginx 站点配置（含限流 / 安全头）
│   └── systemd/               # 巡检 service / timer 单元文件
├── docker/                    # Docker 一键方案
│   ├── nginx.conf             # compose 内 nginx（静态 + 反代 backend，含限流/安全头）
│   └── .env.example           # Docker 部署可配项模板（端口 / 平台代理 / 告警）
├── backend/
│   └── Dockerfile             # 后端镜像（python + fastapi，内部 3001）
├── frontend/
│   └── Dockerfile             # 前端镜像（node 构建 → nginx 托管 + 反代 /api）
├── docker-compose.yml         # 一键编排（backend + frontend + 持久卷 + 健康检查）
└── start.sh                   # 本地一键启动
```

## 🧪 测试

```bash
cd backend && PYTHONPATH=. python -m pytest -q
```

## 🗺️ Roadmap

- [x] B站视频 / 图文专栏
- [x] 腾讯频道（Nuxt devalue 池 + `curl_cffi` 过 EdgeOne 指纹，短签名直链失效自动重解析）
- [x] 米游社（`getPostFull` 视频多档签名直链 / 图文原图，短签名失效自动重解析）
- [x] 微博（`bid → mid` + 访客 Cookie 绕登录；视频多清晰度 / 图集原图，短签名失效自动重解析）
- [x] 即梦AI（短链 302 + `get_item_info` 取原视频，片尾角标如实标注）
- [x] 皮皮虾（`RENDER_DATA` 解码 → `ppxItemDetail.item`；视频多 CDN / 图集原图，短签名失效自动重解析）
- [x] 皮皮搞笑（`fetch_content` 接口 → `data.post`；视频按清晰度分档 / 图集原图）
- [x] 最右（分享页 SSR `APP_INITIAL_STATE` → `sharePost.postDetail.post`；视频直链 / 图集原图，短签名失效自动重解析）
- [x] 今日头条（微头条 `RENDER_DATA` → `articleInfo.thread.threadBase`；`largeImageList` 无水印大图，短签名失效自动重解析；短视频网页端不可用、明确提示 App 内观看）
- [x] 微视（短链 302 取 `feedid` + `WSH5GetPlayPage` → `video_spec_urls` 多档签名直链，`haveWatermark=0` 无水印档；CDN 反 Referer，短签名失效自动重解析）
- [x] 一键部署脚本（含健康检查与失败自动回滚）
- [x] 全平台解析健康巡检 + 定时任务
- [x] 巡检成功率趋势与退化预警（`ops/health_trend.py`）
- [ ] 更多平台接入（…）
- [x] 知乎（API 直取，无需 Cookie，LD/SD/HD/FHD）
- [x] 好看视频（`video/detail` JSON 取 `clarityUrl` 多档签名直链，短签名失效自动重解析）
- [x] 解析成功率统计（`/api/stats`，按天 × 平台）
- [x] 失败样本流水（`/api/stats/failures`，供 AI 复现与修复）
- [x] 平台清单后端化（前端不再硬编码，新增平台只改后端）
- [x] 接口限流与防护（nginx + 应用层双层）
- [x] 批量解析（异步 job + 独立配额，前端多行粘贴 / 进度条 / 逐条结果卡）
- [x] 一键打包下载 zip（批量的真正收益点：后台有界并发抓取成功项主媒体 → ZIP_STORED 打包，逐条失败跳过、体积/保留时限硬限制）
- [x] Docker 一键部署（docker-compose 全栈：backend + nginx，数据卷持久化 + 健康检查，与 systemd 并存）

## ⚠️ 免责声明

本项目仅供学习交流与个人研究使用。请尊重创作者的合法权益，**下载内容的版权归原作者所有**；请勿将本项目用于任何商业用途，或对下载内容进行二次分发、搬运等侵权行为。因使用本项目产生的一切后果由使用者自行承担。

---

<div align="center">

_如果这个项目对你有帮助，欢迎点个 Star ⭐_

</div>
