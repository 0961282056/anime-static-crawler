#!/bin/bash

# Cloudflare Pages Build Script
# 目的：在 Cloudflare 部署環境中，只安裝生成 HTML 所需的最小依賴，跳過爬蟲重型套件。

echo "🚀 [Fast Build] 偵測到 Cloudflare 部署模式..."

# 設定環境變數，告訴 Python 腳本現在是 Build Only 模式
export BUILD_ONLY=true

# 1. 安裝輕量依賴 (加入 sentry-sdk)
echo "📦 安裝 HTML 生成所需套件 (Jinja2, Sentry)..."
pip install jinja2 sentry-sdk

# 2. 執行靜態生成
echo "🔨 開始生成靜態 HTML..."
python generate_static.py