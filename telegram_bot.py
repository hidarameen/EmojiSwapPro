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
        self.emoji_mappings: Dict[str, int] = {}
        self.monitored_channels: Dict[int, Dict[str, str]] = {}
        
        # Arabic command mappings
        self.arabic_commands = {
            'إضافة_استبدال': 'add_emoji_replacement',
            'عرض_الاستبدالات': 'list_emoji_replacements', 
            'حذف_استبدال': 'delete_emoji_replacement',
            'إضافة_قناة': 'add_channel',
            'عرض_القنوات': 'list_channels',
            'حذف_قناة': 'remove_channel',
            'معرف_ايموجي': 'get_emoji_id',
            'مساعدة': 'help_command'
        }

    async def init_database(self):
        """Initialize database connection pool"""
        try:
            self.db_pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=10)
            logger.info("Database connection pool initialized successfully")
            
            # Load cached data
            await self.load_emoji_mappings()
            await self.load_monitored_channels()
            
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
        """Extract all emojis from text using regex"""
        # Unicode emoji regex pattern
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
            "]+", flags=re.UNICODE
        )
        
        return emoji_pattern.findall(text)

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
            
            for emoji in found_emojis:
                if emoji in self.emoji_mappings:
                    premium_emoji_id = self.emoji_mappings[emoji]
                    # Replace emoji with markdown format for premium emoji
                    premium_emoji_markdown = f"[{emoji}](emoji/{premium_emoji_id})"
                    modified_text = modified_text.replace(emoji, premium_emoji_markdown)
                    replacements_made.append(emoji)
            
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
                    
                    logger.info(f"Replaced emojis in message {message.id}: {replacements_made}")
                    
                except Exception as edit_error:
                    logger.error(f"Failed to edit message {message.id}: {edit_error}")
            
        except Exception as e:
            logger.error(f"Failed to replace emojis in message: {e}")

    async def handle_private_message(self, event):
        """Handle private messages with Arabic commands"""
        try:
            message_text = event.message.text.strip()
            chat_id = event.chat_id
            logger.info(f"Handling private message: '{message_text}' from {chat_id}")
            
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
        """Handle add emoji replacement command"""
        try:
            # New format: "😀 🔥 وصف اختياري" where 🔥 is a premium emoji
            parts = args.split(None, 2)
            if len(parts) < 2:
                await event.reply("الاستخدام: إضافة_استبدال <إيموجي_عادي> <إيموجي_مميز> [وصف]")
                return
            
            normal_emoji = parts[0]
            description = parts[2] if len(parts) > 2 else None
            
            # Find premium emoji in the message entities
            premium_emoji_id = None
            if event.message.entities:
                for entity in event.message.entities:
                    if isinstance(entity, MessageEntityCustomEmoji):
                        premium_emoji_id = entity.document_id
                        break
            
            # Fallback: try to parse as number (old format support)
            if premium_emoji_id is None:
                try:
                    premium_emoji_id = int(parts[1])
                except ValueError:
                    await event.reply("""
❌ لم أجد إيموجي مميز في الرسالة.

📋 طرق الاستخدام:
1. الطريقة الجديدة (مستحسنة): إضافة_استبدال 😀 🔥 وصف
   استخدم إيموجي مميز حقيقي بدلاً من 🔥

2. الطريقة القديمة: إضافة_استبدال 😀 1234567890 وصف
   استخدم معرف الإيموجي الرقمي

💡 استخدم أمر "معرف_ايموجي" لمعرفة معرف أي إيموجي مميز
                    """.strip())
                    return
            
            success = await self.add_emoji_replacement(normal_emoji, premium_emoji_id, description)
            
            if success:
                await event.reply(f"✅ تم إضافة استبدال الإيموجي بنجاح!\n{normal_emoji} ← إيموجي مميز (ID: {premium_emoji_id})")
            else:
                await event.reply("❌ فشل في إضافة استبدال الإيموجي")
                
        except Exception as e:
            logger.error(f"Failed to add emoji replacement: {e}")
            await event.reply("حدث خطأ أثناء إضافة استبدال الإيموجي")

    async def cmd_list_emoji_replacements(self, event, args: str):
        """Handle list emoji replacements command"""
        try:
            if not self.emoji_mappings:
                await event.reply("لا توجد استبدالات إيموجي محفوظة")
                return
            
            response = "📋 قائمة استبدالات الإيموجي:\n\n"
            for normal_emoji, premium_id in self.emoji_mappings.items():
                response += f"{normal_emoji} -> {premium_id}\n"
            
            await event.reply(response)
            
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

    async def cmd_help_command(self, event, args: str):
        """Handle help command"""
        help_text = """
🤖 أوامر بوت استبدال الإيموجي:

📝 إدارة الاستبدالات:
• إضافة_استبدال <إيموجي_عادي> <إيموجي_مميز> [وصف]
• عرض_الاستبدالات - عرض جميع الاستبدالات
• حذف_استبدال <إيموجي> - حذف استبدال

📺 إدارة القنوات:
• إضافة_قناة <معرف_أو_اسم_مستخدم> - إضافة قناة للمراقبة
• عرض_القنوات - عرض القنوات المراقبة
• حذف_قناة <معرف_القناة> - حذف قناة من المراقبة

🔍 أدوات مساعدة:
• معرف_ايموجي <إيموجي_مميز> - عرض معرف الإيموجي المميز
• أو رد على رسالة تحتوي على إيموجي مميز بكلمة "معرف_ايموجي"

❓ مساعدة - عرض هذه الرسالة

ملاحظة: جميع الأوامر تعمل في الرسائل الخاصة فقط.
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
                        response = "🔍 معرفات الإيموجي المميز في الرسالة:\n\n"
                        for idx, emoji_id in enumerate(custom_emojis, 1):
                            response += f"• الإيموجي {idx}: `{emoji_id}`\n"
                        response += "\nيمكنك نسخ المعرف واستخدامه مع أمر إضافة_استبدال"
                        await event.reply(response)
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
                    response = "🔍 معرفات الإيموجي المميز في رسالتك:\n\n"
                    for idx, emoji_id in enumerate(custom_emojis, 1):
                        response += f"• الإيموجي {idx}: `{emoji_id}`\n"
                    response += "\nيمكنك نسخ المعرف واستخدامه مع أمر إضافة_استبدال"
                    await event.reply(response)
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