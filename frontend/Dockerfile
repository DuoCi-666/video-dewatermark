# syntax=docker/dockerfile:1
# 前端镜像：Node 构建产物 → nginx 静态托管 + 反代 /api 到 backend 服务

# ---- 构建阶段 ----
FROM node:20-alpine AS build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 运行阶段：nginx ----
FROM nginx:1.27-alpine

# 静态资源
COPY --from=build /build/dist /usr/share/nginx/html

# 站点配置（限流/连接限制 + SPA fallback + 反代 backend）
COPY docker/nginx.conf /etc/nginx/conf.d/video-dewatermark.conf

EXPOSE 8080