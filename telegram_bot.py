#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import asyncpg
import re
from typing import Dict, List, Optional, Tuple, Union
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityCustomEmoji, User, Channel
from custom_parse_mode import CustomParseMode

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('telegram_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TelegramEmojiBot:
    """
    Comprehensive Telegram bot using Telethon with session string support.
    Monitors channels, replaces emojis with premium ones, handles Arabic commands.
    """
    
    def __init__(self):
        # Environment variables
        self.api_id = int(os.getenv('API_ID', '0'))
        self.api_hash = os.getenv('API_HASH', '')
        self.session_string = os.getenv('SESSION_STRING', '')
        self.database_url = os.getenv('DATABASE_URL', '')
        
        # Validate required environment variables
        if not all([self.api_id, self.api_hash, self.session_string, self.database_url]):
            logger.error("Missing required environment variables: API_ID, API_HASH, SESSION_STRING, DATABASE_URL")
            raise ValueError("Missing required environment variables")
        
        # Initialize Telegram client with session string
        self.client = TelegramClient(
            StringSession(self.session_string),
            self.api_id, 
            self.api_hash
        )
        
        # Database connection pool
        self.db_pool: Optional[asyncpg.Pool] = None
        
        # Custom parse mode for premium emojis
        self.parse_mode = CustomParseMode('markdown')
        
        # Cache for emoji mappings and monitored channels
        self.emoji_mappings: Dict[str, int] = {}  # Global replacements
        self.channel_emoji_mappings: Dict[int, Dict[str, int]] = {}  # Channel-specific replacements
        self.monitored_channels: Dict[int, Dict[str, str]] = {}
        self.channel_replacement_status: Dict[int, bool] = {}  # Channel replacement activation status
        
        # Cache for forwarding tasks
        self.forwarding_tasks: Dict[int, Dict[str, Union[int, bool]]] = {}  # task_id -> {source, target, active}
        
        # Cache for admin list
        self.admin_ids: set = {6602517122}  # Default admin
        
        # Arabic command mappings - ordered by length (longest first) to avoid conflicts
        self.arabic_commands = {
            'حذف_جميع_استبدالات_قناة': 'delete_all_channel_emoji_replacements',
            'إضافة_استبدال_قناة': 'add_channel_emoji_replacement',
            'عرض_استبدالات_قناة': 'list_channel_emoji_replacements',
            'حذف_استبدال_قناة': 'delete_channel_emoji_replacement',
            'نسخ_استبدالات_قناة': 'copy_channel_emoji_replacements',
            'حذف_جميع_الاستبدالات': 'delete_all_emoji_replacements',
            'تنظيف_الاستبدالات': 'clean_duplicate_replacements',
            'تفعيل_استبدال_قناة': 'activate_channel_replacement',
            'تعطيل_استبدال_قناة': 'deactivate_channel_replacement',
            'حالة_استبدال_قناة': 'check_channel_replacement_status',
            'تعطيل_مهمة_توجيه': 'deactivate_forwarding_task',
            'تفعيل_مهمة_توجيه': 'activate_forwarding_task',
            'حذف_مهمة_توجيه': 'delete_forwarding_task',
            'تعديل_تأخير_مهمة': 'update_forwarding_delay',
            'إضافة_مهمة_توجيه': 'add_forwarding_task',
            'عرض_مهام_التوجيه': 'list_forwarding_tasks',
            'إضافة_استبدال': 'add_emoji_replacement',
            'عرض_الاستبدالات': 'list_emoji_replacements', 
            'حذف_استبدال': 'delete_emoji_replacement',
            'إضافة_قناة': 'add_channel',
            'عرض_القنوات': 'list_channels',
            'حذف_قناة': 'remove_channel',
            'معرف_ايموجي': 'get_emoji_id',
            'اضافة_ادمن': 'add_admin',
            'عرض_الادمن': 'list_admins',
            'حذف_ادمن': 'remove_admin',
            'مساعدة': 'help_command'
        }

    async def init_database(self):
        """Initialize database connection pool"""
        try:
            self.db_pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=10)
            logger.info("Database connection pool initialized successfully")
            
            # Load cached data
            await self.load_emoji_mappings()
            await self.load_channel_emoji_mappings()
            await self.load_monitored_channels()
            await self.load_forwarding_tasks()
            await self.load_admin_ids()
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def load_emoji_mappings(self):
        """Load emoji mappings from database into cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT normal_emoji, premium_emoji_id FROM emoji_replacements")
                self.emoji_mappings = {row['normal_emoji']: row['premium_emoji_id'] for row in rows}
                logger.info(f"Loaded {len(self.emoji_mappings)} emoji mappings from database")
        except Exception as e:
            logger.error(f"Failed to load emoji mappings: {e}")

    async def load_channel_emoji_mappings(self):
        """Load channel-specific emoji mappings from database into cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
        try:
            async with self.db_pool.acquire() as conn:
                # Create channel_emoji_replacements table if it doesn't exist
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS channel_emoji_replacements (
                        id SERIAL PRIMARY KEY,
                        channel_id BIGINT NOT NULL,
                        normal_emoji TEXT NOT NULL,
                        premium_emoji_id BIGINT NOT NULL,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(channel_id, normal_emoji)
                    )
                """)
                
                rows = await conn.fetch("SELECT channel_id, normal_emoji, premium_emoji_id FROM channel_emoji_replacements")
                self.channel_emoji_mappings = {}
                for row in rows:
                    channel_id = row['channel_id']
                    if channel_id not in self.channel_emoji_mappings:
                        self.channel_emoji_mappings[channel_id] = {}
                    self.channel_emoji_mappings[channel_id][row['normal_emoji']] = row['premium_emoji_id']
                
                total_mappings = sum(len(mappings) for mappings in self.channel_emoji_mappings.values())
                logger.info(f"Loaded {total_mappings} channel-specific emoji mappings for {len(self.channel_emoji_mappings)} channels")
        except Exception as e:
            logger.error(f"Failed to load channel emoji mappings: {e}")

    async def load_monitored_channels(self):
        """Load monitored channels from database into cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
        try:
            async with self.db_pool.acquire() as conn:
                # Add replacement_active column if it doesn't exist
                await conn.execute("""
                    ALTER TABLE monitored_channels 
                    ADD COLUMN IF NOT EXISTS replacement_active BOOLEAN DEFAULT TRUE
                """)
                
                rows = await conn.fetch(
                    "SELECT channel_id, channel_username, channel_title, replacement_active FROM monitored_channels WHERE is_active = TRUE"
                )
                self.monitored_channels = {
                    row['channel_id']: {
                        'username': row['channel_username'],
                        'title': row['channel_title']
                    }
                    for row in rows
                }
                
                # Load replacement activation status
                self.channel_replacement_status = {
                    row['channel_id']: row['replacement_active'] if row['replacement_active'] is not None else True
                    for row in rows
                }
                
                logger.info(f"Loaded {len(self.monitored_channels)} monitored channels from database")
                active_replacements = sum(1 for active in self.channel_replacement_status.values() if active)
                logger.info(f"Replacement active in {active_replacements} channels")
        except Exception as e:
            logger.error(f"Failed to load monitored channels: {e}")

    async def load_forwarding_tasks(self):
        """Load forwarding tasks from database into cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
        try:
            async with self.db_pool.acquire() as conn:
                # Create forwarding_tasks table if it doesn't exist
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS forwarding_tasks (
                        id SERIAL PRIMARY KEY,
                        source_channel_id BIGINT NOT NULL,
                        target_channel_id BIGINT NOT NULL,
                        is_active BOOLEAN DEFAULT TRUE,
                        description TEXT,
                        delay_seconds INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(source_channel_id, target_channel_id)
                    )
                """)
                
                # Add delay_seconds column if it doesn't exist
                await conn.execute("""
                    ALTER TABLE forwarding_tasks 
                    ADD COLUMN IF NOT EXISTS delay_seconds INTEGER DEFAULT 0
                """)
                
                rows = await conn.fetch(
                    "SELECT id, source_channel_id, target_channel_id, is_active, description, delay_seconds FROM forwarding_tasks WHERE is_active = TRUE"
                )
                
                self.forwarding_tasks = {}
                for row in rows:
                    task_id = row['id']
                    self.forwarding_tasks[task_id] = {
                        'source': row['source_channel_id'],
                        'target': row['target_channel_id'],
                        'active': row['is_active'],
                        'description': row['description'] or '',
                        'delay': row['delay_seconds'] or 0
                    }
                
                logger.info(f"Loaded {len(self.forwarding_tasks)} active forwarding tasks")
                
        except Exception as e:
            logger.error(f"Failed to load forwarding tasks: {e}")

    async def add_forwarding_task(self, source_channel_id: int, target_channel_id: int, description: Optional[str] = None, delay_seconds: int = 0) -> bool:
        """Add forwarding task to database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                # Insert new forwarding task
                task_id = await conn.fetchval(
                    """INSERT INTO forwarding_tasks (source_channel_id, target_channel_id, description, delay_seconds, is_active) 
                       VALUES ($1, $2, $3, $4, TRUE) 
                       ON CONFLICT (source_channel_id, target_channel_id) 
                       DO UPDATE SET is_active = TRUE, description = $3, delay_seconds = $4
                       RETURNING id""",
                    source_channel_id, target_channel_id, description, delay_seconds
                )
                
                # Update cache
                self.forwarding_tasks[task_id] = {
                    'source': source_channel_id,
                    'target': target_channel_id,
                    'active': True,
                    'description': description or '',
                    'delay': delay_seconds
                }
                
                logger.info(f"Added forwarding task: {source_channel_id} -> {target_channel_id} (delay: {delay_seconds}s)")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add forwarding task: {e}")
            return False

    async def delete_forwarding_task(self, task_id: int) -> bool:
        """Delete forwarding task from database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE forwarding_tasks SET is_active = FALSE WHERE id = $1",
                    task_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache
                    self.forwarding_tasks.pop(task_id, None)
                    logger.info(f"Deleted forwarding task: {task_id}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to delete forwarding task: {e}")
            return False

    async def activate_forwarding_task(self, task_id: int) -> bool:
        """Activate forwarding task"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE forwarding_tasks SET is_active = TRUE WHERE id = $1",
                    task_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache
                    if task_id in self.forwarding_tasks:
                        self.forwarding_tasks[task_id]['active'] = True
                    else:
                        # Reload from database if not in cache
                        await self.load_forwarding_tasks()
                    
                    logger.info(f"Activated forwarding task: {task_id}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to activate forwarding task: {e}")
            return False

    async def deactivate_forwarding_task(self, task_id: int) -> bool:
        """Deactivate forwarding task"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE forwarding_tasks SET is_active = FALSE WHERE id = $1",
                    task_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache
                    if task_id in self.forwarding_tasks:
                        self.forwarding_tasks[task_id]['active'] = False
                    
                    logger.info(f"Deactivated forwarding task: {task_id}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to deactivate forwarding task: {e}")
            return False

    async def forward_message_to_targets(self, source_channel_id: int, message):
        """Copy message content to all target channels for this source"""
        try:
            # Find active forwarding tasks for this source channel
            active_tasks = []
            for task_id, task_info in self.forwarding_tasks.items():
                if (task_info['source'] == source_channel_id and 
                    task_info['active'] and 
                    task_info['target'] in self.monitored_channels):
                    active_tasks.append(task_info)
            
            if not active_tasks:
                return
            
            logger.info(f"Found {len(active_tasks)} copying targets for channel {source_channel_id}")
            
            # Copy to each target
            for task in active_tasks:
                target_channel_id = task['target']
                delay_seconds = task.get('delay', 0)
                
                # If there's a delay, schedule the copy operation
                if delay_seconds > 0:
                    logger.info(f"Scheduling delayed copy from {source_channel_id} to {target_channel_id} (delay: {delay_seconds}s)")
                    asyncio.create_task(self._delayed_copy_message(
                        source_channel_id, target_channel_id, message, delay_seconds
                    ))
                else:
                    # Copy immediately
                    await self._copy_message_to_target(source_channel_id, target_channel_id, message)
            
        except Exception as e:
            logger.error(f"Failed to process copying for channel {source_channel_id}: {e}")

    async def _delayed_copy_message(self, source_channel_id: int, target_channel_id: int, message, delay_seconds: int):
        """Copy message after delay"""
        try:
            await asyncio.sleep(delay_seconds)
            await self._copy_message_to_target(source_channel_id, target_channel_id, message)
            logger.info(f"Delayed copy completed from {source_channel_id} to {target_channel_id} after {delay_seconds}s")
        except Exception as e:
            logger.error(f"Failed to perform delayed copy from {source_channel_id} to {target_channel_id}: {e}")

    async def _copy_message_to_target(self, source_channel_id: int, target_channel_id: int, message):
        """Copy message content to target channel"""
        try:
            # Copy the message content instead of forwarding
            if message.text or message.message:
                # Text message - preserve all formatting entities
                text_content = message.text or message.message
                
                # Use the original entities to preserve formatting (bold, italic, links, etc.)
                # Convert custom emojis back to markdown format for proper handling
                if message.entities:
                    # Use the CustomParseMode unparse to handle custom emojis properly
                    unparsed_text, unparsed_entities = CustomParseMode.unparse(text_content, message.entities)
                    
                    await self.client.send_message(
                        entity=target_channel_id,
                        message=unparsed_text,
                        formatting_entities=unparsed_entities,
                        parse_mode=None  # Use raw entities instead of parse mode
                    )
                else:
                    # No entities, send plain text
                    await self.client.send_message(
                        entity=target_channel_id,
                        message=text_content
                    )
                    
            elif message.media:
                # Media message (photo, video, document, etc.)
                caption = message.text or message.message or ""
                
                # Handle caption formatting entities the same way
                if message.entities and caption:
                    unparsed_caption, unparsed_entities = CustomParseMode.unparse(caption, message.entities)
                    
                    await self.client.send_file(
                        entity=target_channel_id,
                        file=message.media,
                        caption=unparsed_caption,
                        formatting_entities=unparsed_entities,
                        parse_mode=None  # Use raw entities instead of parse mode
                    )
                else:
                    await self.client.send_file(
                        entity=target_channel_id,
                        file=message.media,
                        caption=caption
                    )
            else:
                # Other types of messages
                logger.warning(f"Unsupported message type for copying from {source_channel_id}")
                return
            
            logger.info(f"Copied message from {source_channel_id} to {target_channel_id} with preserved formatting")
            
        except Exception as copy_error:
            logger.error(f"Failed to copy message from {source_channel_id} to {target_channel_id}: {copy_error}")

    async def load_admin_ids(self):
        """Load admin IDs from database into cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
        try:
            async with self.db_pool.acquire() as conn:
                # Create admins table if it doesn't exist
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS bot_admins (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT UNIQUE NOT NULL,
                        username TEXT,
                        added_by BIGINT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT TRUE
                    )
                """)
                
                # Add default admin if not exists
                await conn.execute("""
                    INSERT INTO bot_admins (user_id, username, added_by, is_active) 
                    VALUES ($1, 'Default Admin', $1, TRUE) 
                    ON CONFLICT (user_id) DO NOTHING
                """, 6602517122)
                
                # Load active admins
                rows = await conn.fetch("SELECT user_id FROM bot_admins WHERE is_active = TRUE")
                self.admin_ids = {row['user_id'] for row in rows}
                logger.info(f"Loaded {len(self.admin_ids)} admin IDs from database")
        except Exception as e:
            logger.error(f"Failed to load admin IDs: {e}")

    async def process_command_queue(self):
        """Process pending commands from control bot"""
        if self.db_pool is None:
            return
        
        try:
            async with self.db_pool.acquire() as conn:
                # Get pending commands
                commands = await conn.fetch(
                    "SELECT * FROM command_queue WHERE status = 'pending' ORDER BY created_at LIMIT 10"
                )
                
                for cmd_row in commands:
                    try:
                        command_id = cmd_row['id']
                        command = cmd_row['command']
                        args = cmd_row['args'] or ""
                        requested_by = cmd_row['requested_by']
                        
                        logger.info(f"Processing command queue ID {command_id}: {command}")
                        
                        # Mark as processing
                        await conn.execute(
                            "UPDATE command_queue SET status = 'processing' WHERE id = $1",
                            command_id
                        )
                        
                        # Execute command
                        result = await self.execute_queued_command(command, args, requested_by)
                        
                        # Update with result
                        await conn.execute(
                            "UPDATE command_queue SET status = 'completed', result = $1, processed_at = CURRENT_TIMESTAMP WHERE id = $2",
                            result, command_id
                        )
                        
                        # Send result to user if needed
                        if result and requested_by:
                            await self.send_result_to_user(requested_by, f"✅ نتيجة الأمر {command}:\n\n{result}")
                        
                    except Exception as cmd_error:
                        logger.error(f"Failed to process command {command_id}: {cmd_error}")
                        # Mark as failed
                        await conn.execute(
                            "UPDATE command_queue SET status = 'failed', result = $1, processed_at = CURRENT_TIMESTAMP WHERE id = $2",
                            str(cmd_error), command_id
                        )
                        
                        if requested_by:
                            await self.send_result_to_user(requested_by, f"❌ فشل تنفيذ الأمر {command}:\n{cmd_error}")
                
        except Exception as e:
            logger.error(f"Failed to process command queue: {e}")

    async def execute_queued_command(self, command: str, args: str, requested_by: int) -> str:
        """Execute a queued command and return result"""
        try:
            # Map command to internal method
            command_mapping = {
                'list_channels': self.get_channels_list,
                'list_global_emojis': self.get_global_emojis_list,
                'list_channel_emojis': self.get_channel_emojis_list,
                'list_forwarding_tasks': self.get_forwarding_tasks_list,
                'get_stats': self.get_system_stats,
            }
            
            if command in command_mapping:
                return await command_mapping[command]()
            else:
                return f"❌ أمر غير معروف: {command}"
                
        except Exception as e:
            logger.error(f"Failed to execute command {command}: {e}")
            return f"❌ خطأ في تنفيذ الأمر: {e}"

    async def get_channels_list(self) -> str:
        """Get formatted list of monitored channels"""
        if not self.monitored_channels:
            return "لا توجد قنوات مراقبة محفوظة"
        
        result = "📺 **قائمة القنوات المراقبة:**\n\n"
        for channel_id, info in self.monitored_channels.items():
            title = info['title'] or 'غير معروف'
            username = info['username'] or 'غير متاح'
            
            # Get replacement status
            is_active = self.channel_replacement_status.get(channel_id, True)
            status_icon = "✅" if is_active else "❌"
            status_text = "مُفعل" if is_active else "مُعطل"
            
            # Count replacements
            replacement_count = len(self.channel_emoji_mappings.get(channel_id, {}))
            
            result += f"• **{title}** (@{username})\n"
            result += f"  📋 المعرف: `{channel_id}`\n"
            result += f"  🔄 الاستبدال: {status_icon} {status_text}\n"
            result += f"  📝 الاستبدالات: {replacement_count}\n\n"
        
        return result

    async def get_global_emojis_list(self) -> str:
        """Get formatted list of global emoji replacements"""
        if not self.emoji_mappings:
            return "لا توجد استبدالات إيموجي عامة محفوظة"
        
        result = "😀 **قائمة الاستبدالات العامة:**\n\n"
        count = 0
        for normal_emoji, premium_id in self.emoji_mappings.items():
            result += f"• {normal_emoji} → `{premium_id}`\n"
            count += 1
            if count >= 20:  # Limit to prevent very long messages
                result += f"\n... وعدد {len(self.emoji_mappings) - 20} استبدال آخر"
                break
        
        return result

    async def get_channel_emojis_list(self) -> str:
        """Get formatted list of channel-specific emoji replacements"""
        if not self.channel_emoji_mappings:
            return "لا توجد استبدالات إيموجي خاصة بالقنوات"
        
        result = "🎯 **استبدالات القنوات:**\n\n"
        for channel_id, mappings in self.channel_emoji_mappings.items():
            channel_name = self.monitored_channels.get(channel_id, {}).get('title', f'القناة {channel_id}')
            result += f"📺 **{channel_name}** (`{channel_id}`):\n"
            
            count = 0
            for normal_emoji, premium_id in mappings.items():
                result += f"  • {normal_emoji} → `{premium_id}`\n"
                count += 1
                if count >= 10:  # Limit per channel
                    result += f"  ... وعدد {len(mappings) - 10} استبدال آخر\n"
                    break
            result += "\n"
        
        return result

    async def get_forwarding_tasks_list(self) -> str:
        """Get formatted list of forwarding tasks"""
        if not self.forwarding_tasks:
            return "لا توجد مهام نسخ محفوظة"
        
        result = "🔄 **قائمة مهام النسخ:**\n\n"
        for task_id, task_info in self.forwarding_tasks.items():
            source_id = task_info['source']
            target_id = task_info['target']
            is_active = task_info['active']
            delay = task_info.get('delay', 0)
            description = task_info['description']

            source_name = self.monitored_channels.get(source_id, {}).get('title', f'القناة {source_id}')
            target_name = self.monitored_channels.get(target_id, {}).get('title', f'القناة {target_id}')

            status_icon = "✅" if is_active else "❌"
            status_text = "مُفعلة" if is_active else "مُعطلة"

            result += f"🆔 **المهمة:** `{task_id}`\n"
            result += f"📤 **من:** {source_name}\n"
            result += f"📥 **إلى:** {target_name}\n"
            result += f"🔄 **الحالة:** {status_icon} {status_text}\n"
            result += f"⏱️ **التأخير:** {delay} ثانية\n"
            
            if description:
                result += f"📝 **الوصف:** {description}\n"
            
            result += "\n"

        return result

    async def get_system_stats(self) -> str:
        """Get system statistics"""
        stats = f"""📊 **إحصائيات النظام:**

📺 **القنوات:**
• المراقبة: {len(self.monitored_channels)}
• الاستبدال المفعل: {sum(1 for active in self.channel_replacement_status.values() if active)}

😀 **الاستبدالات:**
• العامة: {len(self.emoji_mappings)}
• الخاصة بالقنوات: {sum(len(mappings) for mappings in self.channel_emoji_mappings.values())}
• الإجمالي: {len(self.emoji_mappings) + sum(len(mappings) for mappings in self.channel_emoji_mappings.values())}

🔄 **مهام النسخ:**
• النشطة: {len(self.forwarding_tasks)}
• المعطلة: يتم حسابها من قاعدة البيانات

👥 **الإدارة:**
• الأدمن: {len(self.admin_ids)}
"""
        return stats

    async def send_result_to_user(self, user_id: int, message: str):
        """Send result message to user"""
        try:
            await self.client.send_message(user_id, message)
            logger.info(f"Sent result to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send result to user {user_id}: {e}")

    async def start_command_queue_processor(self):
        """Start periodic command queue processing"""
        while True:
            try:
                await self.process_command_queue()
                await asyncio.sleep(5)  # Check every 5 seconds
            except Exception as e:
                logger.error(f"Command queue processor error: {e}")
                await asyncio.sleep(10)  # Wait longer on error

    async def add_admin(self, user_id: int, username: str = None, added_by: int = None) -> bool:
        """Add admin to database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO bot_admins (user_id, username, added_by, is_active) 
                       VALUES ($1, $2, $3, TRUE) 
                       ON CONFLICT (user_id) 
                       DO UPDATE SET username = $2, added_by = $3, is_active = TRUE""",
                    user_id, username, added_by
                )
                
                # Update cache
                self.admin_ids.add(user_id)
                logger.info(f"Added admin: {user_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add admin: {e}")
            return False

    async def remove_admin(self, user_id: int) -> bool:
        """Remove admin from database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
            
        # Prevent removing the default admin
        if user_id == 6602517122:
            return False
            
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE bot_admins SET is_active = FALSE WHERE user_id = $1",
                    user_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache
                    self.admin_ids.discard(user_id)
                    logger.info(f"Removed admin: {user_id}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to remove admin: {e}")
            return False

    async def add_emoji_replacement(self, normal_emoji: str, premium_emoji_id: int, description: Optional[str] = None) -> bool:
        """Add or update emoji replacement in database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO emoji_replacements (normal_emoji, premium_emoji_id, description) 
                       VALUES ($1, $2, $3) 
                       ON CONFLICT (normal_emoji) 
                       DO UPDATE SET premium_emoji_id = $2, description = $3""",
                    normal_emoji, premium_emoji_id, description
                )
                
                # Update cache
                self.emoji_mappings[normal_emoji] = premium_emoji_id
                logger.info(f"Added/updated emoji replacement: {normal_emoji} -> {premium_emoji_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add emoji replacement: {e}")
            return False

    async def delete_emoji_replacement(self, normal_emoji: str) -> bool:
        """Delete emoji replacement from database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM emoji_replacements WHERE normal_emoji = $1",
                    normal_emoji
                )
                
                if result == 'DELETE 1':
                    # Update cache
                    self.emoji_mappings.pop(normal_emoji, None)
                    logger.info(f"Deleted emoji replacement: {normal_emoji}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to delete emoji replacement: {e}")
            return False

    async def delete_all_emoji_replacements(self) -> int:
        """Delete all global emoji replacements from database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return 0
        try:
            async with self.db_pool.acquire() as conn:
                # Get count before deletion
                count_result = await conn.fetchval("SELECT COUNT(*) FROM emoji_replacements")
                
                # Delete all replacements
                await conn.execute("DELETE FROM emoji_replacements")
                
                # Clear cache
                self.emoji_mappings.clear()
                logger.info(f"Deleted all {count_result} emoji replacements")
                return count_result
                
        except Exception as e:
            logger.error(f"Failed to delete all emoji replacements: {e}")
            return 0

    async def add_channel_emoji_replacement(self, channel_id: int, normal_emoji: str, premium_emoji_id: int, description: Optional[str] = None) -> bool:
        """Add or update channel-specific emoji replacement in database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO channel_emoji_replacements (channel_id, normal_emoji, premium_emoji_id, description) 
                       VALUES ($1, $2, $3, $4) 
                       ON CONFLICT (channel_id, normal_emoji) 
                       DO UPDATE SET premium_emoji_id = $3, description = $4""",
                    channel_id, normal_emoji, premium_emoji_id, description
                )
                
                # Update cache
                if channel_id not in self.channel_emoji_mappings:
                    self.channel_emoji_mappings[channel_id] = {}
                self.channel_emoji_mappings[channel_id][normal_emoji] = premium_emoji_id
                logger.info(f"Added/updated channel {channel_id} emoji replacement: {normal_emoji} -> {premium_emoji_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add channel emoji replacement: {e}")
            return False

    async def delete_channel_emoji_replacement(self, channel_id: int, normal_emoji: str) -> bool:
        """Delete channel-specific emoji replacement from database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM channel_emoji_replacements WHERE channel_id = $1 AND normal_emoji = $2",
                    channel_id, normal_emoji
                )
                
                if result == 'DELETE 1':
                    # Update cache
                    if channel_id in self.channel_emoji_mappings:
                        self.channel_emoji_mappings[channel_id].pop(normal_emoji, None)
                        if not self.channel_emoji_mappings[channel_id]:
                            del self.channel_emoji_mappings[channel_id]
                    logger.info(f"Deleted channel {channel_id} emoji replacement: {normal_emoji}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to delete channel emoji replacement: {e}")
            return False

    async def delete_all_channel_emoji_replacements(self, channel_id: int) -> int:
        """Delete all emoji replacements for a specific channel from database and cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return 0
        try:
            async with self.db_pool.acquire() as conn:
                # Get count before deletion
                count_result = await conn.fetchval(
                    "SELECT COUNT(*) FROM channel_emoji_replacements WHERE channel_id = $1",
                    channel_id
                )
                
                # Delete all replacements for this channel
                await conn.execute(
                    "DELETE FROM channel_emoji_replacements WHERE channel_id = $1",
                    channel_id
                )
                
                # Clear cache for this channel
                if channel_id in self.channel_emoji_mappings:
                    del self.channel_emoji_mappings[channel_id]
                    
                logger.info(f"Deleted all {count_result} emoji replacements for channel {channel_id}")
                return count_result
                
        except Exception as e:
            logger.error(f"Failed to delete all channel emoji replacements: {e}")
            return 0

    async def get_channel_emoji_replacements(self, channel_id: int) -> Dict[str, int]:
        """Get all emoji replacements for a specific channel"""
        return self.channel_emoji_mappings.get(channel_id, {})

    async def add_monitored_channel(self, channel_id: int, channel_username: Optional[str] = None, channel_title: Optional[str] = None) -> bool:
        """Add channel to monitoring list"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO monitored_channels (channel_id, channel_username, channel_title, is_active, replacement_active) 
                       VALUES ($1, $2, $3, TRUE, TRUE) 
                       ON CONFLICT (channel_id) 
                       DO UPDATE SET channel_username = $2, channel_title = $3, is_active = TRUE, replacement_active = COALESCE(monitored_channels.replacement_active, TRUE)""",
                    channel_id, channel_username, channel_title
                )
                
                # Update cache
                self.monitored_channels[channel_id] = {
                    'username': channel_username or '',
                    'title': channel_title or ''
                }
                # Set default replacement status to active for new channels
                if channel_id not in self.channel_replacement_status:
                    self.channel_replacement_status[channel_id] = True
                    
                logger.info(f"Added/updated monitored channel: {channel_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add monitored channel: {e}")
            return False

    async def remove_monitored_channel(self, channel_id: int) -> bool:
        """Remove channel from monitoring list and all its emoji replacements"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                # First, delete all channel-specific emoji replacements
                emoji_delete_result = await conn.execute(
                    "DELETE FROM channel_emoji_replacements WHERE channel_id = $1",
                    channel_id
                )
                
                # Extract count from delete result (format: 'DELETE n')
                emoji_deleted_count = 0
                if emoji_delete_result and emoji_delete_result.startswith('DELETE '):
                    try:
                        emoji_deleted_count = int(emoji_delete_result.split(' ')[1])
                    except (IndexError, ValueError):
                        emoji_deleted_count = 0
                
                # Then remove the channel from monitoring
                result = await conn.execute(
                    "UPDATE monitored_channels SET is_active = FALSE WHERE channel_id = $1",
                    channel_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache - remove channel
                    self.monitored_channels.pop(channel_id, None)
                    
                    # Update cache - remove channel emoji mappings
                    if channel_id in self.channel_emoji_mappings:
                        del self.channel_emoji_mappings[channel_id]
                    
                    logger.info(f"Removed monitored channel: {channel_id}")
                    if emoji_deleted_count > 0:
                        logger.info(f"Also deleted {emoji_deleted_count} channel-specific emoji replacements")
                    
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to remove monitored channel: {e}")
            return False

    def extract_emojis_from_text(self, text: str) -> List[str]:
        """Extract all unique emojis and symbols from text using regex - handles composite emojis, variation selectors, and special symbols"""
        
        # Debug: Print Unicode codepoints for each character
        logger.info(f"Text analysis for: '{text}'")
        for i, char in enumerate(text):
            unicode_point = ord(char)
            logger.info(f"  Char {i}: '{char}' -> U+{unicode_point:04X}")
        
        # Enhanced regex pattern to match composite emojis and common symbols
        emoji_pattern = re.compile(
            r"("
            # Standard emoji ranges
            r"[\U0001F600-\U0001F64F"  # emoticons
            r"\U0001F300-\U0001F5FF"   # symbols & pictographs
            r"\U0001F680-\U0001F6FF"   # transport & map
            r"\U0001F1E0-\U0001F1FF"   # flags (iOS)
            r"\U00002700-\U000027BF"   # dingbats
            r"\U0001F900-\U0001F9FF"   # supplemental symbols
            r"\U00002600-\U000026FF"   # miscellaneous symbols
            r"\U0001F170-\U0001F251"   # enclosed characters
            r"\U0001F7E0-\U0001F7FF"   # geometric shapes extended
            r"\U00002190-\U000021FF"   # arrows
            r"\U00002100-\U0000214F"   # letterlike symbols
            r"\U00002150-\U0000218F"   # number forms
            r"\U00002460-\U000024FF"   # enclosed alphanumerics
            r"\U000025A0-\U000025FF"   # geometric shapes
            r"\U00002B00-\U00002BFF"   # miscellaneous symbols and arrows
            r"\U0001F004"              # mahjong tile red dragon
            r"\U0001F0CF"              # playing card black joker
            r"\U0001F18E"              # negative squared ab
            r"\U0001F191-\U0001F19A"   # squared symbols
            r"\U0001F1E6-\U0001F1FF"   # regional indicator symbols
            r"\U0001F201-\U0001F202"   # squared symbols
            r"\U0001F21A"              # squared cjk unified ideograph-7121
            r"\U0001F22F"              # squared cjk unified ideograph-6307
            r"\U0001F232-\U0001F23A"   # squared symbols
            r"\U0001F250-\U0001F251"   # circled symbols
            # Common punctuation and symbols that users might want to replace
            r"\U00002022"              # bullet point •
            r"\U00002023"              # triangular bullet ‣
            r"\U00002043"              # hyphen bullet ⁃
            r"\U0000204C"              # black leftwards bullet ⁌
            r"\U0000204D"              # black rightwards bullet ⁍
            r"\U000025E6"              # white bullet ◦
            r"\U00002219"              # bullet operator ∙
            r"\U000000B7"              # middle dot ·
            r"\U000025AA"              # black small square ▪
            r"\U000025AB"              # white small square ▫
            r"\U000025B6"              # black right-pointing triangle ▶
            r"\U000025C0"              # black left-pointing triangle ◀
            r"\U000025CF"              # black circle ●
            r"\U000025CB"              # white circle ○
            r"\U000025A0"              # black square ■
            r"\U000025A1"              # white square □
            r"\U00002713"              # check mark ✓
            r"\U00002714"              # heavy check mark ✔
            r"\U00002717"              # ballot x ✗
            r"\U00002718"              # heavy ballot x ✘
            r"\U0000274C"              # cross mark ❌
            r"\U00002705"              # white heavy check mark ✅
            r"\U0000274E"              # negative squared cross mark ❎
            r"\U000027A1"              # black rightwards arrow ➡
            r"\U00002B05"              # leftwards black arrow ⬅
            r"\U00002B06"              # upwards black arrow ⬆
            r"\U00002B07"              # downwards black arrow ⬇
            r"\U000021A9"              # leftwards arrow with hook ↩
            r"\U000021AA"              # rightwards arrow with hook ↪
            r"]"
            r"[\U0000FE00-\U0000FE0F]?"  # optional variation selectors
            r"(?:\U0000200D"             # zero-width joiner (for compound emojis)
            r"[\U0001F600-\U0001F64F"
            r"\U0001F300-\U0001F5FF"
            r"\U0001F680-\U0001F6FF"
            r"\U0001F1E0-\U0001F1FF"
            r"\U00002700-\U000027BF"
            r"\U0001F900-\U0001F9FF"
            r"\U00002600-\U000026FF"
            r"\U0001F170-\U0001F251"
            r"\U0001F7E0-\U0001F7FF"
            r"\U00002190-\U000021FF"
            r"\U00002100-\U0000214F"
            r"\U00002150-\U0000218F"
            r"\U00002460-\U000024FF"
            r"\U000025A0-\U000025FF"
            r"\U00002B00-\U00002BFF"
            r"\U0001F004"
            r"\U0001F0CF"
            r"\U0001F18E"
            r"\U0001F191-\U0001F19A"
            r"\U0001F1E6-\U0001F1FF"
            r"\U0001F201-\U0001F202"
            r"\U0001F21A"
            r"\U0001F22F"
            r"\U0001F232-\U0001F23A"
            r"\U0001F250-\U0001F251"
            r"\U00002022"
            r"\U00002023"
            r"\U00002043"
            r"\U0000204C"
            r"\U0000204D"
            r"\U000025E6"
            r"\U00002219"
            r"\U000000B7"
            r"\U000025AA"
            r"\U000025AB"
            r"\U000025B6"
            r"\U000025C0"
            r"\U000025CF"
            r"\U000025CB"
            r"\U000025A0"
            r"\U000025A1"
            r"\U00002713"
            r"\U00002714"
            r"\U00002717"
            r"\U00002718"
            r"\U0000274C"
            r"\U00002705"
            r"\U0000274E"
            r"\U000027A1"
            r"\U00002B05"
            r"\U00002B06"
            r"\U00002B07"
            r"\U000021A9"
            r"\U000021AA"
            r"][\U0000FE00-\U0000FE0F]?)?"  # optional second emoji component with variation selector
            r")",
            flags=re.UNICODE
        )
        
        # Get all emojis and symbols found in text
        found_emojis = emoji_pattern.findall(text)
        logger.info(f"Regex found emojis/symbols: {found_emojis}")
        
        # Fallback: Check each character individually for common symbols that might be missed
        fallback_symbols = {
            '•': '\U00002022',  # bullet point
            '◦': '\U000025E6',  # white bullet
            '▪': '\U000025AA',  # black small square
            '▫': '\U000025AB',  # white small square
            '●': '\U000025CF',  # black circle
            '○': '\U000025CB',  # white circle
            '■': '\U000025A0',  # black square
            '□': '\U000025A1',  # white square
            '✓': '\U00002713',  # check mark
            '✔': '\U00002714',  # heavy check mark
            '✗': '\U00002717',  # ballot x
            '✘': '\U00002718',  # heavy ballot x
            '❌': '\U0000274C', # cross mark
            '✅': '\U00002705', # white heavy check mark
            '❎': '\U0000274E', # negative squared cross mark
            '➡': '\U000027A1',  # black rightwards arrow
            '⬅': '\U00002B05',  # leftwards black arrow
            '⬆': '\U00002B06',  # upwards black arrow
            '⬇': '\U00002B07',  # downwards black arrow
            '↩': '\U000021A9',  # leftwards arrow with hook
            '↪': '\U000021AA',  # rightwards arrow with hook
            '·': '\U000000B7',  # middle dot
            '∙': '\U00002219',  # bullet operator
        }
        
        # Check for symbols that might have been missed by the regex
        for char in text:
            if char in fallback_symbols and char not in found_emojis:
                found_emojis.append(char)
                logger.info(f"Fallback found symbol: {char} (U+{ord(char):04X})")
        
        # Return unique emojis while preserving order
        unique_emojis = []
        seen = set()
        for emoji in found_emojis:
            if emoji and emoji not in seen:
                unique_emojis.append(emoji)
                seen.add(emoji)
        
        logger.info(f"Final unique emojis/symbols: {unique_emojis}")
        return unique_emojis

    async def replace_emojis_in_message(self, event):
        """Replace normal emojis with premium emojis in a message"""
        try:
            message = event.message
            original_text = message.text or message.message
            
            logger.info(f"Attempting to replace emojis in message: '{original_text}'")
            
            if not original_text:
                logger.info("No text in message, skipping emoji replacement")
                return
            
            # Extract emojis from the message
            found_emojis = self.extract_emojis_from_text(original_text)
            logger.info(f"Found emojis in text: {found_emojis}")
            
            if not found_emojis:
                logger.info("No emojis found in message text")
                return
            
            # Check if replacement is enabled for this channel
            event_peer_id = utils.get_peer_id(event.chat)
            replacement_enabled = self.channel_replacement_status.get(event_peer_id, True)
            
            if not replacement_enabled:
                logger.info(f"Replacement disabled for channel {event_peer_id}, skipping")
                return
            
            # Check if any of the found emojis have premium replacements
            replacements_made = []
            modified_text = original_text
            
            # Process text character by character to handle multiple same emojis correctly
            import re
            
            # Create a list to track which emojis need replacement
            # Priority: Channel-specific replacements first, then global replacements
            emojis_to_replace = {}
            
            for emoji in found_emojis:
                # Check channel-specific replacements first
                if (event_peer_id in self.channel_emoji_mappings and 
                    emoji in self.channel_emoji_mappings[event_peer_id]):
                    emojis_to_replace[emoji] = self.channel_emoji_mappings[event_peer_id][emoji]
                    replacements_made.append(emoji)
                    logger.info(f"Found channel-specific replacement for {emoji}: {self.channel_emoji_mappings[event_peer_id][emoji]}")
                # Then check global replacements
                elif emoji in self.emoji_mappings:
                    emojis_to_replace[emoji] = self.emoji_mappings[emoji]
                    replacements_made.append(emoji)
                    logger.info(f"Found global replacement for {emoji}: {self.emoji_mappings[emoji]}")
                else:
                    logger.info(f"No replacement found for emoji: {emoji}")
            
            if not emojis_to_replace:
                return
            
            # Use regex to replace emojis one by one to avoid conflicts
            for normal_emoji, premium_emoji_id in emojis_to_replace.items():
                # Escape special regex characters in emoji
                escaped_emoji = re.escape(normal_emoji)
                premium_emoji_markdown = f"[{normal_emoji}](emoji/{premium_emoji_id})"
                
                # Replace all occurrences of this specific emoji
                modified_text = re.sub(escaped_emoji, premium_emoji_markdown, modified_text)
            
            # If replacements were made, edit the message
            if replacements_made:
                try:
                    # Parse the text with custom parse mode to handle premium emojis
                    try:
                        parsed_text, entities = self.parse_mode.parse(modified_text)
                    except Exception as parse_error:
                        logger.error(f"Failed to parse premium emojis in text: {parse_error}")
                        logger.error(f"Modified text: {modified_text}")
                        return
                    
                    # Edit the original message
                    await self.client.edit_message(
                        event.chat_id,
                        message.id,
                        parsed_text,
                        formatting_entities=entities
                    )
                    
                    logger.info(f"Replaced emojis in message {message.id}: {list(emojis_to_replace.keys())}")
                    
                except Exception as edit_error:
                    logger.error(f"Failed to edit message {message.id}: {edit_error}")
            
        except Exception as e:
            logger.error(f"Failed to replace emojis in message: {e}")

    async def handle_private_message(self, event):
        """Handle private messages with Arabic commands"""
        try:
            message_text = event.message.text.strip()
            chat_id = event.chat_id
            sender_id = event.sender_id
            logger.info(f"Handling private message: '{message_text}' from {chat_id}, sender: {sender_id}")
            
            # Check if sender is admin - silently ignore if not
            if sender_id not in self.admin_ids:
                logger.info(f"Unauthorized access attempt from user {sender_id} - ignoring silently")
                return
            
            # Parse command and arguments
            parts = message_text.split(None, 1)
            command = parts[0]
            args = parts[1] if len(parts) > 1 else ""
            logger.info(f"Parsed command: '{command}', args: '{args}'")
            
            # Find matching Arabic command - exact match first, then startswith
            command_handler = None
            
            # Try exact match first
            if command in self.arabic_commands:
                handler_name = self.arabic_commands[command]
                logger.info(f"Found exact command match: {command} -> {handler_name}")
                command_handler = getattr(self, f"cmd_{handler_name}", None)
            elif command.startswith('/') and command[1:] in self.arabic_commands:
                handler_name = self.arabic_commands[command[1:]]
                logger.info(f"Found exact command match with slash: {command} -> {handler_name}")
                command_handler = getattr(self, f"cmd_{handler_name}", None)
            else:
                # Fall back to startswith for partial matches
                for arabic_cmd, handler_name in self.arabic_commands.items():
                    logger.info(f"Checking command '{command}' against '{arabic_cmd}'")
                    if command.startswith(arabic_cmd) or command.startswith(f"/{arabic_cmd}"):
                        logger.info(f"Found matching command: {arabic_cmd} -> {handler_name}")
                        command_handler = getattr(self, f"cmd_{handler_name}", None)
                        logger.info(f"Handler method: {command_handler}")
                        break
            
            if command_handler:
                logger.info(f"Executing command handler: {command_handler.__name__}")
                await command_handler(event, args)
                logger.info("Command handler executed successfully")
            else:
                logger.info("No matching command found, ignoring silently")
                # Silently ignore unknown commands instead of sending error message
                
        except Exception as e:
            logger.error(f"Failed to handle private message: {e}")
            await event.reply("حدث خطأ أثناء معالجة الأمر.")

    async def cmd_add_emoji_replacement(self, event, args: str):
        """Handle add emoji replacement command - supports single or multiple replacements and reply messages"""
        try:
            # Check if this is a reply to a message
            reply_message = None
            if event.message.is_reply:
                reply_message = await event.message.get_reply_message()
            
            # Check if args contain multiple lines (multiple replacements)
            lines = args.strip().split('\n')
            
            if not args.strip() and not reply_message:
                await event.reply("""
📋 الاستخدام: إضافة_استبدال

🔸 استبدال واحد:
إضافة_استبدال <إيموجي_عادي> <إيموجي_مميز> [وصف]

🔸 عدة إيموجيات عادية لإيموجي مميز واحد:
إضافة_استبدال ✅,🟢,☑️ <إيموجي_مميز> [وصف]

🔸 عدة استبدالات (كل سطر منفصل):
إضافة_استبدال
😀 🔥 وصف أول
❤️,💖,💕 1234567890 وصف ثاني
✅ ✨ وصف ثالث

🔸 الرد على رسالة:
رد على رسالة تحتوي على إيموجيات عادية ومميزة بـ "إضافة_استبدال [وصف]"

💡 يمكنك استخدام الإيموجي المميز مباشرة أو معرفه الرقمي
💡 فصل الإيموجيات العادية بفاصلة (,) لربطها بنفس الإيموجي المميز
                """.strip())
                return
            
            # Handle reply message mode
            if reply_message:
                return await self._handle_reply_emoji_replacement(event, reply_message, args.strip())
            
            # Get all custom emojis from the message
            custom_emoji_ids = []
            if event.message.entities:
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        custom_emoji_ids.append(entity.document_id)
            
            successful_replacements = []
            failed_replacements = []
            custom_emoji_index = 0
            
            # Process each line
            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line:
                    continue
                
                # Parse line: "normal_emoji(s) premium_emoji/id description"
                parts = line.split(None, 2)
                if len(parts) < 2:
                    failed_replacements.append(f"السطر {line_num}: تنسيق غير صحيح")
                    continue
                
                normal_emojis_part = parts[0]
                premium_part = parts[1]
                description = parts[2] if len(parts) > 2 else None
                
                # Split normal emojis by comma to support multiple emojis
                normal_emojis = [emoji.strip() for emoji in normal_emojis_part.split(',') if emoji.strip()]
                
                if not normal_emojis:
                    failed_replacements.append(f"السطر {line_num}: لا توجد إيموجيات عادية صالحة")
                    continue
                
                # Try to determine premium emoji ID
                premium_emoji_id = None
                
                # Method 1: Try to parse as number (ID format)
                try:
                    premium_emoji_id = int(premium_part)
                except ValueError:
                    # Method 2: Check if it's a premium emoji in the message
                    # We need to find which custom emoji corresponds to this position
                    if custom_emoji_index < len(custom_emoji_ids):
                        premium_emoji_id = custom_emoji_ids[custom_emoji_index]
                        custom_emoji_index += 1
                    else:
                        # Method 3: If no more custom emojis available, this line fails
                        failed_replacements.append(f"السطر {line_num}: لم أجد إيموجي مميز أو معرف صحيح")
                        continue
                
                # Check which emojis are new and which already exist
                new_emojis = []
                existing_emojis = []
                
                for normal_emoji in normal_emojis:
                    if normal_emoji in self.emoji_mappings:
                        existing_emojis.append(normal_emoji)
                    else:
                        new_emojis.append(normal_emoji)
                
                # Add replacements only for new emojis
                line_success_count = 0
                line_failed_emojis = []
                
                for normal_emoji in new_emojis:
                    success = await self.add_emoji_replacement(normal_emoji, premium_emoji_id, description)
                    
                    if success:
                        line_success_count += 1
                    else:
                        line_failed_emojis.append(normal_emoji)
                
                # Report results for this line with premium emoji display
                if line_success_count > 0:
                    emoji_list = ", ".join(new_emojis[:line_success_count])
                    premium_emoji_markdown = f"[💎](emoji/{premium_emoji_id})"
                    successful_replacements.append(f"{emoji_list} → {premium_emoji_markdown} (ID: {premium_emoji_id})")
                
                if existing_emojis:
                    existing_emoji_list = ", ".join(existing_emojis)
                    failed_replacements.append(f"السطر {line_num}: موجود مسبقاً: {existing_emoji_list}")
                
                if line_failed_emojis:
                    failed_emoji_list = ", ".join(line_failed_emojis)
                    failed_replacements.append(f"السطر {line_num}: فشل في حفظ {failed_emoji_list}")
            
            # Prepare response with premium emojis
            response_parts = []
            fallback_parts = []
            
            if successful_replacements:
                response_parts.append("✅ تم إضافة الاستبدالات التالية بنجاح:")
                fallback_parts.append("✅ تم إضافة الاستبدالات التالية بنجاح:")
                for replacement in successful_replacements:
                    response_parts.append(f"• {replacement}")
                    # Create fallback version
                    fallback_parts.append(f"• {replacement.replace('إيموجي مميز', 'إيموجي مميز')}")
            
            if failed_replacements:
                if successful_replacements:
                    response_parts.append("")
                    fallback_parts.append("")
                response_parts.append("❌ فشل في إضافة الاستبدالات التالية:")
                fallback_parts.append("❌ فشل في إضافة الاستبدالات التالية:")
                for failure in failed_replacements:
                    response_parts.append(f"• {failure}")
                    fallback_parts.append(f"• {failure}")
            
            if not successful_replacements and not failed_replacements:
                response_parts.append("❌ لم يتم العثور على استبدالات صالحة")
                fallback_parts.append("❌ لم يتم العثور على استبدالات صالحة")
            
            # Try to send with premium emojis first
            try:
                response_text = "\n".join(response_parts)
                parsed_text, entities = self.parse_mode.parse(response_text)
                await self.client.send_message(
                    event.chat_id,
                    parsed_text,
                    formatting_entities=entities
                )
            except Exception as parse_error:
                logger.error(f"Failed to parse premium emojis in add_emoji_replacement response: {parse_error}")
                # Use fallback format
                fallback_response = "\n".join(fallback_parts)
                await event.reply(fallback_response)
                
        except Exception as e:
            logger.error(f"Failed to add emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء إضافة استبدال الإيموجي")

    async def _handle_reply_emoji_replacement(self, event, reply_message, description: str):
        """Handle emoji replacement when replying to a message"""
        try:
            # Extract emojis from reply message text
            reply_text = reply_message.text or reply_message.message or ""
            normal_emojis = self.extract_emojis_from_text(reply_text)
            
            # Get custom emojis from reply message
            custom_emoji_ids = []
            if reply_message.entities:
                for entity in reply_message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        custom_emoji_ids.append(entity.document_id)
            
            if not normal_emojis and not custom_emoji_ids:
                await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجيات")
                return
            
            if not custom_emoji_ids:
                await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجيات مميزة")
                return
            
            successful_replacements = []
            failed_replacements = []
            existing_emojis = []
            
            # Use the first premium emoji for all normal emojis
            premium_emoji_id = custom_emoji_ids[0]
            
            if not normal_emojis:
                await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجيات عادية")
                return
            
            # Process each normal emoji
            for normal_emoji in normal_emojis:
                if normal_emoji in self.emoji_mappings:
                    existing_emojis.append(normal_emoji)
                    continue
                
                success = await self.add_emoji_replacement(normal_emoji, premium_emoji_id, description or f"من الرد على الرسالة")
                
                if success:
                    successful_replacements.append(normal_emoji)
                else:
                    failed_replacements.append(normal_emoji)
            
            # Prepare response with premium emoji display
            response_parts = []
            fallback_parts = []
            
            if successful_replacements:
                emoji_list = ", ".join(successful_replacements)
                premium_emoji_markdown = f"[💎](emoji/{premium_emoji_id})"
                
                response_parts.append(f"✅ تم إضافة الاستبدالات التالية بنجاح:")
                response_parts.append(f"• {emoji_list} → {premium_emoji_markdown} (ID: {premium_emoji_id})")
                
                fallback_parts.append(f"✅ تم إضافة الاستبدالات التالية بنجاح:")
                fallback_parts.append(f"• {emoji_list} → إيموجي مميز (ID: {premium_emoji_id})")
            
            if existing_emojis:
                if successful_replacements:
                    response_parts.append("")
                    fallback_parts.append("")
                response_parts.append(f"⚠️ موجود مسبقاً: {', '.join(existing_emojis)}")
                fallback_parts.append(f"⚠️ موجود مسبقاً: {', '.join(existing_emojis)}")
            
            if failed_replacements:
                if successful_replacements or existing_emojis:
                    response_parts.append("")
                    fallback_parts.append("")
                response_parts.append(f"❌ فشل في إضافة: {', '.join(failed_replacements)}")
                fallback_parts.append(f"❌ فشل في إضافة: {', '.join(failed_replacements)}")
            
            if not response_parts:
                response_parts.append("❌ لم يتم إضافة أي استبدالات")
                fallback_parts.append("❌ لم يتم إضافة أي استبدالات")
            
            # Try to send with premium emojis first
            try:
                response_text = "\n".join(response_parts)
                parsed_text, entities = self.parse_mode.parse(response_text)
                await self.client.send_message(
                    event.chat_id,
                    parsed_text,
                    formatting_entities=entities
                )
            except Exception as parse_error:
                logger.error(f"Failed to parse premium emojis in reply replacement response: {parse_error}")
                # Use fallback format
                fallback_response = "\n".join(fallback_parts)
                await event.reply(fallback_response)
            
        except Exception as e:
            logger.error(f"Failed to handle reply emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء معالجة الرد")

    async def cmd_list_emoji_replacements(self, event, args: str):
        """Handle list emoji replacements command"""
        try:
            if not self.emoji_mappings:
                await event.reply("لا توجد استبدالات إيموجي محفوظة")
                return
            
            # Create response parts for both methods
            response_parts = ["📋 قائمة استبدالات الإيموجي:\n"]
            fallback_parts = ["📋 قائمة استبدالات الإيموجي:\n"]
            
            for normal_emoji, premium_id in self.emoji_mappings.items():
                # For premium emoji display: normal → premium → (ID)
                premium_emoji_markdown = f"[💎](emoji/{premium_id})"
                response_parts.append(f"{normal_emoji} → {premium_emoji_markdown} → (ID: {premium_id})")
                
                # Fallback format
                fallback_parts.append(f"{normal_emoji} → إيموجي مميز → (ID: {premium_id})")
            
            # Try to send with premium emojis first
            try:
                response_text = "\n".join(response_parts)
                parsed_text, entities = self.parse_mode.parse(response_text)
                
                await self.client.send_message(
                    event.chat_id,
                    parsed_text,
                    formatting_entities=entities
                )
                logger.info("Successfully sent emoji list with premium emojis")
                
            except Exception as parse_error:
                logger.error(f"Failed to parse premium emojis in list: {parse_error}")
                # Use fallback format
                fallback_response = "\n".join(fallback_parts)
                await event.reply(fallback_response)
                logger.info("Sent emoji list using fallback format")
            
        except Exception as e:
            logger.error(f"Failed to list emoji replacements: {e}")
            await event.reply("حدث خطأ أثناء عرض قائمة الاستبدالات")

    async def cmd_delete_emoji_replacement(self, event, args: str):
        """Handle delete emoji replacement command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: حذف_استبدال <إيموجي>")
                return
            
            normal_emoji = args.strip()
            success = await self.delete_emoji_replacement(normal_emoji)
            
            if success:
                await event.reply(f"تم حذف استبدال الإيموجي: {normal_emoji}")
            else:
                await event.reply("الإيموجي غير موجود في قائمة الاستبدالات")
                
        except Exception as e:
            logger.error(f"Failed to delete emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء حذف استبدال الإيموجي")

    async def cmd_delete_all_emoji_replacements(self, event, args: str):
        """Handle delete all emoji replacements command"""
        try:
            # Check if user provided confirmation
            if args.strip().lower() != "تأكيد":
                await event.reply("""
⚠️ تحذير: هذا الأمر سيحذف جميع الاستبدالات العامة!

📊 الاستبدالات الحالية: {} استبدال

🔴 لتأكيد الحذف، أرسل:
حذف_جميع_الاستبدالات تأكيد

💡 يمكنك استخدام أمر "عرض_الاستبدالات" لرؤية القائمة قبل الحذف
                """.format(len(self.emoji_mappings)).strip())
                return
            
            if not self.emoji_mappings:
                await event.reply("لا توجد استبدالات عامة لحذفها")
                return
            
            deleted_count = await self.delete_all_emoji_replacements()
            
            if deleted_count > 0:
                await event.reply(f"✅ تم حذف جميع الاستبدالات العامة بنجاح!\n🗑️ المحذوف: {deleted_count} استبدال")
            else:
                await event.reply("❌ فشل في حذف الاستبدالات")
                
        except Exception as e:
            logger.error(f"Failed to delete all emoji replacements: {e}")
            await event.reply("حدث خطأ أثناء حذف جميع الاستبدالات")

    async def cmd_clean_duplicate_replacements(self, event, args: str):
        """Clean duplicate emoji replacements and show detailed analysis"""
        try:
            if self.db_pool is None:
                await event.reply("❌ قاعدة البيانات غير متاحة")
                return
            
            async with self.db_pool.acquire() as conn:
                # Get all replacements with their creation times
                rows = await conn.fetch("""
                    SELECT normal_emoji, premium_emoji_id, description, created_at 
                    FROM emoji_replacements 
                    ORDER BY normal_emoji, created_at DESC
                """)
                
                if not rows:
                    await event.reply("❌ لا توجد استبدالات في قاعدة البيانات")
                    return
                
                # Group by emoji and find duplicates
                emoji_groups = {}
                for row in rows:
                    emoji = row['normal_emoji']
                    if emoji not in emoji_groups:
                        emoji_groups[emoji] = []
                    emoji_groups[emoji].append(row)
                
                # Find duplicates and clean them
                cleaned_count = 0
                duplicate_report = []
                
                for emoji, entries in emoji_groups.items():
                    if len(entries) > 1:
                        # Keep the most recent (first in DESC order)
                        keep_entry = entries[0]
                        delete_entries = entries[1:]
                        
                        duplicate_report.append(f"🔄 {emoji}:")
                        duplicate_report.append(f"   ✅ احتفظ بـ: ID {keep_entry['premium_emoji_id']} ({keep_entry['created_at']})")
                        
                        # Delete older duplicates
                        for old_entry in delete_entries:
                            await conn.execute(
                                "DELETE FROM emoji_replacements WHERE normal_emoji = $1 AND premium_emoji_id = $2 AND created_at = $3",
                                old_entry['normal_emoji'], old_entry['premium_emoji_id'], old_entry['created_at']
                            )
                            duplicate_report.append(f"   ❌ حذف: ID {old_entry['premium_emoji_id']} ({old_entry['created_at']})")
                            cleaned_count += 1
                
                # Reload cache after cleaning
                await self.load_emoji_mappings()
                
                # Prepare response
                if cleaned_count > 0:
                    response = f"🧹 تم تنظيف {cleaned_count} استبدال مكرر:\n\n"
                    response += "\n".join(duplicate_report)
                    response += f"\n\n✅ تم إعادة تحميل {len(self.emoji_mappings)} استبدال نشط"
                else:
                    response = "✅ لا توجد استبدالات مكررة. قاعدة البيانات نظيفة!"
                    
                    # Show current mappings summary
                    response += f"\n\n📊 الاستبدالات الحالية: {len(self.emoji_mappings)}"
                    if args.strip().lower() == "تفصيل":
                        response += "\n\n📋 التفاصيل:"
                        for emoji, emoji_id in self.emoji_mappings.items():
                            response += f"\n• {emoji} → ID: {emoji_id}"
                
                await event.reply(response)
                
        except Exception as e:
            logger.error(f"Failed to clean duplicate replacements: {e}")
            await event.reply("❌ حدث خطأ أثناء تنظيف الاستبدالات المكررة")

    async def cmd_add_channel(self, event, args: str):
        """Handle add channel command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: إضافة_قناة <معرف_القناة_أو_اسم_المستخدم>")
                return
            
            channel_identifier = args.strip()
            
            try:
                # Try to get channel entity
                channel_entity = await self.client.get_entity(channel_identifier)
                
                if isinstance(channel_entity, Channel):
                    # Use peer_id for consistent ID comparison
                    channel_id = utils.get_peer_id(channel_entity)
                    channel_username = getattr(channel_entity, 'username', None)
                    channel_title = getattr(channel_entity, 'title', 'Unknown Channel')
                    
                    logger.info(f"Adding channel {channel_title} with peer_id: {channel_id}")
                    success = await self.add_monitored_channel(
                        channel_id, channel_username, channel_title
                    )
                    
                    if success:
                        username_display = channel_username or 'No username'
                        await event.reply(f"تم إضافة القناة للمراقبة: {channel_title} ({username_display})")
                    else:
                        await event.reply("فشل في إضافة القناة")
                else:
                    await event.reply("المعرف المدخل ليس قناة صالحة")
                    
            except Exception as channel_error:
                await event.reply(f"لا يمكن العثور على القناة: {channel_error}")
                
        except Exception as e:
            logger.error(f"Failed to add channel: {e}")
            await event.reply("حدث خطأ أثناء إضافة القناة")

    async def cmd_list_channels(self, event, args: str):
        """Handle list channels command"""
        try:
            if not self.monitored_channels:
                await event.reply("لا توجد قنوات مراقبة محفوظة")
                return
            
            response = "📺 قائمة القنوات المراقبة:\n\n"
            for channel_id, info in self.monitored_channels.items():
                title = info['title'] or 'غير معروف'
                username = info['username'] or 'غير متاح'
                
                # Get replacement status
                is_active = self.channel_replacement_status.get(channel_id, True)
                status_icon = "✅" if is_active else "❌"
                status_text = "مُفعل" if is_active else "مُعطل"
                
                # Count replacements
                replacement_count = len(self.channel_emoji_mappings.get(channel_id, {}))
                
                response += f"• {title} (@{username})\n"
                response += f"  معرف: {channel_id}\n"
                response += f"  الاستبدال: {status_icon} {status_text}\n"
                response += f"  الاستبدالات: {replacement_count}\n\n"
            
            await event.reply(response)
            
        except Exception as e:
            logger.error(f"Failed to list channels: {e}")
            await event.reply("حدث خطأ أثناء عرض قائمة القنوات")

    async def cmd_remove_channel(self, event, args: str):
        """Handle remove channel command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: حذف_قناة <معرف_القناة>")
                return
            
            try:
                channel_id = int(args.strip())
            except ValueError:
                await event.reply("معرف القناة يجب أن يكون رقماً")
                return
            
            if channel_id not in self.monitored_channels:
                await event.reply("القناة غير موجودة في قائمة المراقبة")
                return
            
            # Get channel info and count of emoji replacements before deletion
            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')
            emoji_count = len(self.channel_emoji_mappings.get(channel_id, {}))
            
            success = await self.remove_monitored_channel(channel_id)
            
            if success:
                response = f"✅ تم حذف القناة من المراقبة: {channel_name}"
                if emoji_count > 0:
                    response += f"\n🗑️ تم حذف {emoji_count} استبدال إيموجي خاص بالقناة تلقائياً"
                else:
                    response += "\n📝 لم تكن هناك استبدالات خاصة بالقناة"
                
                await event.reply(response)
            else:
                await event.reply("❌ فشل في حذف القناة")
                
        except Exception as e:
            logger.error(f"Failed to remove channel: {e}")
            await event.reply("حدث خطأ أثناء حذف القناة")

    async def cmd_add_admin(self, event, args: str):
        """Handle add admin command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: اضافة_ادمن <معرف_المستخدم> [اسم_المستخدم]")
                return
            
            parts = args.strip().split(None, 1)
            try:
                user_id = int(parts[0])
            except ValueError:
                await event.reply("معرف المستخدم يجب أن يكون رقماً")
                return
                
            username = parts[1] if len(parts) > 1 else None
            
            if user_id in self.admin_ids:
                await event.reply("هذا المستخدم أدمن بالفعل")
                return
            
            success = await self.add_admin(user_id, username, event.sender_id)
            
            if success:
                await event.reply(f"✅ تم إضافة الأدمن بنجاح: {user_id}")
            else:
                await event.reply("❌ فشل في إضافة الأدمن")
                
        except Exception as e:
            logger.error(f"Failed to add admin: {e}")
            await event.reply("حدث خطأ أثناء إضافة الأدمن")

    async def cmd_list_admins(self, event, args: str):
        """Handle list admins command"""
        try:
            if not self.admin_ids:
                await event.reply("لا توجد أدمن محفوظين")
                return
            
            if self.db_pool is None:
                await event.reply("❌ قاعدة البيانات غير متاحة")
                return
                
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id, username, added_by, added_at 
                    FROM bot_admins 
                    WHERE is_active = TRUE 
                    ORDER BY added_at
                """)
                
                response = "👥 قائمة الأدمن:\n\n"
                for row in rows:
                    username_display = row['username'] or 'غير معروف'
                    added_by_display = row['added_by'] or 'النظام'
                    added_date = row['added_at'].strftime('%Y-%m-%d') if row['added_at'] else 'غير معروف'
                    
                    response += f"• معرف: {row['user_id']}\n"
                    response += f"  الاسم: {username_display}\n"
                    response += f"  أضيف بواسطة: {added_by_display}\n"
                    response += f"  التاريخ: {added_date}\n\n"
                
                await event.reply(response)
                
        except Exception as e:
            logger.error(f"Failed to list admins: {e}")
            await event.reply("حدث خطأ أثناء عرض قائمة الأدمن")

    async def cmd_remove_admin(self, event, args: str):
        """Handle remove admin command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: حذف_ادمن <معرف_المستخدم>")
                return
            
            try:
                user_id = int(args.strip())
            except ValueError:
                await event.reply("معرف المستخدم يجب أن يكون رقماً")
                return
            
            if user_id == 6602517122:
                await event.reply("❌ لا يمكن حذف الأدمن الرئيسي")
                return
                
            if user_id not in self.admin_ids:
                await event.reply("هذا المستخدم ليس أدمن")
                return
            
            success = await self.remove_admin(user_id)
            
            if success:
                await event.reply(f"✅ تم حذف الأدمن بنجاح: {user_id}")
            else:
                await event.reply("❌ فشل في حذف الأدمن")
                
        except Exception as e:
            logger.error(f"Failed to remove admin: {e}")
            await event.reply("حدث خطأ أثناء حذف الأدمن")

    async def cmd_add_channel_emoji_replacement(self, event, args: str):
        """Handle add channel-specific emoji replacement command - supports single or multiple replacements and reply messages"""
        try:
            # Check if this is a reply to a message
            reply_message = None
            if event.message.is_reply:
                reply_message = await event.message.get_reply_message()
            
            if not args.strip() and not reply_message:
                await event.reply("""
📋 الاستخدام: إضافة_استبدال_قناة

🔸 استبدال واحد:
إضافة_استبدال_قناة <معرف_القناة> <إيموجي_عادي> <إيموجي_مميز> [وصف]

🔸 عدة إيموجيات عادية لإيموجي مميز واحد:
إضافة_استبدال_قناة <معرف_القناة> ✅,🟢,☑️ <إيموجي_مميز> [وصف]

🔸 عدة استبدالات (كل سطر منفصل):
إضافة_استبدال_قناة <معرف_القناة>
😀 🔥 وصف أول
❤️,💖,💕 1234567890 وصف ثاني
✅ ✨ وصف ثالث

🔸 الرد على رسالة:
رد على رسالة تحتوي على إيموجيات مع "إضافة_استبدال_قناة <معرف_القناة> [وصف]"

💡 يمكنك استخدام الإيموجي المميز مباشرة أو معرفه الرقمي
💡 فصل الإيموجيات العادية بفاصلة (,) لربطها بنفس الإيموجي المميز
                """.strip())
                return

            # Handle reply message mode
            if reply_message:
                parts = args.strip().split(None, 1)
                if len(parts) < 1:
                    await event.reply("❌ استخدم: إضافة_استبدال_قناة <معرف_القناة> [وصف]")
                    return
                
                try:
                    channel_id = int(parts[0])
                except ValueError:
                    await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                    return
                
                description = parts[1] if len(parts) > 1 else None
                return await self._handle_reply_channel_emoji_replacement(event, reply_message, channel_id, description)

            # Parse the command to get channel ID
            lines = args.strip().split('\n')
            first_line_parts = lines[0].split(None, 3)
            
            if len(first_line_parts) < 1:
                await event.reply("❌ تنسيق غير صحيح. استخدم: إضافة_استبدال_قناة <معرف_القناة> ...")
                return

            try:
                channel_id = int(first_line_parts[0])
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            # Check if channel is monitored
            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة. أضفها أولاً باستخدام أمر إضافة_قناة")
                return

            # Get all custom emojis from the message
            custom_emoji_ids = []
            if event.message.entities:
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        custom_emoji_ids.append(entity.document_id)

            successful_replacements = []
            failed_replacements = []
            custom_emoji_index = 0

            # Check if this is a single-line or multi-line format
            if len(lines) == 1 and len(first_line_parts) >= 3:
                # Single line format: معرف_القناة إيموجي_عادي إيموجي_مميز [وصف]
                normal_emojis_part = first_line_parts[1]
                premium_part = first_line_parts[2]
                description = first_line_parts[3] if len(first_line_parts) > 3 else None

                # Split normal emojis by comma
                normal_emojis = [emoji.strip() for emoji in normal_emojis_part.split(',') if emoji.strip()]

                if not normal_emojis:
                    await event.reply("❌ لا توجد إيموجيات عادية صالحة")
                    return

                # Determine premium emoji ID
                premium_emoji_id = None
                try:
                    premium_emoji_id = int(premium_part)
                except ValueError:
                    if custom_emoji_index < len(custom_emoji_ids):
                        premium_emoji_id = custom_emoji_ids[custom_emoji_index]
                        custom_emoji_index += 1
                    else:
                        await event.reply("❌ لم أجد إيموجي مميز أو معرف صحيح")
                        return

                # Process emojis
                new_emojis = []
                existing_emojis = []

                for normal_emoji in normal_emojis:
                    if (channel_id in self.channel_emoji_mappings and 
                        normal_emoji in self.channel_emoji_mappings[channel_id]):
                        existing_emojis.append(normal_emoji)
                    else:
                        new_emojis.append(normal_emoji)

                # Add replacements
                success_count = 0
                for normal_emoji in new_emojis:
                    success = await self.add_channel_emoji_replacement(channel_id, normal_emoji, premium_emoji_id, description)
                    if success:
                        success_count += 1

                if success_count > 0:
                    emoji_list = ", ".join(new_emojis[:success_count])
                    premium_emoji_markdown = f"[💎](emoji/{premium_emoji_id})"
                    successful_replacements.append(f"{emoji_list} → {premium_emoji_markdown} (ID: {premium_emoji_id})")

                if existing_emojis:
                    failed_replacements.append(f"موجود مسبقاً: {', '.join(existing_emojis)}")

            else:
                # Multi-line format: معرف_القناة followed by multiple lines of replacements
                replacement_lines = []
                
                # Check if first line contains both channel ID and first replacement
                if len(first_line_parts) >= 3:
                    # First line has: channel_id emoji1 emoji2 [description]
                    first_replacement = ' '.join(first_line_parts[1:])
                    replacement_lines.append(first_replacement)
                    # Add remaining lines
                    replacement_lines.extend(lines[1:])
                else:
                    # First line only has channel ID, use remaining lines
                    replacement_lines = lines[1:]

                # Process each replacement line
                for line_num, line in enumerate(replacement_lines, 1):
                    line = line.strip()
                    if not line:
                        continue

                    # Parse line: "normal_emoji(s) premium_emoji/id description"
                    parts = line.split(None, 2)
                    if len(parts) < 2:
                        failed_replacements.append(f"السطر {line_num}: تنسيق غير صحيح")
                        continue

                    normal_emojis_part = parts[0]
                    premium_part = parts[1]
                    description = parts[2] if len(parts) > 2 else None

                    # Split normal emojis by comma
                    normal_emojis = [emoji.strip() for emoji in normal_emojis_part.split(',') if emoji.strip()]

                    if not normal_emojis:
                        failed_replacements.append(f"السطر {line_num}: لا توجد إيموجيات عادية صالحة")
                        continue

                    # Try to determine premium emoji ID
                    premium_emoji_id = None

                    # Method 1: Try to parse as number (ID format)
                    try:
                        premium_emoji_id = int(premium_part)
                    except ValueError:
                        # Method 2: Check if it's a premium emoji in the message
                        if custom_emoji_index < len(custom_emoji_ids):
                            premium_emoji_id = custom_emoji_ids[custom_emoji_index]
                            custom_emoji_index += 1
                        else:
                            failed_replacements.append(f"السطر {line_num}: لم أجد إيموجي مميز أو معرف صحيح")
                            continue

                    # Check which emojis are new and which already exist
                    new_emojis = []
                    existing_emojis = []

                    for normal_emoji in normal_emojis:
                        if (channel_id in self.channel_emoji_mappings and 
                            normal_emoji in self.channel_emoji_mappings[channel_id]):
                            existing_emojis.append(normal_emoji)
                        else:
                            new_emojis.append(normal_emoji)

                    # Add replacements only for new emojis
                    line_success_count = 0
                    line_failed_emojis = []

                    for normal_emoji in new_emojis:
                        success = await self.add_channel_emoji_replacement(channel_id, normal_emoji, premium_emoji_id, description)

                        if success:
                            line_success_count += 1
                        else:
                            line_failed_emojis.append(normal_emoji)

                    # Report results for this line with premium emoji display
                    if line_success_count > 0:
                        emoji_list = ", ".join(new_emojis[:line_success_count])
                        premium_emoji_markdown = f"[💎](emoji/{premium_emoji_id})"
                        successful_replacements.append(f"{emoji_list} → {premium_emoji_markdown} (ID: {premium_emoji_id})")

                    if existing_emojis:
                        existing_emoji_list = ", ".join(existing_emojis)
                        failed_replacements.append(f"السطر {line_num}: موجود مسبقاً: {existing_emoji_list}")

                    if line_failed_emojis:
                        failed_emoji_list = ", ".join(line_failed_emojis)
                        failed_replacements.append(f"السطر {line_num}: فشل في حفظ {failed_emoji_list}")

            # Prepare response with premium emojis
            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')
            
            response_parts = []
            fallback_parts = []

            if successful_replacements:
                response_parts.append(f"✅ تم إضافة الاستبدالات التالية للقناة {channel_name}:")
                fallback_parts.append(f"✅ تم إضافة الاستبدالات التالية للقناة {channel_name}:")
                for replacement in successful_replacements:
                    response_parts.append(f"• {replacement}")
                    # Create fallback version
                    fallback_parts.append(f"• {replacement.replace('[💎]', 'إيموجي مميز')}")

            if failed_replacements:
                if successful_replacements:
                    response_parts.append("")
                    fallback_parts.append("")
                response_parts.append("❌ فشل في إضافة الاستبدالات التالية:")
                fallback_parts.append("❌ فشل في إضافة الاستبدالات التالية:")
                for failure in failed_replacements:
                    response_parts.append(f"• {failure}")
                    fallback_parts.append(f"• {failure}")

            if not successful_replacements and not failed_replacements:
                response_parts.append("❌ لم يتم العثور على استبدالات صالحة")
                fallback_parts.append("❌ لم يتم العثور على استبدالات صالحة")

            # Try to send with premium emojis first
            try:
                response_text = "\n".join(response_parts)
                parsed_text, entities = self.parse_mode.parse(response_text)
                await self.client.send_message(
                    event.chat_id,
                    parsed_text,
                    formatting_entities=entities
                )
            except Exception as parse_error:
                logger.error(f"Failed to parse premium emojis in channel replacement response: {parse_error}")
                # Use fallback format
                fallback_response = "\n".join(fallback_parts)
                await event.reply(fallback_response)

        except Exception as e:
            logger.error(f"Failed to add channel emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء إضافة استبدال الإيموجي للقناة")

    async def _handle_reply_channel_emoji_replacement(self, event, reply_message, channel_id: int, description: str):
        """Handle channel emoji replacement when replying to a message"""
        try:
            # Check if channel is monitored
            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة. أضفها أولاً باستخدام أمر إضافة_قناة")
                return
            
            # Extract emojis from reply message text
            reply_text = reply_message.text or reply_message.message or ""
            normal_emojis = self.extract_emojis_from_text(reply_text)
            
            # Get custom emojis from reply message
            custom_emoji_ids = []
            if reply_message.entities:
                for entity in reply_message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        custom_emoji_ids.append(entity.document_id)
            
            if not normal_emojis and not custom_emoji_ids:
                await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجيات")
                return
            
            if not custom_emoji_ids:
                await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجيات مميزة")
                return
            
            if not normal_emojis:
                await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجيات عادية")
                return
            
            # Use the first premium emoji for all normal emojis
            premium_emoji_id = custom_emoji_ids[0]
            
            successful_count = 0
            failed_emojis = []
            existing_emojis = []
            
            # Process each normal emoji
            for normal_emoji in normal_emojis:
                # Check if already exists for this channel
                if (channel_id in self.channel_emoji_mappings and 
                    normal_emoji in self.channel_emoji_mappings[channel_id]):
                    existing_emojis.append(normal_emoji)
                    continue
                
                success = await self.add_channel_emoji_replacement(
                    channel_id, normal_emoji, premium_emoji_id, 
                    description or f"من الرد على الرسالة"
                )
                if success:
                    successful_count += 1
                else:
                    failed_emojis.append(normal_emoji)
            
            # Prepare response with premium emoji display
            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')
            
            response_parts = []
            fallback_parts = []
            
            if successful_count > 0:
                successful_emojis = [e for e in normal_emojis if e not in existing_emojis and e not in failed_emojis]
                emoji_list = ", ".join(successful_emojis[:successful_count])
                premium_emoji_markdown = f"[💎](emoji/{premium_emoji_id})"
                
                response_parts.append(f"✅ تم إضافة {successful_count} استبدال للقناة {channel_name}:")
                response_parts.append(f"• {emoji_list} → {premium_emoji_markdown} (ID: {premium_emoji_id})")
                
                fallback_parts.append(f"✅ تم إضافة {successful_count} استبدال للقناة {channel_name}:")
                fallback_parts.append(f"• {emoji_list} → إيموجي مميز (ID: {premium_emoji_id})")

            if existing_emojis:
                response_parts.append(f"⚠️ موجود مسبقاً: {', '.join(existing_emojis)}")
                fallback_parts.append(f"⚠️ موجود مسبقاً: {', '.join(existing_emojis)}")

            if failed_emojis:
                response_parts.append(f"❌ فشل في إضافة: {', '.join(failed_emojis)}")
                fallback_parts.append(f"❌ فشل في إضافة: {', '.join(failed_emojis)}")

            if not response_parts:
                response_parts.append("❌ لم يتم إضافة أي استبدالات")
                fallback_parts.append("❌ لم يتم إضافة أي استبدالات")

            # Try to send with premium emojis first
            try:
                response_text = "\n".join(response_parts)
                parsed_text, entities = self.parse_mode.parse(response_text)
                await self.client.send_message(
                    event.chat_id,
                    parsed_text,
                    formatting_entities=entities
                )
            except Exception as parse_error:
                logger.error(f"Failed to parse premium emojis in channel reply replacement response: {parse_error}")
                # Use fallback format
                fallback_response = "\n".join(fallback_parts)
                await event.reply(fallback_response)
            
        except Exception as e:
            logger.error(f"Failed to handle reply channel emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء معالجة الرد للقناة")

    async def cmd_list_channel_emoji_replacements(self, event, args: str):
        """Handle list channel-specific emoji replacements command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: عرض_استبدالات_قناة <معرف_القناة>")
                return

            try:
                channel_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة")
                return

            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')
            
            channel_mappings = self.channel_emoji_mappings.get(channel_id, {})
            
            if not channel_mappings:
                await event.reply(f"لا توجد استبدالات خاصة بالقناة: {channel_name}")
                return

            response_parts = [f"📋 استبدالات القناة {channel_name}:\n"]
            fallback_parts = [f"📋 استبدالات القناة {channel_name}:\n"]

            for normal_emoji, premium_id in channel_mappings.items():
                premium_emoji_markdown = f"[💎](emoji/{premium_id})"
                response_parts.append(f"{normal_emoji} → {premium_emoji_markdown} → (ID: {premium_id})")
                fallback_parts.append(f"{normal_emoji} → إيموجي مميز → (ID: {premium_id})")

            # Try to send with premium emojis first
            try:
                response_text = "\n".join(response_parts)
                parsed_text, entities = self.parse_mode.parse(response_text)
                await self.client.send_message(event.chat_id, parsed_text, formatting_entities=entities)
            except Exception:
                fallback_response = "\n".join(fallback_parts)
                await event.reply(fallback_response)

        except Exception as e:
            logger.error(f"Failed to list channel emoji replacements: {e}")
            await event.reply("حدث خطأ أثناء عرض استبدالات القناة")

    async def cmd_delete_channel_emoji_replacement(self, event, args: str):
        """Handle delete channel-specific emoji replacement command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: حذف_استبدال_قناة <معرف_القناة> <إيموجي>")
                return

            parts = args.strip().split(None, 1)
            if len(parts) != 2:
                await event.reply("❌ تنسيق غير صحيح. استخدم: حذف_استبدال_قناة <معرف_القناة> <إيموجي>")
                return

            try:
                channel_id = int(parts[0])
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            normal_emoji = parts[1]

            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة")
                return

            success = await self.delete_channel_emoji_replacement(channel_id, normal_emoji)
            
            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')

            if success:
                await event.reply(f"✅ تم حذف استبدال الإيموجي {normal_emoji} من القناة {channel_name}")
            else:
                await event.reply(f"❌ الإيموجي غير موجود في استبدالات القناة {channel_name}")

        except Exception as e:
            logger.error(f"Failed to delete channel emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء حذف استبدال الإيموجي من القناة")

    async def cmd_delete_all_channel_emoji_replacements(self, event, args: str):
        """Handle delete all channel-specific emoji replacements command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: حذف_جميع_استبدالات_قناة <معرف_القناة> تأكيد")
                return

            parts = args.strip().split()
            if len(parts) < 1:
                await event.reply("❌ تنسيق غير صحيح. استخدم: حذف_جميع_استبدالات_قناة <معرف_القناة> تأكيد")
                return

            try:
                channel_id = int(parts[0])
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة")
                return

            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')
            current_count = len(self.channel_emoji_mappings.get(channel_id, {}))

            # Check if user provided confirmation
            if len(parts) < 2 or parts[1].lower() != "تأكيد":
                await event.reply(f"""
⚠️ تحذير: هذا الأمر سيحذف جميع الاستبدالات الخاصة بالقناة!

📺 القناة: {channel_name}
📊 الاستبدالات الحالية: {current_count} استبدال

🔴 لتأكيد الحذف، أرسل:
حذف_جميع_استبدالات_قناة {channel_id} تأكيد

💡 يمكنك استخدام "عرض_استبدالات_قناة {channel_id}" لرؤية القائمة قبل الحذف
                """.strip())
                return

            if current_count == 0:
                await event.reply(f"لا توجد استبدالات خاصة بالقناة {channel_name} لحذفها")
                return

            deleted_count = await self.delete_all_channel_emoji_replacements(channel_id)

            if deleted_count > 0:
                await event.reply(f"✅ تم حذف جميع الاستبدالات الخاصة بالقناة {channel_name} بنجاح!\n🗑️ المحذوف: {deleted_count} استبدال")
            else:
                await event.reply(f"❌ فشل في حذف استبدالات القناة {channel_name}")

        except Exception as e:
            logger.error(f"Failed to delete all channel emoji replacements: {e}")
            await event.reply("حدث خطأ أثناء حذف جميع استبدالات القناة")

    async def cmd_copy_channel_emoji_replacements(self, event, args: str):
        """Handle copy emoji replacements from one channel to another"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: نسخ_استبدالات_قناة <معرف_القناة_المصدر> <معرف_القناة_الهدف>")
                return

            parts = args.strip().split()
            if len(parts) != 2:
                await event.reply("❌ تنسيق غير صحيح. استخدم: نسخ_استبدالات_قناة <معرف_القناة_المصدر> <معرف_القناة_الهدف>")
                return

            try:
                source_channel_id = int(parts[0])
                target_channel_id = int(parts[1])
            except ValueError:
                await event.reply("❌ معرفات القنوات يجب أن تكون أرقاماً")
                return

            # Check if both channels are monitored
            if source_channel_id not in self.monitored_channels:
                await event.reply("❌ القناة المصدر غير مراقبة")
                return

            if target_channel_id not in self.monitored_channels:
                await event.reply("❌ القناة الهدف غير مراقبة")
                return

            source_mappings = self.channel_emoji_mappings.get(source_channel_id, {})
            if not source_mappings:
                await event.reply("❌ لا توجد استبدالات في القناة المصدر")
                return

            # Copy replacements
            copied_count = 0
            failed_count = 0

            for normal_emoji, premium_emoji_id in source_mappings.items():
                success = await self.add_channel_emoji_replacement(
                    target_channel_id, normal_emoji, premium_emoji_id, f"نسخ من القناة {source_channel_id}"
                )
                if success:
                    copied_count += 1
                else:
                    failed_count += 1

            source_name = self.monitored_channels[source_channel_id].get('title', 'Unknown')
            target_name = self.monitored_channels[target_channel_id].get('title', 'Unknown')

            response = f"✅ تم نسخ {copied_count} استبدال من {source_name} إلى {target_name}"
            if failed_count > 0:
                response += f"\n❌ فشل في نسخ {failed_count} استبدال"

            await event.reply(response)

        except Exception as e:
            logger.error(f"Failed to copy channel emoji replacements: {e}")
            await event.reply("حدث خطأ أثناء نسخ الاستبدالات")

    async def cmd_activate_channel_replacement(self, event, args: str):
        """Handle activate channel replacement command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: تفعيل_استبدال_قناة <معرف_القناة>")
                return

            try:
                channel_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة. أضفها أولاً باستخدام أمر إضافة_قناة")
                return

            if self.db_pool is None:
                await event.reply("❌ قاعدة البيانات غير متاحة")
                return

            try:
                async with self.db_pool.acquire() as conn:
                    result = await conn.execute(
                        "UPDATE monitored_channels SET replacement_active = TRUE WHERE channel_id = $1",
                        channel_id
                    )
                    
                    if result in ['UPDATE 1', 'UPDATE 0']:
                        # Update cache
                        self.channel_replacement_status[channel_id] = True
                        
                        channel_name = self.monitored_channels[channel_id].get('title', 'Unknown Channel')
                        await event.reply(f"✅ تم تفعيل الاستبدال في القناة: {channel_name}")
                        logger.info(f"Activated replacement for channel {channel_id}")
                        return True
                    else:
                        await event.reply("❌ فشل في تفعيل الاستبدال")
                        return False

            except Exception as e:
                logger.error(f"Database error in activate_channel_replacement: {e}")
                await event.reply("❌ حدث خطأ في قاعدة البيانات")
                return False

        except Exception as e:
            logger.error(f"Failed to activate channel replacement: {e}")
            await event.reply("حدث خطأ أثناء تفعيل الاستبدال")

    async def cmd_deactivate_channel_replacement(self, event, args: str):
        """Handle deactivate channel replacement command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: تعطيل_استبدال_قناة <معرف_القناة>")
                return

            try:
                channel_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة")
                return

            if self.db_pool is None:
                await event.reply("❌ قاعدة البيانات غير متاحة")
                return

            try:
                async with self.db_pool.acquire() as conn:
                    result = await conn.execute(
                        "UPDATE monitored_channels SET replacement_active = FALSE WHERE channel_id = $1",
                        channel_id
                    )
                    
                    if result in ['UPDATE 1', 'UPDATE 0']:
                        # Update cache
                        self.channel_replacement_status[channel_id] = False
                        
                        channel_name = self.monitored_channels[channel_id].get('title', 'Unknown Channel')
                        await event.reply(f"✅ تم تعطيل الاستبدال في القناة: {channel_name}")
                        logger.info(f"Deactivated replacement for channel {channel_id}")
                        return True
                    else:
                        await event.reply("❌ فشل في تعطيل الاستبدال")
                        return False

            except Exception as e:
                logger.error(f"Database error in deactivate_channel_replacement: {e}")
                await event.reply("❌ حدث خطأ في قاعدة البيانات")
                return False

        except Exception as e:
            logger.error(f"Failed to deactivate channel replacement: {e}")
            await event.reply("حدث خطأ أثناء تعطيل الاستبدال")

    async def cmd_check_channel_replacement_status(self, event, args: str):
        """Handle check channel replacement status command"""
        try:
            if not args.strip():
                # Show status for all monitored channels
                if not self.monitored_channels:
                    await event.reply("لا توجد قنوات مراقبة")
                    return

                response = "📊 حالة الاستبدال للقنوات المراقبة:\n\n"
                
                for channel_id, channel_info in self.monitored_channels.items():
                    channel_name = channel_info.get('title', 'Unknown Channel')
                    is_active = self.channel_replacement_status.get(channel_id, True)
                    status_icon = "✅" if is_active else "❌"
                    status_text = "مُفعل" if is_active else "مُعطل"
                    
                    response += f"• {channel_name}\n"
                    response += f"  المعرف: {channel_id}\n"
                    response += f"  الحالة: {status_icon} {status_text}\n\n"

                await event.reply(response)
                return

            try:
                channel_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة")
                return

            channel_name = self.monitored_channels[channel_id].get('title', 'Unknown Channel')
            is_active = self.channel_replacement_status.get(channel_id, True)
            status_icon = "✅" if is_active else "❌"
            status_text = "مُفعل" if is_active else "مُعطل"
            
            # Count replacements for this channel
            replacement_count = len(self.channel_emoji_mappings.get(channel_id, {}))
            
            response = f"📊 حالة القناة: {channel_name}\n\n"
            response += f"🆔 المعرف: {channel_id}\n"
            response += f"🔄 حالة الاستبدال: {status_icon} {status_text}\n"
            response += f"📝 عدد الاستبدالات: {replacement_count}\n\n"
            
            if is_active:
                response += "💡 الاستبدال مُفعل - سيتم استبدال الإيموجيات تلقائياً"
            else:
                response += "💡 الاستبدال مُعطل - لن يتم استبدال الإيموجيات\n"
                response += "استخدم 'تفعيل_استبدال_قناة' لتفعيل الاستبدال"

            await event.reply(response)

        except Exception as e:
            logger.error(f"Failed to check channel replacement status: {e}")
            await event.reply("حدث خطأ أثناء فحص حالة الاستبدال")

    async def cmd_add_forwarding_task(self, event, args: str):
        """Handle add forwarding task command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: إضافة_مهمة_توجيه <معرف_القناة_المصدر> <معرف_القناة_الهدف> [التأخير_بالثواني] [وصف]")
                return

            parts = args.strip().split(None, 3)
            if len(parts) < 2:
                await event.reply("❌ تنسيق غير صحيح. استخدم: إضافة_مهمة_توجيه <معرف_القناة_المصدر> <معرف_القناة_الهدف> [التأخير_بالثواني] [وصف]")
                return

            try:
                source_channel_id = int(parts[0])
                target_channel_id = int(parts[1])
            except ValueError:
                await event.reply("❌ معرفات القنوات يجب أن تكون أرقاماً")
                return

            # Parse delay and description
            delay_seconds = 0
            description = None
            
            if len(parts) >= 3:
                try:
                    # Try to parse third parameter as delay
                    delay_seconds = int(parts[2])
                    if delay_seconds < 0:
                        await event.reply("❌ التأخير يجب أن يكون رقماً موجباً أو صفر")
                        return
                    if delay_seconds > 3600:  # Max 1 hour
                        await event.reply("❌ التأخير الأقصى هو 3600 ثانية (ساعة واحدة)")
                        return
                    
                    # Description is the fourth parameter
                    description = parts[3] if len(parts) > 3 else None
                    
                except ValueError:
                    # Third parameter is not a number, treat it as description
                    description = ' '.join(parts[2:])
                    delay_seconds = 0

            # Check if both channels are monitored
            if source_channel_id not in self.monitored_channels:
                await event.reply("❌ القناة المصدر غير مراقبة. أضفها أولاً باستخدام أمر إضافة_قناة")
                return

            if target_channel_id not in self.monitored_channels:
                await event.reply("❌ القناة الهدف غير مراقبة. أضفها أولاً باستخدام أمر إضافة_قناة")
                return

            if source_channel_id == target_channel_id:
                await event.reply("❌ لا يمكن توجيه الرسائل من القناة إلى نفسها")
                return

            success = await self.add_forwarding_task(source_channel_id, target_channel_id, description, delay_seconds)

            source_name = self.monitored_channels[source_channel_id].get('title', 'Unknown')
            target_name = self.monitored_channels[target_channel_id].get('title', 'Unknown')

            if success:
                response = f"✅ تم إضافة مهمة النسخ بنجاح!\n📤 من: {source_name}\n📥 إلى: {target_name}"
                if delay_seconds > 0:
                    response += f"\n⏱️ التأخير: {delay_seconds} ثانية"
                else:
                    response += f"\n⏱️ التأخير: فوري (بدون تأخير)"
                await event.reply(response)
            else:
                await event.reply("❌ فشل في إضافة مهمة النسخ")

        except Exception as e:
            logger.error(f"Failed to add forwarding task: {e}")
            await event.reply("حدث خطأ أثناء إضافة مهمة التوجيه")

    async def cmd_list_forwarding_tasks(self, event, args: str):
        """Handle list forwarding tasks command"""
        try:
            if not self.forwarding_tasks:
                await event.reply("لا توجد مهام توجيه محفوظة")
                return

            response = "📋 قائمة مهام النسخ:\n\n"
            
            for task_id, task_info in self.forwarding_tasks.items():
                source_id = task_info['source']
                target_id = task_info['target']
                is_active = task_info['active']
                description = task_info['description']
                delay = task_info.get('delay', 0)

                source_name = self.monitored_channels.get(source_id, {}).get('title', f'القناة {source_id}')
                target_name = self.monitored_channels.get(target_id, {}).get('title', f'القناة {target_id}')

                status_icon = "✅" if is_active else "❌"
                status_text = "مُفعلة" if is_active else "مُعطلة"

                response += f"🆔 المهمة: {task_id}\n"
                response += f"📤 من: {source_name} ({source_id})\n"
                response += f"📥 إلى: {target_name} ({target_id})\n"
                response += f"🔄 الحالة: {status_icon} {status_text}\n"
                response += f"⏱️ التأخير: {delay} ثانية\n"
                
                if description:
                    response += f"📝 الوصف: {description}\n"
                
                response += "\n"

            await event.reply(response)

        except Exception as e:
            logger.error(f"Failed to list forwarding tasks: {e}")
            await event.reply("حدث خطأ أثناء عرض مهام التوجيه")

    async def cmd_delete_forwarding_task(self, event, args: str):
        """Handle delete forwarding task command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: حذف_مهمة_توجيه <معرف_المهمة>")
                return

            try:
                task_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف المهمة يجب أن يكون رقماً")
                return

            if task_id not in self.forwarding_tasks:
                await event.reply("❌ المهمة غير موجودة")
                return

            task_info = self.forwarding_tasks[task_id]
            source_name = self.monitored_channels.get(task_info['source'], {}).get('title', 'Unknown')
            target_name = self.monitored_channels.get(task_info['target'], {}).get('title', 'Unknown')

            success = await self.delete_forwarding_task(task_id)

            if success:
                await event.reply(f"✅ تم حذف مهمة النسخ بنجاح!\n📤 من: {source_name}\n📥 إلى: {target_name}")
            else:
                await event.reply("❌ فشل في حذف مهمة النسخ")

        except Exception as e:
            logger.error(f"Failed to delete forwarding task: {e}")
            await event.reply("حدث خطأ أثناء حذف مهمة التوجيه")

    async def cmd_activate_forwarding_task(self, event, args: str):
        """Handle activate forwarding task command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: تفعيل_مهمة_توجيه <معرف_المهمة>")
                return

            try:
                task_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف المهمة يجب أن يكون رقماً")
                return

            # Check if task exists (including inactive ones)
            if self.db_pool is None:
                await event.reply("❌ قاعدة البيانات غير متاحة")
                return

            async with self.db_pool.acquire() as conn:
                task_row = await conn.fetchrow("SELECT * FROM forwarding_tasks WHERE id = $1", task_id)
                
                if not task_row:
                    await event.reply("❌ المهمة غير موجودة")
                    return

            success = await self.activate_forwarding_task(task_id)

            if success:
                # Reload cache to get updated task info
                await self.load_forwarding_tasks()
                
                if task_id in self.forwarding_tasks:
                    task_info = self.forwarding_tasks[task_id]
                    source_name = self.monitored_channels.get(task_info['source'], {}).get('title', 'Unknown')
                    target_name = self.monitored_channels.get(task_info['target'], {}).get('title', 'Unknown')
                    
                    await event.reply(f"✅ تم تفعيل مهمة النسخ بنجاح!\n📤 من: {source_name}\n📥 إلى: {target_name}")
                else:
                    await event.reply("✅ تم تفعيل مهمة النسخ بنجاح!")
            else:
                await event.reply("❌ فشل في تفعيل مهمة النسخ")

        except Exception as e:
            logger.error(f"Failed to activate forwarding task: {e}")
            await event.reply("حدث خطأ أثناء تفعيل مهمة التوجيه")

    async def cmd_deactivate_forwarding_task(self, event, args: str):
        """Handle deactivate forwarding task command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: تعطيل_مهمة_توجيه <معرف_المهمة>")
                return

            try:
                task_id = int(args.strip())
            except ValueError:
                await event.reply("❌ معرف المهمة يجب أن يكون رقماً")
                return

            if task_id not in self.forwarding_tasks:
                await event.reply("❌ المهمة غير موجودة أو معطلة بالفعل")
                return

            task_info = self.forwarding_tasks[task_id]
            source_name = self.monitored_channels.get(task_info['source'], {}).get('title', 'Unknown')
            target_name = self.monitored_channels.get(task_info['target'], {}).get('title', 'Unknown')

            success = await self.deactivate_forwarding_task(task_id)

            if success:
                await event.reply(f"✅ تم تعطيل مهمة النسخ بنجاح!\n📤 من: {source_name}\n📥 إلى: {target_name}")
            else:
                await event.reply("❌ فشل في تعطيل مهمة النسخ")

        except Exception as e:
            logger.error(f"Failed to deactivate forwarding task: {e}")
            await event.reply("حدث خطأ أثناء تعطيل مهمة التوجيه")

    async def cmd_update_forwarding_delay(self, event, args: str):
        """Handle update forwarding task delay command"""
        try:
            if not args.strip():
                await event.reply("الاستخدام: تعديل_تأخير_مهمة <معرف_المهمة> <التأخير_بالثواني>")
                return

            parts = args.strip().split()
            if len(parts) != 2:
                await event.reply("❌ تنسيق غير صحيح. استخدم: تعديل_تأخير_مهمة <معرف_المهمة> <التأخير_بالثواني>")
                return

            try:
                task_id = int(parts[0])
                delay_seconds = int(parts[1])
            except ValueError:
                await event.reply("❌ معرف المهمة والتأخير يجب أن يكونا أرقاماً")
                return

            if delay_seconds < 0:
                await event.reply("❌ التأخير يجب أن يكون رقماً موجباً أو صفر")
                return

            if delay_seconds > 3600:  # Max 1 hour
                await event.reply("❌ التأخير الأقصى هو 3600 ثانية (ساعة واحدة)")
                return

            # Check if task exists (including inactive ones)
            if self.db_pool is None:
                await event.reply("❌ قاعدة البيانات غير متاحة")
                return

            async with self.db_pool.acquire() as conn:
                # Update delay in database
                result = await conn.execute(
                    "UPDATE forwarding_tasks SET delay_seconds = $1 WHERE id = $2",
                    delay_seconds, task_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache if task is active
                    if task_id in self.forwarding_tasks:
                        self.forwarding_tasks[task_id]['delay'] = delay_seconds
                        
                        task_info = self.forwarding_tasks[task_id]
                        source_name = self.monitored_channels.get(task_info['source'], {}).get('title', 'Unknown')
                        target_name = self.monitored_channels.get(task_info['target'], {}).get('title', 'Unknown')
                    else:
                        # Get task info from database
                        task_row = await conn.fetchrow("SELECT source_channel_id, target_channel_id FROM forwarding_tasks WHERE id = $1", task_id)
                        if task_row:
                            source_name = self.monitored_channels.get(task_row['source_channel_id'], {}).get('title', 'Unknown')
                            target_name = self.monitored_channels.get(task_row['target_channel_id'], {}).get('title', 'Unknown')
                        else:
                            source_name = "Unknown"
                            target_name = "Unknown"
                    
                    if delay_seconds > 0:
                        await event.reply(f"✅ تم تحديث تأخير المهمة {task_id} بنجاح!\n📤 من: {source_name}\n📥 إلى: {target_name}\n⏱️ التأخير الجديد: {delay_seconds} ثانية")
                    else:
                        await event.reply(f"✅ تم تحديث تأخير المهمة {task_id} بنجاح!\n📤 من: {source_name}\n📥 إلى: {target_name}\n⏱️ التأخير: فوري (بدون تأخير)")
                    
                    logger.info(f"Updated forwarding task {task_id} delay to {delay_seconds} seconds")
                    return True
                else:
                    await event.reply("❌ المهمة غير موجودة")
                    return False

        except Exception as e:
            logger.error(f"Failed to update forwarding task delay: {e}")
            await event.reply("حدث خطأ أثناء تحديث تأخير مهمة التوجيه")

    async def cmd_help_command(self, event, args: str):
        """Handle help command"""
        help_text = """
🤖 أوامر بوت استبدال الإيموجي:

📝 إدارة الاستبدالات العامة:
• إضافة_استبدال <إيموجي_عادي> <إيموجي_مميز> [وصف]
• عرض_الاستبدالات - عرض جميع الاستبدالات العامة
• حذف_استبدال <إيموجي> - حذف استبدال عام
• حذف_جميع_الاستبدالات تأكيد - حذف جميع الاستبدالات العامة
• تنظيف_الاستبدالات [تفصيل] - حذف الاستبدالات المكررة

🎯 إدارة الاستبدالات الخاصة بالقنوات:
• إضافة_استبدال_قناة <معرف_القناة> <إيموجي_عادي> <إيموجي_مميز> [وصف]
• عرض_استبدالات_قناة <معرف_القناة> - عرض استبدالات قناة معينة
• حذف_استبدال_قناة <معرف_القناة> <إيموجي> - حذف استبدال من قناة
• حذف_جميع_استبدالات_قناة <معرف_القناة> تأكيد - حذف جميع استبدالات القناة
• نسخ_استبدالات_قناة <معرف_المصدر> <معرف_الهدف> - نسخ الاستبدالات
• تفعيل_استبدال_قناة <معرف_القناة> - تفعيل الاستبدال في القناة
• تعطيل_استبدال_قناة <معرف_القناة> - تعطيل الاستبدال في القناة
• حالة_استبدال_قناة [معرف_القناة] - فحص حالة الاستبدال

🔄 إدارة مهام النسخ:
• إضافة_مهمة_توجيه <معرف_المصدر> <معرف_الهدف> [التأخير_بالثواني] [وصف] - إضافة مهمة نسخ جديدة
• عرض_مهام_التوجيه - عرض جميع مهام النسخ
• حذف_مهمة_توجيه <معرف_المهمة> - حذف مهمة نسخ
• تفعيل_مهمة_توجيه <معرف_المهمة> - تفعيل مهمة نسخ
• تعطيل_مهمة_توجيه <معرف_المهمة> - تعطيل مهمة نسخ
• تعديل_تأخير_مهمة <معرف_المهمة> <التأخير_بالثواني> - تعديل تأخير مهمة موجودة

📺 إدارة القنوات:
• إضافة_قناة <معرف_أو_اسم_مستخدم> - إضافة قناة للمراقبة
• عرض_القنوات - عرض القنوات المراقبة
• حذف_قناة <معرف_القناة> - حذف قناة من المراقبة

👥 إدارة الأدمن:
• اضافة_ادمن <معرف_المستخدم> [اسم_المستخدم] - إضافة أدمن جديد
• عرض_الادمن - عرض قائمة الأدمن
• حذف_ادمن <معرف_المستخدم> - حذف أدمن

🔍 أدوات مساعدة:
• معرف_ايموجي <إيموجي_مميز> - عرض معرف الإيموجي المميز
• أو رد على رسالة تحتوي على إيموجي مميز بكلمة "معرف_ايموجي"

❓ مساعدة - عرض هذه الرسالة

ملاحظة: 
- جميع الأوامر تعمل في الرسائل الخاصة فقط
- الاستبدالات الخاصة بالقناة لها أولوية أعلى من الاستبدالات العامة
- أوامر الحذف الشامل تتطلب كلمة "تأكيد" لتجنب الحذف الخطأ
- مهام التوجيه تعمل فقط بين القنوات المراقبة
        """
        await event.reply(help_text.strip())

    async def cmd_get_emoji_id(self, event, args: str):
        """Handle get emoji ID command"""
        try:
            # Check if this is a reply to a message with custom emojis
            if event.message.is_reply:
                reply_msg = await event.message.get_reply_message()
                if reply_msg and reply_msg.entities:
                    custom_emojis = []
                    for entity in reply_msg.entities:
                        if isinstance(entity, MessageEntityCustomEmoji):
                            custom_emojis.append(entity.document_id)
                    
                    if custom_emojis:
                        # Build response with actual premium emojis
                        response_parts = ["🔍 معرفات الإيموجي المميز في الرسالة:\n"]
                        
                        for idx, emoji_id in enumerate(custom_emojis, 1):
                            # Create markdown for premium emoji with a placeholder emoji
                            premium_emoji_markdown = f"[💎](emoji/{emoji_id})"
                            response_parts.append(f"• {premium_emoji_markdown} `{emoji_id}`")
                        
                        response_parts.append("\nيمكنك نسخ المعرف واستخدامه مع أمر إضافة_استبدال")
                        response_text = "\n".join(response_parts)
                        
                        # Parse and send with premium emojis
                        try:
                            parsed_text, entities = self.parse_mode.parse(response_text)
                            await self.client.send_message(
                                event.chat_id,
                                parsed_text,
                                formatting_entities=entities
                            )
                        except Exception as parse_error:
                            logger.error(f"Failed to parse premium emojis in get_emoji_id: {parse_error}")
                            # Fallback to simple text
                            simple_response = "🔍 معرفات الإيموجي المميز في الرسالة:\n\n"
                            for idx, emoji_id in enumerate(custom_emojis, 1):
                                simple_response += f"• إيموجي مميز: `{emoji_id}`\n"
                            simple_response += "\nيمكنك نسخ المعرف واستخدامه مع أمر إضافة_استبدال"
                            await event.reply(simple_response)
                        return
                    else:
                        await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجي مميز")
                        return
                else:
                    await event.reply("❌ الرسالة المردود عليها لا تحتوي على إيموجي")
                    return
            
            # Check for custom emojis in the current message
            if event.message.entities:
                custom_emojis = []
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        custom_emojis.append(entity.document_id)
                
                if custom_emojis:
                    # Build response with actual premium emojis
                    response_parts = ["🔍 معرفات الإيموجي المميز في رسالتك:\n"]
                    
                    for idx, emoji_id in enumerate(custom_emojis, 1):
                        # Create markdown for premium emoji with a placeholder emoji
                        premium_emoji_markdown = f"[💎](emoji/{emoji_id})"
                        response_parts.append(f"• {premium_emoji_markdown} `{emoji_id}`")
                    
                    response_parts.append("\nيمكنك نسخ المعرف واستخدامه مع أمر إضافة_استبدال")
                    response_text = "\n".join(response_parts)
                    
                    # Parse and send with premium emojis
                    try:
                        parsed_text, entities = self.parse_mode.parse(response_text)
                        await self.client.send_message(
                            event.chat_id,
                            parsed_text,
                            formatting_entities=entities
                        )
                    except Exception as parse_error:
                        logger.error(f"Failed to parse premium emojis in get_emoji_id: {parse_error}")
                        # Fallback to simple text
                        simple_response = "🔍 معرفات الإيموجي المميز في رسالتك:\n\n"
                        for idx, emoji_id in enumerate(custom_emojis, 1):
                            simple_response += f"• إيموجي مميز: `{emoji_id}`\n"
                        simple_response += "\nيمكنك نسخ المعرف واستخدامه مع أمر إضافة_استبدال"
                        await event.reply(simple_response)
                    return
            
            # No custom emojis found
            await event.reply("""
❌ لم أجد أي إيموجي مميز.

📋 طرق الاستخدام:
1. أرسل "معرف_ايموجي" مع إيموجي مميز في نفس الرسالة
2. رد على رسالة تحتوي على إيموجي مميز بكلمة "معرف_ايموجي"

💡 مثال: معرف_ايموجي 🔥
(استخدم إيموجي مميز بدلاً من العادي)
            """.strip())
                
        except Exception as e:
            logger.error(f"Failed to get emoji ID: {e}")
            await event.reply("حدث خطأ أثناء البحث عن معرف الإيموجي")

    def setup_event_handlers(self):
        """Setup Telegram event handlers"""
        
        # Handler for new messages in monitored channels
        @self.client.on(events.NewMessage())
        async def new_message_handler(event):
            try:
                # Handle private messages with commands
                # Include saved messages (messages to self) where sender_id equals chat_id
                if event.is_private and (not event.message.out or event.sender_id == event.chat_id):
                    logger.info("Processing private message or saved message")
                    await self.handle_private_message(event)
                    return
                
                # Check if message is from a monitored channel  
                event_peer_id = utils.get_peer_id(event.chat)
                if event_peer_id in self.monitored_channels:
                    message_text = event.message.text or event.message.message or ""
                    logger.info(f"Processing message in monitored channel {event_peer_id}: {message_text}")
                    
                    # Handle emoji replacement first
                    await self.replace_emojis_in_message(event)
                    
                    # Then handle forwarding (after emoji replacement)
                    # Get the updated message after emoji replacement
                    updated_message = event.message
                    try:
                        # Try to get the most recent version of the message
                        updated_message = await self.client.get_messages(event.chat, ids=event.message.id)
                        if isinstance(updated_message, list) and len(updated_message) > 0:
                            updated_message = updated_message[0]
                    except Exception as e:
                        logger.warning(f"Could not fetch updated message, using original: {e}")
                        updated_message = event.message
                    
                    await self.forward_message_to_targets(event_peer_id, updated_message)
                    logger.info(f"Finished processing message in channel {event_peer_id}")
                    
            except Exception as e:
                logger.error(f"Error in new message handler: {e}")

        # Handler for message edits in monitored channels
        @self.client.on(events.MessageEdited())
        async def edited_message_handler(event):
            try:
                # Check if edited message is from a monitored channel
                event_peer_id = utils.get_peer_id(event.chat)
                if event_peer_id in self.monitored_channels:
                    logger.info(f"Message edited in monitored channel {event_peer_id}")
                    await self.replace_emojis_in_message(event)
                    
            except Exception as e:
                logger.error(f"Error in edited message handler: {e}")

        logger.info("Event handlers set up successfully")

    async def start(self):
        """Start the bot"""
        try:
            logger.info("Starting Telegram Emoji Bot...")
            
            # Initialize database
            await self.init_database()
            
            # Start Telegram client
            try:
                start_method = getattr(self.client, 'start', None)
                if start_method and callable(start_method):
                    await start_method()
            except Exception as e:
                logger.error(f"Failed to start Telegram client: {e}")
                raise
            
            # Verify session is authorized
            is_authorized = await self.client.is_user_authorized()
            if not is_authorized:
                logger.error("Session string is invalid or expired - bot is not authorized")
                raise ValueError("Invalid session string - bot is not authorized")
            
            logger.info("Telegram client started and authorized successfully")
            
            # Get bot info
            me = await self.client.get_me()
            first_name = getattr(me, 'first_name', 'Unknown User')
            username = getattr(me, 'username', None) or 'Unknown'
            logger.info(f"Bot started as: {first_name} (@{username})")
            
            # Setup event handlers
            self.setup_event_handlers()
            
            # Start command queue processor
            asyncio.create_task(self.start_command_queue_processor())
            
            logger.info("Bot is now running and monitoring channels...")
            logger.info("Command queue processor started for Control Bot integration")
            
            # Keep the bot running
            try:
                run_method = getattr(self.client, 'run_until_disconnected', None)
                if run_method and callable(run_method):
                    await run_method()
            except Exception as e:
                logger.error(f"Failed to run client: {e}")
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            raise
        finally:
            # Clean up
            if self.db_pool:
                await self.db_pool.close()
            logger.info("Bot stopped")

    async def stop(self):
        """Stop the bot"""
        logger.info("Stopping bot...")
        
        try:
            is_connected_method = getattr(self.client, 'is_connected', None)
            disconnect_method = getattr(self.client, 'disconnect', None)
            if is_connected_method and callable(is_connected_method) and is_connected_method():
                if disconnect_method and callable(disconnect_method):
                    await disconnect_method()
        except Exception as e:
            logger.error(f"Failed to disconnect client: {e}")
        
        if self.db_pool:
            await self.db_pool.close()

# Main execution
async def main():
    """Main function to run the bot"""
    bot = TelegramEmojiBot()
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise
    finally:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())