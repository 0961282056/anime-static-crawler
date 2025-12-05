#!/bin/bash

# 取得腳本所在的目錄
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR" || exit

# 升級 pip (可選，通常可以省去)
# pip install --upgrade pip

# --- 【極速優化】條件式安裝依賴 ---
if [ "$BUILD_ONLY" = "true" ]; then
    echo "🚀 [Fast Build] 偵測到 Cloudflare 部署模式..."
    echo "📦 僅安裝 HTML 生成所需的輕量套件 (Jinja2)..."
    pip install Jinja2
else
    echo "🕷️ [Crawler Mode] 偵測到爬蟲模式，安裝完整依賴..."
    pip install -r requirements.txt
fi
# ------------------------------------

# 執行靜態生成腳本
python generate_static.py

# 複製靜態資源
cp templates/base.html dist/
cp -r static dist/

# Cloudflare 的輸出目錄
# 請確保後台設定為 'dist'
