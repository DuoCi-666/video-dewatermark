# syntax=docker/dockerfile:1
# 后端镜像：Python + FastAPI 解析服务（内部端口 3001，不直接对外）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统依赖：ffmpeg —— 把 HLS(m3u8) 源转封装成 mp4（预览/下载通用）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖利用层缓存，代码变更不触发重装
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 后拷贝源码
COPY backend/app ./app

# 数据落盘目录（stats / tokens / failures / packs），由 compose 挂卷持久化
RUN mkdir -p /data

EXPOSE 3001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]