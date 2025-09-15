#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import asyncpg
import re
import json
from typing import Dict, List, Optional, Tuple, Union
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityCustomEmoji, User, Channel
from custom_parse_mode import CustomParseMode

# Telegram Bot API imports for control bot
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
    BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    InlineQueryHandler, ContextTypes
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('unified_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class UnifiedTelegramBot:
    """
    Unified bot combining both UserBot (Telethon) and Control Bot (python-telegram-bot)
    """
    
    def __init__(self):
        # UserBot environment variables
        self.api_id = int(os.getenv('API_ID', '0'))
        self.api_hash = os.getenv('API_HASH', '')
        self.session_string = os.getenv('SESSION_STRING', '')
        
        # Control Bot environment variables
        self.control_bot_token = os.getenv('CONTROL_BOT_TOKEN', '')
        
        # Common environment variables
        self.database_url = os.getenv('DATABASE_URL', '')
        self.userbot_admin_id = int(os.getenv('USERBOT_ADMIN_ID', '6602517122'))
        
        # Validate required environment variables
        if not all([self.api_id, self.api_hash, self.session_string, self.database_url, self.control_bot_token]):
            logger.error("Missing required environment variables")
            raise ValueError("Missing required environment variables")
        
        # Initialize Telegram UserBot client
        self.user_client = TelegramClient(
            StringSession(self.session_string),
            self.api_id, 
            self.api_hash
        )
        
        # Control Bot application will be initialized later
        self.control_app = None
        
        # Database connection pool
        self.db_pool: Optional[asyncpg.Pool] = None
        
        # Custom parse mode for premium emojis
        self.parse_mode = CustomParseMode('markdown')
        
        # Cache for emoji mappings and monitored channels
        self.emoji_mappings: Dict[str, int] = {}  # Global replacements
        self.channel_emoji_mappings: Dict[int, Dict[str, int]] = {}  # Channel-specific replacements
        self.monitored_channels: Dict[int, Dict[str, str]] = {}
        self.channel_replacement_status: Dict[int, bool] = {}
        
        # Cache for forwarding tasks
        self.forwarding_tasks: Dict[int, Dict[str, Union[int, bool]]] = {}
        
        # Cache for admin list
        self.admin_ids: set = {self.userbot_admin_id}
        
        # Arabic command mappings for UserBot
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
            
            # Create command queue table for control bot communication
            await self.create_command_queue_table()
            
            # Load cached data
            await self.load_emoji_mappings()
            await self.load_channel_emoji_mappings()
            await self.load_monitored_channels()
            await self.load_forwarding_tasks()
            await self.load_admin_ids()
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def create_command_queue_table(self):
        """Create command queue table for control bot communication"""
        if self.db_pool is None:
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS command_queue (
                        id SERIAL PRIMARY KEY,
                        command VARCHAR(100) NOT NULL,
                        parameters TEXT DEFAULT '',
                        requester_id BIGINT NOT NULL,
                        status VARCHAR(20) DEFAULT 'pending',
                        result TEXT DEFAULT '',
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMP WITH TIME ZONE DEFAULT NULL
                    )
                ''')
            logger.info("Command queue table created/verified successfully")
        except Exception as e:
            logger.error(f"Failed to create command queue table: {e}")

    async def load_emoji_mappings(self):
        """Load emoji mappings from database into cache"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT text_emoji, premium_emoji_id FROM emoji_replacements")
                self.emoji_mappings = {row['text_emoji']: row['premium_emoji_id'] for row in rows}
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
                rows = await conn.fetch("SELECT channel_id, text_emoji, premium_emoji_id FROM channel_emoji_replacements")
                
                # Group by channel_id
                for row in rows:
                    channel_id = row['channel_id']
                    if channel_id not in self.channel_emoji_mappings:
                        self.channel_emoji_mappings[channel_id] = {}
                    self.channel_emoji_mappings[channel_id][row['text_emoji']] = row['premium_emoji_id']
                
                total_mappings = sum(len(mappings) for mappings in self.channel_emoji_mappings.values())
                logger.info(f"Loaded {total_mappings} channel-specific emoji mappings for {len(self.channel_emoji_mappings)} channels")
                
        except Exception as e:
            logger.error(f"Failed to load channel emoji mappings: {e}")

    async def load_monitored_channels(self):
        """Load monitored channels from database"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT channel_id, channel_name, replacement_active FROM monitored_channels")
                
                for row in rows:
                    channel_id = row['channel_id']
                    self.monitored_channels[channel_id] = {
                        'name': row['channel_name'],
                        'replacement_active': row['replacement_active']
                    }
                    self.channel_replacement_status[channel_id] = row['replacement_active']
                    
                logger.info(f"Loaded {len(self.monitored_channels)} monitored channels from database")
                
                # Count active channels
                active_channels = sum(1 for status in self.channel_replacement_status.values() if status)
                logger.info(f"Replacement active in {active_channels} channels")
                
        except Exception as e:
            logger.error(f"Failed to load monitored channels: {e}")

    async def load_forwarding_tasks(self):
        """Load forwarding tasks from database"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT id, source_channel_id, target_channel_id, delay_seconds, active FROM forwarding_tasks")
                
                for row in rows:
                    task_id = row['id']
                    self.forwarding_tasks[task_id] = {
                        'source': row['source_channel_id'],
                        'target': row['target_channel_id'],
                        'delay': row['delay_seconds'],
                        'active': row['active']
                    }
                    
                active_tasks = sum(1 for task in self.forwarding_tasks.values() if task['active'])
                logger.info(f"Loaded {active_tasks} active forwarding tasks")
                
        except Exception as e:
            logger.error(f"Failed to load forwarding tasks: {e}")

    async def load_admin_ids(self):
        """Load admin IDs from database"""
        if self.db_pool is None:
            logger.error("Database pool not initialized")
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT user_id FROM bot_admins")
                self.admin_ids = {row['user_id'] for row in rows}
                logger.info(f"Loaded {len(self.admin_ids)} admin IDs from database")
        except Exception as e:
            logger.error(f"Failed to load admin IDs: {e}")

    async def start_userbot(self):
        """Start and configure UserBot"""
        try:
            logger.info("Starting UserBot...")
            await self.user_client.start()
            
            me = await self.user_client.get_me()
            logger.info(f"Bot started as: {me.first_name} (@{me.username})")
            
            # Set up event handlers for UserBot
            await self.setup_userbot_handlers()
            
            logger.info("Event handlers set up successfully")
            logger.info("Bot is now running and monitoring channels...")
            
            # Start command queue processor
            asyncio.create_task(self.process_command_queue())
            logger.info("Command queue processor started for Control Bot integration")
            
        except Exception as e:
            logger.error(f"Failed to start UserBot: {e}")
            raise

    async def setup_userbot_handlers(self):
        """Set up event handlers for UserBot"""
        # Handler for new messages in monitored channels
        @self.user_client.on(events.NewMessage())
        async def handle_new_message(event):
            await self.handle_channel_message(event)

        # Handler for admin commands
        @self.user_client.on(events.NewMessage(pattern=r'^[^\s]+', func=lambda e: e.is_private and e.sender_id in self.admin_ids))
        async def handle_admin_command(event):
            await self.handle_admin_command(event)

    async def handle_channel_message(self, event):
        """Handle messages in monitored channels for emoji replacement and forwarding"""
        try:
            # Check if channel is monitored
            channel_id = event.chat_id
            if channel_id not in self.monitored_channels:
                return
                
            # Check if replacement is active for this channel
            if not self.channel_replacement_status.get(channel_id, False):
                return
                
            # Get message text
            message_text = event.raw_text
            if not message_text:
                return
            
            # Replace emojis
            replaced_text, replacements_made = await self.replace_emojis_in_text(message_text, channel_id)
            
            if replacements_made > 0:
                try:
                    # Edit the message with replaced emojis
                    await event.edit(replaced_text, parse_mode=self.parse_mode)
                    logger.info(f"Replaced {replacements_made} emojis in channel {channel_id}")
                except Exception as e:
                    logger.error(f"Failed to edit message in channel {channel_id}: {e}")
                    
            # Handle forwarding tasks
            await self.handle_forwarding_tasks(event, channel_id)
            
        except Exception as e:
            logger.error(f"Error handling channel message: {e}")

    async def replace_emojis_in_text(self, text: str, channel_id: int) -> Tuple[str, int]:
        """Replace emojis in text using global and channel-specific mappings"""
        replacements_made = 0
        result_text = text
        
        # Channel-specific replacements (higher priority)
        if channel_id in self.channel_emoji_mappings:
            for text_emoji, premium_id in self.channel_emoji_mappings[channel_id].items():
                if text_emoji in result_text:
                    premium_emoji = f'<emoji id="{premium_id}">🎭</emoji>'
                    count = result_text.count(text_emoji)
                    result_text = result_text.replace(text_emoji, premium_emoji)
                    replacements_made += count
        
        # Global replacements (lower priority)
        for text_emoji, premium_id in self.emoji_mappings.items():
            if text_emoji in result_text:
                premium_emoji = f'<emoji id="{premium_id}">🎭</emoji>'
                count = result_text.count(text_emoji)
                result_text = result_text.replace(text_emoji, premium_emoji)
                replacements_made += count
        
        return result_text, replacements_made

    async def handle_forwarding_tasks(self, event, source_channel_id: int):
        """Handle forwarding tasks for messages"""
        try:
            for task_id, task in self.forwarding_tasks.items():
                if task['source'] == source_channel_id and task['active']:
                    # Schedule forwarding with delay
                    delay = task.get('delay', 0)
                    target_channel_id = task['target']
                    
                    if delay > 0:
                        asyncio.create_task(self.delayed_forward(event, target_channel_id, delay))
                    else:
                        await self.forward_message(event, target_channel_id)
                        
        except Exception as e:
            logger.error(f"Error in forwarding tasks: {e}")

    async def delayed_forward(self, event, target_channel_id: int, delay: int):
        """Forward message after delay"""
        try:
            await asyncio.sleep(delay)
            await self.forward_message(event, target_channel_id)
        except Exception as e:
            logger.error(f"Error in delayed forward: {e}")

    async def forward_message(self, event, target_channel_id: int):
        """Forward message to target channel"""
        try:
            await self.user_client.forward_messages(target_channel_id, event.message)
            logger.info(f"Forwarded message from {event.chat_id} to {target_channel_id}")
        except Exception as e:
            logger.error(f"Failed to forward message: {e}")

    async def handle_admin_command(self, event):
        """Handle admin commands for UserBot"""
        try:
            command = event.raw_text.strip()
            
            # Find matching Arabic command
            for arabic_cmd, english_cmd in self.arabic_commands.items():
                if command.startswith(arabic_cmd):
                    # Extract parameters
                    params = command[len(arabic_cmd):].strip()
                    # Execute command
                    await self.execute_userbot_command(english_cmd, params, event)
                    return
                    
            # If no Arabic command matched, show help
            if command in ['مساعدة', 'help']:
                await self.show_help(event)
                
        except Exception as e:
            logger.error(f"Error handling admin command: {e}")
            await event.reply(f"❌ خطأ في معالجة الأمر: {e}")

    async def execute_userbot_command(self, command: str, params: str, event):
        """Execute UserBot command"""
        if command == 'list_channels':
            await self.list_channels_command(event)
        elif command == 'add_channel':
            await self.add_channel_command(params, event)
        elif command == 'remove_channel':
            await self.remove_channel_command(params, event)
        elif command == 'list_emoji_replacements':
            await self.list_emoji_replacements_command(event)
        elif command == 'add_emoji_replacement':
            await self.add_emoji_replacement_command(params, event)
        elif command == 'delete_emoji_replacement':
            await self.delete_emoji_replacement_command(params, event)
        elif command == 'help_command':
            await self.show_help(event)
        else:
            await event.reply(f"❌ أمر غير معروف: {command}")

    async def list_channels_command(self, event):
        """List monitored channels"""
        if not self.monitored_channels:
            await event.reply("📺 **لا توجد قنوات مراقبة حالياً**")
            return
            
        message = "📺 **القنوات المراقبة:**\n\n"
        for channel_id, info in self.monitored_channels.items():
            status = "✅ نشط" if self.channel_replacement_status.get(channel_id, False) else "❌ معطل"
            message += f"• **{info['name']}** (ID: `{channel_id}`) - {status}\n"
            
        await event.reply(message)

    async def add_channel_command(self, params: str, event):
        """Add channel to monitoring"""
        try:
            if not params:
                await event.reply("❌ يرجى تحديد معرف القناة أو رابطها\n**مثال:** `إضافة_قناة @channel_username`")
                return
                
            # Get channel entity
            channel = await self.user_client.get_entity(params.strip())
            channel_id = channel.id
            channel_name = getattr(channel, 'title', getattr(channel, 'username', str(channel_id)))
            
            # Add to database
            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO monitored_channels (channel_id, channel_name, replacement_active)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (channel_id) DO UPDATE SET
                        channel_name = EXCLUDED.channel_name,
                        replacement_active = EXCLUDED.replacement_active
                    """, channel_id, channel_name, True)
                    
                # Update cache
                self.monitored_channels[channel_id] = {
                    'name': channel_name,
                    'replacement_active': True
                }
                self.channel_replacement_status[channel_id] = True
                
                await event.reply(f"✅ **تم إضافة القناة بنجاح**\n\n"
                                f"**اسم القناة:** {channel_name}\n"
                                f"**معرف القناة:** `{channel_id}`\n"
                                f"**حالة الاستبدال:** نشط")
            else:
                await event.reply("❌ خطأ في الاتصال بقاعدة البيانات")
                
        except Exception as e:
            logger.error(f"Failed to add channel: {e}")
            await event.reply(f"❌ فشل في إضافة القناة: {e}")

    async def remove_channel_command(self, params: str, event):
        """Remove channel from monitoring"""
        try:
            if not params:
                await event.reply("❌ يرجى تحديد معرف القناة\n**مثال:** `حذف_قناة -1001234567890`")
                return
                
            channel_id = int(params.strip())
            
            if channel_id not in self.monitored_channels:
                await event.reply("❌ القناة غير موجودة في قائمة المراقبة")
                return
                
            # Remove from database
            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.execute("DELETE FROM monitored_channels WHERE channel_id = $1", channel_id)
                    
                # Update cache
                channel_name = self.monitored_channels[channel_id]['name']
                del self.monitored_channels[channel_id]
                del self.channel_replacement_status[channel_id]
                
                await event.reply(f"✅ **تم حذف القناة بنجاح**\n\n"
                                f"**القناة المحذوفة:** {channel_name}\n"
                                f"**معرف القناة:** `{channel_id}`")
            else:
                await event.reply("❌ خطأ في الاتصال بقاعدة البيانات")
                
        except ValueError:
            await event.reply("❌ معرف القناة يجب أن يكون رقماً")
        except Exception as e:
            logger.error(f"Failed to remove channel: {e}")
            await event.reply(f"❌ فشل في حذف القناة: {e}")

    async def list_emoji_replacements_command(self, event):
        """List emoji replacements"""
        if not self.emoji_mappings:
            await event.reply("😀 **لا توجد استبدالات إيموجي حالياً**")
            return
            
        message = "😀 **قائمة الاستبدالات:**\n\n"
        for text_emoji, premium_id in list(self.emoji_mappings.items())[:20]:  # Show first 20
            message += f"• `{text_emoji}` → `{premium_id}`\n"
            
        if len(self.emoji_mappings) > 20:
            message += f"\n*... وأكثر من {len(self.emoji_mappings) - 20} استبدال آخر*"
            
        await event.reply(message)

    async def add_emoji_replacement_command(self, params: str, event):
        """Add emoji replacement"""
        try:
            parts = params.split()
            if len(parts) != 2:
                await event.reply("❌ تنسيق خاطئ\n**مثال:** `إضافة_استبدال 😀 5789604237543946959`")
                return
                
            text_emoji = parts[0]
            premium_id = int(parts[1])
            
            # Add to database
            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO emoji_replacements (text_emoji, premium_emoji_id)
                        VALUES ($1, $2)
                        ON CONFLICT (text_emoji) DO UPDATE SET
                        premium_emoji_id = EXCLUDED.premium_emoji_id
                    """, text_emoji, premium_id)
                    
                # Update cache
                self.emoji_mappings[text_emoji] = premium_id
                
                await event.reply(f"✅ **تم إضافة الاستبدال بنجاح**\n\n"
                                f"**الإيموجي:** {text_emoji}\n"
                                f"**معرف الإيموجي المميز:** `{premium_id}`")
            else:
                await event.reply("❌ خطأ في الاتصال بقاعدة البيانات")
                
        except ValueError:
            await event.reply("❌ معرف الإيموجي يجب أن يكون رقماً")
        except Exception as e:
            logger.error(f"Failed to add emoji replacement: {e}")
            await event.reply(f"❌ فشل في إضافة الاستبدال: {e}")

    async def delete_emoji_replacement_command(self, params: str, event):
        """Delete emoji replacement"""
        try:
            if not params:
                await event.reply("❌ يرجى تحديد الإيموجي المراد حذفه\n**مثال:** `حذف_استبدال 😀`")
                return
                
            text_emoji = params.strip()
            
            if text_emoji not in self.emoji_mappings:
                await event.reply("❌ الإيموجي غير موجود في قائمة الاستبدالات")
                return
                
            # Remove from database
            if self.db_pool:
                async with self.db_pool.acquire() as conn:
                    await conn.execute("DELETE FROM emoji_replacements WHERE text_emoji = $1", text_emoji)
                    
                # Update cache
                del self.emoji_mappings[text_emoji]
                
                await event.reply(f"✅ **تم حذف الاستبدال بنجاح**\n\n"
                                f"**الإيموجي المحذوف:** {text_emoji}")
            else:
                await event.reply("❌ خطأ في الاتصال بقاعدة البيانات")
                
        except Exception as e:
            logger.error(f"Failed to delete emoji replacement: {e}")
            await event.reply(f"❌ فشل في حذف الاستبدال: {e}")

    async def show_help(self, event):
        """Show help message"""
        help_text = """
🤖 **مساعدة البوت المدمج**

**📺 إدارة القنوات:**
• `عرض_القنوات` - عرض القنوات المراقبة
• `إضافة_قناة @channel` - إضافة قناة للمراقبة  
• `حذف_قناة channel_id` - حذف قناة من المراقبة

**😀 إدارة الإيموجي:**
• `عرض_الاستبدالات` - عرض قائمة الاستبدالات
• `إضافة_استبدال 😀 premium_id` - إضافة استبدال إيموجي
• `حذف_استبدال 😀` - حذف استبدال إيموجي

**🎛️ لوحة التحكم:**
اكتب اسم البوت في أي محادثة لفتح لوحة التحكم التفاعلية

**ℹ️ معلومات:**
• `مساعدة` - عرض هذه الرسالة
"""
        await event.reply(help_text)

    # Control Bot functionality
    async def start_control_bot(self):
        """Start Control Bot"""
        try:
            logger.info("Starting Control Bot...")
            
            # Create application
            self.control_app = Application.builder().token(self.control_bot_token).build()
            
            # Setup commands
            await self.setup_control_bot_commands()
            
            # Add handlers
            self.control_app.add_handler(CommandHandler("start", self.control_start_command))
            self.control_app.add_handler(CommandHandler("help", self.control_help_command))
            self.control_app.add_handler(InlineQueryHandler(self.inline_query_handler))
            self.control_app.add_handler(CallbackQueryHandler(self.callback_query_handler))
            
            # Initialize and start bot
            await self.control_app.initialize()
            await self.control_app.start()
            
            # Set inline mode
            me = await self.control_app.bot.get_me()
            logger.info(f"Control bot started: @{me.username}")
            logger.info("Inline mode enabled - users can type @botname in any chat")
            
            # Start polling in background
            asyncio.create_task(self.run_control_bot_polling())
            
        except Exception as e:
            logger.error(f"Failed to start control bot: {e}")
            raise

    async def run_control_bot_polling(self):
        """Run control bot polling"""
        try:
            await self.control_app.updater.start_polling()
            logger.info("Control bot is now running with inline mode...")
        except Exception as e:
            logger.error(f"Control bot polling error: {e}")

    async def setup_control_bot_commands(self):
        """Set up bot commands for Control Bot"""
        commands = [
            BotCommand("start", "بدء استخدام البوت"),
            BotCommand("help", "عرض المساعدة")
        ]
        try:
            await self.control_app.bot.set_my_commands(commands)
            logger.info("Control bot commands set successfully")
        except Exception as e:
            logger.error(f"Failed to set control bot commands: {e}")

    async def control_start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command for Control Bot"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ غير مخول لاستخدام هذا البوت")
            return
            
        message = (
            "🎛️ **مرحباً بك في لوحة التحكم**\n\n"
            "يمكنك استخدام هذا البوت بطريقتين:\n\n"
            "1️⃣ **Inline Mode**: اكتب اسم البوت في أي محادثة (@botname) لفتح لوحة التحكم\n"
            "2️⃣ **الأوامر المباشرة**: استخدم الأوامر العربية في محادثة البوت الرئيسي\n\n"
            "🔧 **الميزات المتاحة:**\n"
            "• 📺 إدارة القنوات المراقبة\n"
            "• 😀 إدارة استبدالات الإيموجي\n"
            "• 🔄 إدارة مهام النسخ التلقائي\n"
            "• 📊 عرض الإحصائيات\n\n"
            "اكتب /help للمزيد من التفاصيل"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')

    async def control_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command for Control Bot"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ غير مخول لاستخدام هذا البوت")
            return
            
        help_text = (
            "🆘 **مساعدة بوت التحكم**\n\n"
            "**🎛️ Inline Mode:**\n"
            "اكتب `@botname` في أي محادثة لفتح:\n"
            "• لوحة التحكم الرئيسية\n"
            "• إدارة القنوات والإيموجي\n"
            "• مهام النسخ والإحصائيات\n\n"
            "**📱 الأوامر المباشرة:**\n"
            "يمكنك أيضاً استخدام الأوامر العربية مباشرة مع البوت الرئيسي:\n"
            "• `عرض_القنوات` - عرض القنوات المراقبة\n"
            "• `إضافة_قناة @channel` - إضافة قناة\n"
            "• `عرض_الاستبدالات` - عرض الإيموجي\n"
            "• `مساعدة` - عرض جميع الأوامر\n\n"
            "**💡 نصيحة:** استخدم Inline Mode للوصول السريع للوحة التحكم التفاعلية!"
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def inline_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline queries for Control Bot"""
        query = update.inline_query.query.strip()
        user_id = update.inline_query.from_user.id
        
        # Check if user is authorized
        if user_id not in self.admin_ids:
            results = [
                InlineQueryResultArticle(
                    id="unauthorized",
                    title="❌ غير مخول",
                    description="عذراً، أنت غير مخول لاستخدام هذا البوت",
                    input_message_content=InputTextMessageContent("❌ غير مخول لاستخدام هذا البوت")
                )
            ]
            await update.inline_query.answer(results, cache_time=0)
            return
        
        # Create inline query results
        results = []
        
        if not query or "قائمة" in query or "menu" in query.lower():
            # Main menu
            results.append(
                InlineQueryResultArticle(
                    id="main_menu",
                    title="🎛️ لوحة التحكم الرئيسية",
                    description=f"📺 {len(self.monitored_channels)} قناة | 😀 {len(self.emoji_mappings)} استبدال | 🔄 {len(self.forwarding_tasks)} مهمة",
                    input_message_content=InputTextMessageContent(
                        "🎛️ **لوحة التحكم الرئيسية**\n\n"
                        f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                        f"😀 الاستبدالات العامة: {len(self.emoji_mappings)}\n"
                        f"🎯 استبدالات القنوات: {sum(len(m) for m in self.channel_emoji_mappings.values())}\n"
                        f"🔄 مهام النسخ: {len(self.forwarding_tasks)}\n\n"
                        "اختر خياراً من القائمة:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=self.get_main_menu_keyboard()
                )
            )
        
        await update.inline_query.answer(results, cache_time=0)

    def get_main_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Create main menu inline keyboard"""
        keyboard = [
            [
                InlineKeyboardButton("📺 إدارة القنوات", callback_data="channels_menu"),
                InlineKeyboardButton("😀 إدارة الإيموجي", callback_data="emoji_menu")
            ],
            [
                InlineKeyboardButton("🔄 مهام النسخ", callback_data="forwarding_menu"),
                InlineKeyboardButton("📊 الإحصائيات", callback_data="stats_menu")
            ],
            [
                InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu"),
                InlineKeyboardButton("❓ المساعدة", callback_data="help_menu")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries from inline keyboards"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        if user_id not in self.admin_ids:
            await query.edit_message_text("❌ غير مخول لاستخدام هذا البوت")
            return
        
        data = query.data
        
        if data == "main_menu":
            await query.edit_message_text(
                "🎛️ **لوحة التحكم الرئيسية**\n\n"
                f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                f"😀 الاستبدالات العامة: {len(self.emoji_mappings)}\n"
                f"🎯 استبدالات القنوات: {sum(len(m) for m in self.channel_emoji_mappings.values())}\n"
                f"🔄 مهام النسخ: {len(self.forwarding_tasks)}\n\n"
                "اختر خياراً من القائمة:",
                parse_mode='Markdown',
                reply_markup=self.get_main_menu_keyboard()
            )
        elif data == "channels_menu":
            await query.edit_message_text(
                f"📺 **إدارة القنوات**\n\n"
                f"القنوات المراقبة: {len(self.monitored_channels)}\n\n"
                "استخدم الأوامر العربية في البوت الرئيسي لإدارة القنوات:\n"
                "• `عرض_القنوات`\n"
                "• `إضافة_قناة @channel`\n" 
                "• `حذف_قناة channel_id`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
            )
        elif data == "emoji_menu":
            await query.edit_message_text(
                f"😀 **إدارة الإيموجي**\n\n"
                f"الاستبدالات العامة: {len(self.emoji_mappings)}\n"
                f"استبدالات القنوات: {sum(len(m) for m in self.channel_emoji_mappings.values())}\n\n"
                "استخدم الأوامر العربية في البوت الرئيسي لإدارة الإيموجي:\n"
                "• `عرض_الاستبدالات`\n"
                "• `إضافة_استبدال 😀 id`\n"
                "• `حذف_استبدال 😀`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
            )
        else:
            await query.edit_message_text(
                "🔧 **قيد التطوير**\n\nهذه الميزة قيد التطوير حالياً",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
            )

    async def process_command_queue(self):
        """Process command queue for control bot integration"""
        while True:
            try:
                if self.db_pool:
                    async with self.db_pool.acquire() as conn:
                        # Get pending commands
                        rows = await conn.fetch("""
                            SELECT id, command, parameters, requester_id
                            FROM command_queue 
                            WHERE status = 'pending'
                            ORDER BY created_at ASC
                            LIMIT 10
                        """)
                        
                        for row in rows:
                            command_id = row['id']
                            command = row['command']
                            params = row['parameters']
                            requester_id = row['requester_id']
                            
                            # Process command (placeholder for now)
                            result = f"Command {command} processed with params: {params}"
                            
                            # Update command status
                            await conn.execute("""
                                UPDATE command_queue 
                                SET status = 'completed', result = $1, processed_at = CURRENT_TIMESTAMP
                                WHERE id = $2
                            """, result, command_id)
                            
                await asyncio.sleep(5)  # Check every 5 seconds
            except Exception as e:
                logger.error(f"Failed to process command queue: {e}")
                await asyncio.sleep(5)

    async def start_unified_bot(self):
        """Start both UserBot and Control Bot"""
        try:
            # Initialize database first
            await self.init_database()
            
            # Start UserBot
            await self.start_userbot()
            
            # Start Control Bot
            await self.start_control_bot()
            
            logger.info("🚀 Unified bot (UserBot + Control Bot) started successfully!")
            
            # Keep running
            await self.user_client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"Failed to start unified bot: {e}")
            raise
        finally:
            if self.db_pool:
                await self.db_pool.close()
            if self.control_app:
                await self.control_app.shutdown()

async def main():
    """Main function to start the unified bot"""
    try:
        bot = UnifiedTelegramBot()
        await bot.start_unified_bot()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())