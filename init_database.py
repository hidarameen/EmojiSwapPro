
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import asyncpg
import os
import logging
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def create_database_tables(conn: asyncpg.Connection) -> None:
    """Create all required database tables"""
    
    # Emoji replacements table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS emoji_replacements (
            id SERIAL PRIMARY KEY,
            normal_emoji TEXT NOT NULL,
            premium_emoji_id BIGINT NOT NULL,
            channel_id BIGINT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            usage_count INTEGER DEFAULT 0
        )
    """)
    
    # Monitored channels table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS monitored_channels (
            id SERIAL PRIMARY KEY,
            channel_id BIGINT NOT NULL UNIQUE,
            channel_name TEXT,
            channel_username TEXT,
            is_replacement_enabled BOOLEAN DEFAULT TRUE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            message_count INTEGER DEFAULT 0
        )
    """)
    
    # Bot settings table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            id SERIAL PRIMARY KEY,
            setting_key TEXT NOT NULL UNIQUE,
            setting_value TEXT,
            setting_type TEXT DEFAULT 'string',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Forwarding tasks table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS forwarding_tasks (
            id SERIAL PRIMARY KEY,
            source_channel_id BIGINT NOT NULL,
            target_channel_id BIGINT NOT NULL,
            source_channel_name TEXT,
            target_channel_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_forwarded TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            messages_forwarded INTEGER DEFAULT 0,
            UNIQUE(source_channel_id, target_channel_id)
        )
    """)
    
    # Statistics table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_statistics (
            id SERIAL PRIMARY KEY,
            stat_date DATE DEFAULT CURRENT_DATE,
            messages_processed INTEGER DEFAULT 0,
            emojis_replaced INTEGER DEFAULT 0,
            messages_forwarded INTEGER DEFAULT 0,
            errors_count INTEGER DEFAULT 0,
            uptime_seconds INTEGER DEFAULT 0,
            UNIQUE(stat_date)
        )
    """)
    
    logger.info("✅ All database tables created successfully")

async def create_indexes(conn: asyncpg.Connection) -> None:
    """Create database indexes for better performance"""
    
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_emoji_replacements_channel ON emoji_replacements(channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_emoji_replacements_emoji ON emoji_replacements(normal_emoji)",
        "CREATE INDEX IF NOT EXISTS idx_emoji_replacements_active ON emoji_replacements(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_monitored_channels_active ON monitored_channels(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_monitored_channels_id ON monitored_channels(channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_forwarding_tasks_source ON forwarding_tasks(source_channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_forwarding_tasks_target ON forwarding_tasks(target_channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_forwarding_tasks_active ON forwarding_tasks(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_bot_statistics_date ON bot_statistics(stat_date)"
    ]
    
    for index_sql in indexes:
        await conn.execute(index_sql)
    
    logger.info("✅ All database indexes created successfully")

async def insert_default_settings(conn: asyncpg.Connection) -> None:
    """Insert default bot settings"""
    
    default_settings = [
        ('bot_version', '2.0.0', 'string', 'Bot version'),
        ('max_message_length', '4096', 'integer', 'Maximum message length'),
        ('replacement_enabled', 'true', 'boolean', 'Global emoji replacement status'),
        ('forwarding_enabled', 'true', 'boolean', 'Global message forwarding status'),
        ('log_level', 'INFO', 'string', 'Logging level'),
        ('auto_backup', 'true', 'boolean', 'Automatic database backup'),
        ('rate_limit_messages', '30', 'integer', 'Messages per minute limit'),
        ('session_timeout', '3600', 'integer', 'Session timeout in seconds')
    ]
    
    for key, value, setting_type, description in default_settings:
        await conn.execute("""
            INSERT INTO bot_settings (setting_key, setting_value, setting_type, description)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (setting_key) DO NOTHING
        """, key, value, setting_type, description)
    
    logger.info("✅ Default settings inserted successfully")

async def verify_database_setup(conn: asyncpg.Connection) -> bool:
    """Verify that database is properly set up"""
    
    try:
        # Check tables exist
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN (
                'emoji_replacements', 
                'monitored_channels', 
                'bot_settings',
                'forwarding_tasks',
                'bot_statistics'
            )
        """)
        
        table_names = [row['table_name'] for row in tables]
        expected_tables = ['emoji_replacements', 'monitored_channels', 'bot_settings', 'forwarding_tasks', 'bot_statistics']
        
        missing_tables = set(expected_tables) - set(table_names)
        if missing_tables:
            logger.error(f"❌ Missing tables: {missing_tables}")
            return False
        
        # Check settings exist
        settings_count = await conn.fetchval("SELECT COUNT(*) FROM bot_settings")
        logger.info(f"✅ Found {settings_count} bot settings")
        
        logger.info("✅ Database verification completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Database verification failed: {e}")
        return False

async def init_database() -> bool:
    """Initialize complete database setup"""
    
    try:
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            logger.warning("⚠️ No DATABASE_URL provided, skipping database initialization")
            return True
        
        logger.info("🗄️ Connecting to database...")
        conn = await asyncpg.connect(database_url)
        logger.info("✅ Connected to database successfully")
        
        logger.info("📝 Creating database tables...")
        await create_database_tables(conn)
        
        logger.info("🔍 Creating database indexes...")
        await create_indexes(conn)
        
        logger.info("⚙️ Inserting default settings...")
        await insert_default_settings(conn)
        
        logger.info("✅ Verifying database setup...")
        verification_success = await verify_database_setup(conn)
        
        await conn.close()
        logger.info("🎉 Database initialization completed successfully!")
        
        return verification_success
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(init_database())
    exit(0 if success else 1)
