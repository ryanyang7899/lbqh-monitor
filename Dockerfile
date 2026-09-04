# 联并千行MaaS 多用户余额监控 —— Docker 镜像
#
# 基座：官方 Playwright Python 镜像（预装 chromium 及其系统依赖），
# 服务用 Playwright + 验证码识别抓取平台数据，必须带浏览器。
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# 先装 Python 依赖（利用构建缓存）
# 国外源（files.pythonhosted.org / playwright 官方 CDN）在本机网络下超时，
# 默认走国内镜像，可构建时用 --build-arg 覆盖：--build-arg PIP_INDEX_URL=xxx
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
ENV PIP_INDEX_URL=$PIP_INDEX_URL \
    PLAYWRIGHT_DOWNLOAD_HOST=$PLAYWRIGHT_DOWNLOAD_HOST \
    PIP_DEFAULT_TIMEOUT=120
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

# 拷贝源码（敏感与运行时数据已由 .dockerignore 排除，经 volume 挂载）
COPY . .

EXPOSE 8100

# 服务默认监听 8100（8000 可能被其他服务占用）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
