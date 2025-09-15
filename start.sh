#!/bin/bash

# Northflank startup script for Telegram Emoji Bot
# This script handles environment setup and starts the bot

set -e  # Exit on any error

echo "🚀 Starting Telegram Emoji Bot..."

# Check required environment variables
required_vars=("API_ID" "API_HASH" "SESSION_STRING")
missing_vars=()

for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
    echo "❌ Missing required environment variables:"
    printf '%s\n' "${missing_vars[@]}"
    echo "Please set these variables in Northflank Environment settings"
    exit 1
fi

# Set default values for optional variables
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export ENVIRONMENT="${ENVIRONMENT:-production}"

echo "✅ Environment variables validated"
echo "📡 API_ID: ${API_ID}"
echo "🔧 Environment: ${ENVIRONMENT}"
echo "📊 Log Level: ${LOG_LEVEL}"

# Check database connection if DATABASE_URL is set
if [ -n "$DATABASE_URL" ]; then
    echo "🗄️ Database URL configured"
    
    # Test database connection
    python -c "
import asyncpg
import asyncio
import os
import sys

async def test_db():
    try:
        conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
        await conn.close()
        print('✅ Database connection successful')
        return True
    except Exception as e:
        print(f'❌ Database connection failed: {e}')
        return False

result = asyncio.run(test_db())
sys.exit(0 if result else 1)
" || {
        echo "⚠️ Database connection test failed, but continuing..."
        echo "⚠️ Bot will try to connect during runtime"
    }
else
    echo "⚠️ No DATABASE_URL provided - bot will run without database persistence"
fi

# Create logs directory
mkdir -p /app/logs

# Initialize Sentry if configured
if [ -n "$SENTRY_DSN" ]; then
    echo "📊 Sentry error monitoring enabled"
fi

# Start the bot
echo "🤖 Starting Telegram Emoji Bot..."
exec python telegram_bot.py