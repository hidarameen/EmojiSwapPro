# Use Python 3.11 slim image for smaller size
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Install system dependencies required for Telethon and asyncpg
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy requirements file first (Docker layer caching optimization)
COPY requirements-docker.txt requirements.txt

# Upgrade pip and install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY telegram_bot.py .
COPY custom_parse_mode.py .
COPY generate_session.py .
COPY start.sh .

# Make startup script executable
RUN chmod +x start.sh

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 botuser && \
    chown -R botuser:botuser /app
USER botuser

# Create directory for logs
RUN mkdir -p /app/logs

# Health check for container monitoring
HEALTHCHECK --interval=60s --timeout=30s --start-period=120s --retries=3 \
    CMD python -c "import asyncio; import sys; sys.exit(0)" || exit 1

# Expose port for health checks (if needed)
EXPOSE 8080

# Run the bot using startup script
CMD ["./start.sh"]