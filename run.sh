#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "📦 Встановлення залежностей..."
pip install -r requirements.txt -q

echo "🚀 Запуск TGStat на http://localhost:8000"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
