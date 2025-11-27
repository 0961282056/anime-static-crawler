#!/bin/bash

# --- 新增的程式碼：確保在根目錄執行 ---
# 取得腳本所在的目錄（即專案根目錄）
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# 將工作目錄切換到腳本所在的目錄 (這將解決所有內部路徑錯誤)
cd "$SCRIPT_DIR" || exit
# ------------------------------------

# 1. 安裝環境
pip install --upgrade pip
# 需要安裝 Jinja2 來替代 Flask 內建的渲染器
pip install -r requirements.txt
pip install Jinja2

# 2. 執行靜態生成腳本
python generate_static.py

# 3. 複製靜態資源
# 由於 Cloudflare Pages 只會部署 'dist' 目錄的內容
# 您必須將所有的 CSS/JS/base.html 依賴複製到 'dist' 目錄中
cp templates/base.html dist/
# 假設您將所有的 CSS/JS 放在 static/css 和 static/js 中
cp -r static dist/

# Cloudflare Pages 的 Build Output Directory 請設定為 'dist'