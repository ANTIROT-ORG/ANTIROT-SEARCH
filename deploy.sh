#!/bin/bash
# Antirot — Deploy to Cloudflare Pages
set -e

echo "🕷️ Antirot Deployment"
echo "========================"

# 1. Build frontend
echo ""
echo "📦 Building Astro app..."
cd web
npm run build
cd ..

# 2. Deploy to Cloudflare Pages
echo ""
echo "🌐 Deploying to Cloudflare Pages..."
cd web
npx wrangler pages deploy dist --project-name=ratsearch
cd ..

echo ""
echo "✅ Deployed!"
echo "   URL: https://ratsearch.pages.dev"
echo ""
echo "Required env vars (set in Cloudflare dashboard):"
echo "   TURSO_DATABASE_URL"
echo "   TURSO_AUTH_TOKEN"
echo "   REDIS_URL (optional, for caching)"
echo "   HF_TOKEN (for HuggingFace raw layer)"
