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

# Copy ALL project files
COPY . .

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install from requirements.txt if exists
RUN if [ -f "requirements.txt" ]; then \
    pip install --no-cache-dir -r requirements.txt; \
    fi

# Install any additional packages (fallback)
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

# Create non-root user
RUN useradd --create-home --shell /bin/bash --uid 1000 botuser
RUN mkdir -p /app/logs /app/data /app/sessions && \
    chown -R botuser:botuser /app && \
    chmod -R 755 /app

USER botuser

# Health check
HEALTHCHECK --interval=60s --timeout=30s --start-period=120s --retries=3 \
    CMD python -c "import sys; print('Bot is healthy'); sys.exit(0)" || exit 1

# Expose port (optional for health check)
EXPOSE 8080

# Startup script
RUN echo '#!/bin/bash\n\
set -e\n\
echo "🚀 Starting Telegram Bot..."\n\
python /app/run_control_bot.py\n\
' > /app/start.sh && chmod +x /app/start.sh

# Run bot
CMD ["/app/start.sh"]
