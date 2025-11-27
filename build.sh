#!/bin/bash

# 1. 安裝環境
pip install --upgrade pip
pip install -r requirements.txt

# 2. 執行靜態生成腳本
# 此步驟會生成 dist/index.html (動畫爬蟲頁面) 和 dist/data/*.json
python generate_static.py

# 3. 檔案重命名與路徑修正 (確保根目錄能顯示功能列表)
# 3a. 將動畫爬蟲頁面 (原 dist/index.html) 重新命名為 crawler.html
mv dist/index.html dist/crawler.html

# 3b. 將 templates/home.html (功能列表頁面) 複製到 dist/ 並命名為 index.html 作為網站入口
cp templates/home.html dist/index.html

# 4. 複製靜態資源
# 將 static/ 內容複製到 dist/ 中
cp -r static dist/