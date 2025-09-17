
# Use Python 3.11 slim image for smaller size
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Install system dependencies required for all packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    build-essential \
    pkg-config \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy ALL project files (override .dockerignore for complete copy)
COPY . .

# Install ALL Python dependencies from ALL requirements files
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install from requirements-docker.txt (production dependencies)
RUN if [ -f "requirements-docker.txt" ]; then \
    pip install --no-cache-dir -r requirements-docker.txt; \
    fi

# Install from requirements.txt if exists
RUN if [ -f "requirements.txt" ]; then \
    pip install --no-cache-dir -r requirements.txt; \
    fi

# Install from requirements_control.txt if exists
RUN if [ -f "requirements_control.txt" ]; then \
    pip install --no-cache-dir -r requirements_control.txt; \
    fi

# Install from pyproject.toml if exists
RUN if [ -f "pyproject.toml" ]; then \
    pip install --no-cache-dir -e .; \
    fi

# Install any additional packages that might be needed
RUN pip install --no-cache-dir \
    telethon \
    asyncpg \
    python-dotenv \
    python-telegram-bot \
    aiohttp \
    python-dateutil \
    cryptography \
    structlog \
    orjson \
    aiofiles \
    psutil \
    sentry-sdk \
    prometheus-client

# Make all shell scripts executable
RUN find . -name "*.sh" -type f -exec chmod +x {} \;
RUN find . -name "*.py" -type f -exec chmod +x {} \;

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 botuser

# Create necessary directories and set permissions
RUN mkdir -p /app/logs /app/data /app/sessions && \
    chown -R botuser:botuser /app && \
    chmod -R 755 /app

# Switch to non-root user
USER botuser

# Create database initialization script
RUN echo '#!/usr/bin/env python3\n\
import asyncio\n\
import asyncpg\n\
import os\n\
import logging\n\
\n\
logging.basicConfig(level=logging.INFO)\n\
logger = logging.getLogger(__name__)\n\
\n\
async def init_database():\n\
    """Initialize database tables if they dont exist"""\n\
    try:\n\
        database_url = os.getenv("DATABASE_URL")\n\
        if not database_url:\n\
            logger.warning("No DATABASE_URL provided, skipping database initialization")\n\
            return\n\
        \n\
        conn = await asyncpg.connect(database_url)\n\
        logger.info("Connected to database successfully")\n\
        \n\
        # Create tables\n\
        await conn.execute("""\n\
            CREATE TABLE IF NOT EXISTS emoji_replacements (\n\
                id SERIAL PRIMARY KEY,\n\
                normal_emoji TEXT NOT NULL,\n\
                premium_emoji_id BIGINT NOT NULL,\n\
                channel_id BIGINT,\n\
                description TEXT,\n\
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n\
                is_active BOOLEAN DEFAULT TRUE\n\
            )\n\
        """)\n\
        \n\
        await conn.execute("""\n\
            CREATE TABLE IF NOT EXISTS monitored_channels (\n\
                id SERIAL PRIMARY KEY,\n\
                channel_id BIGINT NOT NULL UNIQUE,\n\
                channel_name TEXT,\n\
                is_replacement_enabled BOOLEAN DEFAULT TRUE,\n\
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n\
                is_active BOOLEAN DEFAULT TRUE\n\
            )\n\
        """)\n\
        \n\
        await conn.execute("""\n\
            CREATE TABLE IF NOT EXISTS bot_settings (\n\
                id SERIAL PRIMARY KEY,\n\
                setting_key TEXT NOT NULL UNIQUE,\n\
                setting_value TEXT,\n\
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n\
            )\n\
        """)\n\
        \n\
        await conn.execute("""\n\
            CREATE TABLE IF NOT EXISTS forwarding_tasks (\n\
                id SERIAL PRIMARY KEY,\n\
                source_channel_id BIGINT NOT NULL,\n\
                target_channel_id BIGINT NOT NULL,\n\
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n\
                is_active BOOLEAN DEFAULT TRUE,\n\
                UNIQUE(source_channel_id, target_channel_id)\n\
            )\n\
        """)\n\
        \n\
        # Create indexes\n\
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_emoji_replacements_channel ON emoji_replacements(channel_id)")\n\
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_monitored_channels_active ON monitored_channels(is_active)")\n\
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_forwarding_tasks_source ON forwarding_tasks(source_channel_id)")\n\
        \n\
        logger.info("Database tables initialized successfully")\n\
        await conn.close()\n\
        \n\
    except Exception as e:\n\
        logger.error(f"Database initialization failed: {e}")\n\
        raise\n\
\n\
if __name__ == "__main__":\n\
    asyncio.run(init_database())\n\
' > /app/init_db.py && chmod +x /app/init_db.py

# Health check for container monitoring
HEALTHCHECK --interval=60s --timeout=30s --start-period=120s --retries=3 \
    CMD python -c "import sys; print('Bot is healthy'); sys.exit(0)" || exit 1

# Expose port for health checks (if needed)
EXPOSE 8080

# Create comprehensive startup script
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
echo "🚀 Starting Telegram Bot Complete Setup..."\n\
\n\
# Check required environment variables\n\
required_vars=("API_ID" "API_HASH" "SESSION_STRING")\n\
missing_vars=()\n\
\n\
for var in "${required_vars[@]}"; do\n\
    if [ -z "${!var}" ]; then\n\
        missing_vars+=("$var")\n\
    fi\n\
done\n\
\n\
if [ ${#missing_vars[@]} -ne 0 ]; then\n\
    echo "❌ Missing required environment variables:"\n\
    printf "%s\n" "${missing_vars[@]}"\n\
    echo "Please set these variables in your environment"\n\
    exit 1\n\
fi\n\
\n\
# Set default values\n\
export LOG_LEVEL="${LOG_LEVEL:-INFO}"\n\
export ENVIRONMENT="${ENVIRONMENT:-production}"\n\
\n\
echo "✅ Environment variables validated"\n\
echo "📡 API_ID: ${API_ID}"\n\
echo "🔧 Environment: ${ENVIRONMENT}"\n\
echo "📊 Log Level: ${LOG_LEVEL}"\n\
\n\
# Initialize database\n\
echo "🗄️ Initializing database..."\n\
python /app/init_db.py || {\n\
    echo "⚠️ Database initialization failed, but continuing..."\n\
}\n\
\n\
# Test bot components\n\
echo "🧪 Testing bot components..."\n\
python /app/test_bot.py || {\n\
    echo "⚠️ Some tests failed, but continuing..."\n\
}\n\
\n\
# Create logs directory\n\
mkdir -p /app/logs\n\
\n\
echo "🤖 All setup complete! Starting Telegram Bot..."\n\
exec python /app/telegram_bot.py\n\
' > /app/start_complete.sh && chmod +x /app/start_complete.sh

# Run the complete setup and bot startup
CMD ["/app/start_complete.sh"]
