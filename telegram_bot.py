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
        
        # Cache for admin list
        self.admin_ids: set = {6602517122}  # Default admin
        
        # Arabic command mappings
        self.arabic_commands = {
            'إضافة_استبدال': 'add_emoji_replacement',
            'عرض_الاستبدالات': 'list_emoji_replacements', 
            'حذف_استبدال': 'delete_emoji_replacement',
            'حذف_جميع_الاستبدالات': 'delete_all_emoji_replacements',
            'تنظيف_الاستبدالات': 'clean_duplicate_replacements',
            'إضافة_استبدال_قناة': 'add_channel_emoji_replacement',
            'عرض_استبدالات_قناة': 'list_channel_emoji_replacements',
            'حذف_استبدال_قناة': 'delete_channel_emoji_replacement',
            'حذف_جميع_استبدالات_قناة': 'delete_all_channel_emoji_replacements',
            'نسخ_استبدالات_قناة': 'copy_channel_emoji_replacements',
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
                rows = await conn.fetch(
                    "SELECT channel_id, channel_username, channel_title FROM monitored_channels WHERE is_active = TRUE"
                )
                self.monitored_channels = {
                    row['channel_id']: {
                        'username': row['channel_username'],
                        'title': row['channel_title']
                    }
                    for row in rows
                }
                logger.info(f"Loaded {len(self.monitored_channels)} monitored channels from database")
        except Exception as e:
            logger.error(f"Failed to load monitored channels: {e}")

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
                    """INSERT INTO monitored_channels (channel_id, channel_username, channel_title, is_active) 
                       VALUES ($1, $2, $3, TRUE) 
                       ON CONFLICT (channel_id) 
                       DO UPDATE SET channel_username = $2, channel_title = $3, is_active = TRUE""",
                    channel_id, channel_username, channel_title
                )
                
                # Update cache
                self.monitored_channels[channel_id] = {
                    'username': channel_username or '',
                    'title': channel_title or ''
                }
                logger.info(f"Added/updated monitored channel: {channel_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add monitored channel: {e}")
            return False

    async def remove_monitored_channel(self, channel_id: int) -> bool:
        """Remove channel from monitoring list"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return False
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE monitored_channels SET is_active = FALSE WHERE channel_id = $1",
                    channel_id
                )
                
                if result == 'UPDATE 1':
                    # Update cache
                    self.monitored_channels.pop(channel_id, None)
                    logger.info(f"Removed monitored channel: {channel_id}")
                    return True
                else:
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to remove monitored channel: {e}")
            return False

    def extract_emojis_from_text(self, text: str) -> List[str]:
        """Extract all unique emojis from text using regex - handles consecutive emojis"""
        # Unicode emoji regex pattern - removed '+' to match single emoji at a time
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\U00002700-\U000027BF"  # dingbats
            "\U0001F900-\U0001F9FF"  # supplemental symbols
            "\U00002600-\U000026FF"  # miscellaneous symbols
            "\U0001F170-\U0001F251"
            "]", flags=re.UNICODE  # Removed '+' to match individual emojis
        )
        
        # Get all individual emojis found in text
        found_emojis = emoji_pattern.findall(text)
        
        # Return unique emojis while preserving order
        unique_emojis = []
        seen = set()
        for emoji in found_emojis:
            if emoji not in seen:
                unique_emojis.append(emoji)
                seen.add(emoji)
        
        return unique_emojis

    async def replace_emojis_in_message(self, event):
        """Replace normal emojis with premium emojis in a message"""
        try:
            message = event.message
            original_text = message.text or message.message
            
            if not original_text:
                return
            
            # Extract emojis from the message
            found_emojis = self.extract_emojis_from_text(original_text)
            
            if not found_emojis:
                return
            
            # Check if any of the found emojis have premium replacements
            replacements_made = []
            modified_text = original_text
            
            # Process text character by character to handle multiple same emojis correctly
            import re
            
            # Create a list to track which emojis need replacement
            # Priority: Channel-specific replacements first, then global replacements
            emojis_to_replace = {}
            event_peer_id = utils.get_peer_id(event.chat)
            
            for emoji in found_emojis:
                # Check channel-specific replacements first
                if (event_peer_id in self.channel_emoji_mappings and 
                    emoji in self.channel_emoji_mappings[event_peer_id]):
                    emojis_to_replace[emoji] = self.channel_emoji_mappings[event_peer_id][emoji]
                    replacements_made.append(emoji)
                # Then check global replacements
                elif emoji in self.emoji_mappings:
                    emojis_to_replace[emoji] = self.emoji_mappings[emoji]
                    replacements_made.append(emoji)
            
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
            
            # Find matching Arabic command
            command_handler = None
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
                logger.info("No matching command found, sending help message")
                await event.reply("أمر غير معروف. استخدم 'مساعدة' لعرض الأوامر المتاحة.")
                
        except Exception as e:
            logger.error(f"Failed to handle private message: {e}")
            await event.reply("حدث خطأ أثناء معالجة الأمر.")

    async def cmd_add_emoji_replacement(self, event, args: str):
        """Handle add emoji replacement command - supports single or multiple replacements"""
        try:
            # Check if args contain multiple lines (multiple replacements)
            lines = args.strip().split('\n')
            
            if not args.strip():
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

💡 يمكنك استخدام الإيموجي المميز مباشرة أو معرفه الرقمي
💡 فصل الإيموجيات العادية بفاصلة (,) لربطها بنفس الإيموجي المميز
                """.strip())
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
                
                # Report results for this line
                if line_success_count > 0:
                    emoji_list = ", ".join(new_emojis[:line_success_count])
                    successful_replacements.append(f"{emoji_list} → إيموجي مميز (ID: {premium_emoji_id})")
                
                if existing_emojis:
                    existing_emoji_list = ", ".join(existing_emojis)
                    failed_replacements.append(f"السطر {line_num}: موجود مسبقاً: {existing_emoji_list}")
                
                if line_failed_emojis:
                    failed_emoji_list = ", ".join(line_failed_emojis)
                    failed_replacements.append(f"السطر {line_num}: فشل في حفظ {failed_emoji_list}")
            
            # Prepare response
            response_parts = []
            
            if successful_replacements:
                response_parts.append("✅ تم إضافة الاستبدالات التالية بنجاح:")
                for replacement in successful_replacements:
                    response_parts.append(f"• {replacement}")
            
            if failed_replacements:
                if successful_replacements:
                    response_parts.append("")
                response_parts.append("❌ فشل في إضافة الاستبدالات التالية:")
                for failure in failed_replacements:
                    response_parts.append(f"• {failure}")
            
            if not successful_replacements and not failed_replacements:
                response_parts.append("❌ لم يتم العثور على استبدالات صالحة")
            
            await event.reply("\n".join(response_parts))
                
        except Exception as e:
            logger.error(f"Failed to add emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء إضافة استبدال الإيموجي")

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
                response += f"• {title} (@{username})\n  معرف: {channel_id}\n\n"
            
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
            
            success = await self.remove_monitored_channel(channel_id)
            
            if success:
                await event.reply(f"تم حذف القناة من المراقبة: {channel_id}")
            else:
                await event.reply("القناة غير موجودة في قائمة المراقبة")
                
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
        """Handle add channel-specific emoji replacement command"""
        try:
            if not args.strip():
                await event.reply("""
📋 الاستخدام: إضافة_استبدال_قناة

🔸 استبدال واحد:
إضافة_استبدال_قناة <معرف_القناة> <إيموجي_عادي> <إيموجي_مميز> [وصف]

🔸 عدة إيموجيات عادية لإيموجي مميز واحد:
إضافة_استبدال_قناة <معرف_القناة> ✅,🟢,☑️ <إيموجي_مميز> [وصف]

💡 يمكنك استخدام الإيموجي المميز مباشرة أو معرفه الرقمي
💡 فصل الإيموجيات العادية بفاصلة (,) لربطها بنفس الإيموجي المميز
                """.strip())
                return

            parts = args.strip().split(None, 3)
            if len(parts) < 3:
                await event.reply("❌ تنسيق غير صحيح. استخدم: إضافة_استبدال_قناة <معرف_القناة> <إيموجي_عادي> <إيموجي_مميز> [وصف]")
                return

            try:
                channel_id = int(parts[0])
            except ValueError:
                await event.reply("❌ معرف القناة يجب أن يكون رقماً")
                return

            # Check if channel is monitored
            if channel_id not in self.monitored_channels:
                await event.reply("❌ هذه القناة غير مراقبة. أضفها أولاً باستخدام أمر إضافة_قناة")
                return

            normal_emojis_part = parts[1]
            premium_part = parts[2]
            description = parts[3] if len(parts) > 3 else None

            # Split normal emojis by comma
            normal_emojis = [emoji.strip() for emoji in normal_emojis_part.split(',') if emoji.strip()]

            if not normal_emojis:
                await event.reply("❌ لا توجد إيموجيات عادية صالحة")
                return

            # Get custom emojis from message
            custom_emoji_ids = []
            if event.message.entities:
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        custom_emoji_ids.append(entity.document_id)

            # Determine premium emoji ID
            premium_emoji_id = None
            try:
                premium_emoji_id = int(premium_part)
            except ValueError:
                if custom_emoji_ids:
                    premium_emoji_id = custom_emoji_ids[0]
                else:
                    await event.reply("❌ لم أجد إيموجي مميز أو معرف صحيح")
                    return

            # Add replacements
            successful_count = 0
            failed_emojis = []
            existing_emojis = []

            for normal_emoji in normal_emojis:
                # Check if already exists for this channel
                if (channel_id in self.channel_emoji_mappings and 
                    normal_emoji in self.channel_emoji_mappings[channel_id]):
                    existing_emojis.append(normal_emoji)
                    continue

                success = await self.add_channel_emoji_replacement(channel_id, normal_emoji, premium_emoji_id, description)
                if success:
                    successful_count += 1
                else:
                    failed_emojis.append(normal_emoji)

            # Prepare response
            channel_info = self.monitored_channels[channel_id]
            channel_name = channel_info.get('title', 'Unknown Channel')
            
            response_parts = []
            if successful_count > 0:
                emoji_list = ", ".join(normal_emojis[:successful_count])
                response_parts.append(f"✅ تم إضافة {successful_count} استبدال للقناة {channel_name}:")
                response_parts.append(f"• {emoji_list} → إيموجي مميز (ID: {premium_emoji_id})")

            if existing_emojis:
                response_parts.append(f"⚠️ موجود مسبقاً: {', '.join(existing_emojis)}")

            if failed_emojis:
                response_parts.append(f"❌ فشل في إضافة: {', '.join(failed_emojis)}")

            if not response_parts:
                response_parts.append("❌ لم يتم إضافة أي استبدالات")

            await event.reply("\n".join(response_parts))

        except Exception as e:
            logger.error(f"Failed to add channel emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء إضافة استبدال الإيموجي للقناة")

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
                # Log for debugging
                logger.info(f"Message: is_private={event.is_private}, out={event.message.out}, chat_id={event.chat_id}, sender_id={event.sender_id}")
                
                # Handle private messages with commands
                # Include saved messages (messages to self) where sender_id equals chat_id
                if event.is_private and (not event.message.out or event.sender_id == event.chat_id):
                    logger.info("Processing private message or saved message")
                    await self.handle_private_message(event)
                    return
                
                # Check if message is from a monitored channel  
                event_peer_id = utils.get_peer_id(event.chat)
                if event_peer_id in self.monitored_channels:
                    logger.info(f"New message in monitored channel {event_peer_id}")
                    await self.replace_emojis_in_message(event)
                    
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
            
            logger.info("Bot is now running and monitoring channels...")
            
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