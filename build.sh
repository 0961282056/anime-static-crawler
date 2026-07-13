#!/usr/bin/env bash
set -euo pipefail

echo "Installing locked build dependencies..."
python -m pip install --require-hashes -r requirements-build.txt

echo "Validating tracked data and generating Cloudflare Pages output..."
export BUILD_ONLY=true
export PYTHONDONTWRITEBYTECODE=1
python generate_static.py
python manage.py validate-all

echo "Static build completed successfully."
