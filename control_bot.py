
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import asyncpg
import json
from typing import Dict, List, Optional
from dotenv import load_dotenv
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
        logging.FileHandler('control_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TelegramControlBot:
    """
    بوت التحكم مع دعم Inline Mode لإدارة UserBot
    """
    
    def __init__(self):
        # Environment variables
        self.bot_token = os.getenv('CONTROL_BOT_TOKEN', '')
        self.database_url = os.getenv('DATABASE_URL', '')
        self.userbot_admin_id = int(os.getenv('USERBOT_ADMIN_ID', '6602517122'))
        
        if not all([self.bot_token, self.database_url]):
            logger.error("Missing required environment variables: CONTROL_BOT_TOKEN, DATABASE_URL")
            raise ValueError("Missing required environment variables")
        
        # Database connection pool
        self.db_pool: Optional[asyncpg.Pool] = None
        
        # Admin IDs for control bot (can be different from userbot admins)
        self.admin_ids: set = {self.userbot_admin_id}  # Default admin
        
        # Cache for quick access
        self.monitored_channels: Dict[int, Dict[str, str]] = {}
        self.emoji_mappings_count: int = 0
        self.channel_emoji_mappings_count: int = 0
        self.forwarding_tasks_count: int = 0

    async def init_database(self):
        """Initialize database connection pool"""
        try:
            self.db_pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
            logger.info("Control bot database connection initialized")
            
            # Create command queue table for communication with UserBot
            await self.create_command_queue_table()
            
            # Load cached data
            await self.load_cached_data()
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def create_command_queue_table(self):
        """Create table for command queue between control bot and userbot"""
        if self.db_pool is None:
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS command_queue (
                        id SERIAL PRIMARY KEY,
                        command TEXT NOT NULL,
                        args TEXT,
                        requested_by BIGINT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        result TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMP
                    )
                """)
                logger.info("Command queue table created successfully")
                
        except Exception as e:
            logger.error(f"Failed to create command queue table: {e}")

    async def load_cached_data(self):
        """Load data for quick access in inline queries"""
        if self.db_pool is None:
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                # Load monitored channels
                channels = await conn.fetch(
                    "SELECT channel_id, channel_username, channel_title FROM monitored_channels WHERE is_active = TRUE"
                )
                self.monitored_channels = {
                    row['channel_id']: {
                        'username': row['channel_username'] or '',
                        'title': row['channel_title'] or 'Unknown Channel'
                    }
                    for row in channels
                }
                
                # Count emoji mappings
                self.emoji_mappings_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM emoji_replacements"
                ) or 0
                
                # Count channel emoji mappings
                self.channel_emoji_mappings_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM channel_emoji_replacements"
                ) or 0
                
                # Count forwarding tasks
                self.forwarding_tasks_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM forwarding_tasks WHERE is_active = TRUE"
                ) or 0
                
                logger.info(f"Loaded cache: {len(self.monitored_channels)} channels, "
                           f"{self.emoji_mappings_count} global emojis, "
                           f"{self.channel_emoji_mappings_count} channel emojis, "
                           f"{self.forwarding_tasks_count} forwarding tasks")
                
        except Exception as e:
            logger.error(f"Failed to load cached data: {e}")

    async def queue_command(self, command: str, args: str = "", requested_by: int = 0) -> bool:
        """Queue command for UserBot to process"""
        if self.db_pool is None:
            return False
            
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO command_queue (command, args, requested_by) VALUES ($1, $2, $3)",
                    command, args, requested_by
                )
                logger.info(f"Queued command: {command} with args: {args}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to queue command: {e}")
            return False

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

    def get_channels_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Create channels management menu"""
        keyboard = [
            [
                InlineKeyboardButton("📋 عرض القنوات", callback_data="list_channels"),
                InlineKeyboardButton("➕ إضافة قناة", callback_data="add_channel")
            ],
            [
                InlineKeyboardButton("🔄 حالة الاستبدال", callback_data="channel_replacement_status"),
                InlineKeyboardButton("❌ حذف قناة", callback_data="remove_channel")
            ],
            [InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_emoji_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Create emoji management menu"""
        keyboard = [
            [
                InlineKeyboardButton("📋 الاستبدالات العامة", callback_data="list_global_emojis"),
                InlineKeyboardButton("📝 استبدالات القنوات", callback_data="list_channel_emojis")
            ],
            [
                InlineKeyboardButton("➕ إضافة استبدال عام", callback_data="add_global_emoji"),
                InlineKeyboardButton("🎯 إضافة استبدال قناة", callback_data="add_channel_emoji")
            ],
            [
                InlineKeyboardButton("🗑️ حذف استبدال", callback_data="delete_emoji"),
                InlineKeyboardButton("🧹 تنظيف مكرر", callback_data="clean_duplicates")
            ],
            [InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_forwarding_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Create forwarding tasks menu"""
        keyboard = [
            [
                InlineKeyboardButton("📋 عرض المهام", callback_data="list_forwarding_tasks"),
                InlineKeyboardButton("➕ إضافة مهمة", callback_data="add_forwarding_task")
            ],
            [
                InlineKeyboardButton("✅ تفعيل مهمة", callback_data="activate_task"),
                InlineKeyboardButton("❌ تعطيل مهمة", callback_data="deactivate_task")
            ],
            [
                InlineKeyboardButton("⏱️ تعديل التأخير", callback_data="update_delay"),
                InlineKeyboardButton("🗑️ حذف مهمة", callback_data="delete_task")
            ],
            [InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def inline_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline queries"""
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
                    description=f"📺 {len(self.monitored_channels)} قناة | 😀 {self.emoji_mappings_count + self.channel_emoji_mappings_count} استبدال | 🔄 {self.forwarding_tasks_count} مهمة",
                    input_message_content=InputTextMessageContent(
                        "🎛️ **لوحة التحكم الرئيسية**\n\n"
                        f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                        f"😀 الاستبدالات العامة: {self.emoji_mappings_count}\n"
                        f"🎯 استبدالات القنوات: {self.channel_emoji_mappings_count}\n"
                        f"🔄 مهام النسخ: {self.forwarding_tasks_count}\n\n"
                        "اختر خياراً من القائمة:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=self.get_main_menu_keyboard()
                )
            )
        
        if "قناة" in query or "channel" in query.lower():
            # Channel management
            results.append(
                InlineQueryResultArticle(
                    id="channels_menu",
                    title="📺 إدارة القنوات",
                    description=f"إدارة {len(self.monitored_channels)} قناة مراقبة",
                    input_message_content=InputTextMessageContent(
                        f"📺 **إدارة القنوات**\n\n"
                        f"القنوات المراقبة: {len(self.monitored_channels)}\n\n"
                        "اختر عملية:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=self.get_channels_menu_keyboard()
                )
            )
        
        if "إيموجي" in query or "emoji" in query.lower():
            # Emoji management
            results.append(
                InlineQueryResultArticle(
                    id="emoji_menu",
                    title="😀 إدارة الإيموجي",
                    description=f"إدارة {self.emoji_mappings_count + self.channel_emoji_mappings_count} استبدال إيموجي",
                    input_message_content=InputTextMessageContent(
                        f"😀 **إدارة الإيموجي**\n\n"
                        f"الاستبدالات العامة: {self.emoji_mappings_count}\n"
                        f"استبدالات القنوات: {self.channel_emoji_mappings_count}\n\n"
                        "اختر عملية:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=self.get_emoji_menu_keyboard()
                )
            )
        
        if "نسخ" in query or "forward" in query.lower():
            # Forwarding management
            results.append(
                InlineQueryResultArticle(
                    id="forwarding_menu",
                    title="🔄 مهام النسخ",
                    description=f"إدارة {self.forwarding_tasks_count} مهمة نسخ",
                    input_message_content=InputTextMessageContent(
                        f"🔄 **مهام النسخ**\n\n"
                        f"المهام النشطة: {self.forwarding_tasks_count}\n\n"
                        "اختر عملية:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=self.get_forwarding_menu_keyboard()
                )
            )
        
        # If no specific results, show main menu
        if not results:
            results.append(
                InlineQueryResultArticle(
                    id="main_menu_default",
                    title="🎛️ لوحة التحكم",
                    description="افتح لوحة التحكم الرئيسية",
                    input_message_content=InputTextMessageContent(
                        "🎛️ **لوحة التحكم**\n\nاختر خياراً:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=self.get_main_menu_keyboard()
                )
            )
        
        await update.inline_query.answer(results, cache_time=1)

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
                f"😀 الاستبدالات العامة: {self.emoji_mappings_count}\n"
                f"🎯 استبدالات القنوات: {self.channel_emoji_mappings_count}\n"
                f"🔄 مهام النسخ: {self.forwarding_tasks_count}\n\n"
                "اختر خياراً من القائمة:",
                parse_mode='Markdown',
                reply_markup=self.get_main_menu_keyboard()
            )
        
        elif data == "channels_menu":
            await query.edit_message_text(
                f"📺 **إدارة القنوات**\n\n"
                f"القنوات المراقبة: {len(self.monitored_channels)}\n\n"
                "اختر عملية:",
                parse_mode='Markdown',
                reply_markup=self.get_channels_menu_keyboard()
            )
        
        elif data == "emoji_menu":
            await query.edit_message_text(
                f"😀 **إدارة الإيموجي**\n\n"
                f"الاستبدالات العامة: {self.emoji_mappings_count}\n"
                f"استبدالات القنوات: {self.channel_emoji_mappings_count}\n\n"
                "اختر عملية:",
                parse_mode='Markdown',
                reply_markup=self.get_emoji_menu_keyboard()
            )
        
        elif data == "forwarding_menu":
            await query.edit_message_text(
                f"🔄 **مهام النسخ**\n\n"
                f"المهام النشطة: {self.forwarding_tasks_count}\n\n"
                "اختر عملية:",
                parse_mode='Markdown',
                reply_markup=self.get_forwarding_menu_keyboard()
            )
        
        elif data == "list_channels":
            # Queue command to UserBot and show result
            await self.queue_command("list_channels", "", user_id)
            await query.edit_message_text(
                "📺 **قائمة القنوات المراقبة**\n\n"
                "⏳ جاري تحميل البيانات من UserBot...\n\n"
                "📝 ستظهر النتيجة في رسالة منفصلة قريباً.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ العودة", callback_data="channels_menu")
                ]])
            )
        
        elif data == "stats_menu":
            # Reload cache for fresh stats
            await self.load_cached_data()
            
            stats_text = (
                "📊 **إحصائيات النظام**\n\n"
                f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                f"😀 الاستبدالات العامة: {self.emoji_mappings_count}\n"
                f"🎯 استبدالات القنوات: {self.channel_emoji_mappings_count}\n"
                f"🔄 مهام النسخ النشطة: {self.forwarding_tasks_count}\n\n"
                f"📈 إجمالي الاستبدالات: {self.emoji_mappings_count + self.channel_emoji_mappings_count}"
            )
            
            await query.edit_message_text(
                stats_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 تحديث", callback_data="stats_menu"),
                    InlineKeyboardButton("⬅️ العودة", callback_data="main_menu")
                ]])
            )
        
        elif data == "help_menu":
            help_text = (
                "❓ **المساعدة**\n\n"
                "🎯 **كيفية الاستخدام:**\n"
                "• اكتب `@botname` في أي محادثة\n"
                "• اختر القائمة المطلوبة\n"
                "• استخدم الأزرار للتحكم\n\n"
                "🔄 **الأوامر ترسل إلى:**\n"
                f"• UserBot: @Testtt1200\n"
                "• النتائج تظهر في رسائل منفصلة\n\n"
                "📞 **للدعم:**\n"
                "• تحقق من السجلات\n"
                "• تأكد من اتصال قاعدة البيانات"
            )
            
            await query.edit_message_text(
                help_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ العودة", callback_data="main_menu")
                ]])
            )
        
        # Add more handlers for other callback data...

    async def setup_bot_commands(self, app: Application):
        """Setup bot commands"""
        commands = [
            BotCommand("start", "بدء البوت"),
            BotCommand("help", "المساعدة"),
        ]
        await app.bot.set_my_commands(commands)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text(
                "❌ عذراً، أنت غير مخول لاستخدام هذا البوت.\n"
                "📞 للحصول على الصلاحية، تواصل مع المطور."
            )
            return
        
        await update.message.reply_text(
            "🎛️ **أهلاً بك في بوت التحكم!**\n\n"
            "🚀 **طرق الاستخدام:**\n\n"
            "1️⃣ **Inline Mode (الأفضل):**\n"
            f"   • اكتب `@{context.bot.username}` في أي محادثة\n"
            "   • اختر القائمة المطلوبة\n"
            "   • استخدم الأزرار للتحكم\n\n"
            "2️⃣ **الأوامر المباشرة:**\n"
            "   • /help - المساعدة\n\n"
            "💡 **ملاحظة:** جميع الأوامر ترسل إلى UserBot ويتم تنفيذها هناك.",
            parse_mode='Markdown',
            reply_markup=self.get_main_menu_keyboard()
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await update.message.reply_text(
            "❓ **المساعدة - بوت التحكم**\n\n"
            "🎯 **الاستخدام الأساسي:**\n"
            f"• اكتب `@{context.bot.username}` في أي محادثة\n"
            "• ستظهر لك خيارات لوحة التحكم\n"
            "• اختر الخيار المطلوب واستخدم الأزرار\n\n"
            "📋 **القوائم المتاحة:**\n"
            "• 📺 إدارة القنوات\n"
            "• 😀 إدارة الإيموجي\n"
            "• 🔄 مهام النسخ\n"
            "• 📊 الإحصائيات\n\n"
            "⚡ **مزايا Inline Mode:**\n"
            "• يعمل في أي محادثة\n"
            "• واجهة تفاعلية\n"
            "• لا حاجة للرسائل الخاصة\n\n"
            "🔗 **الربط مع UserBot:**\n"
            "• الأوامر ترسل تلقائياً إلى UserBot\n"
            "• النتائج تظهر في رسائل منفصلة\n"
            "• تزامن كامل مع قاعدة البيانات",
            parse_mode='Markdown'
        )

    async def start_bot(self):
        """Start the control bot"""
        try:
            logger.info("Starting Telegram Control Bot...")
            
            # Initialize database
            await self.init_database()
            
            # Create application
            app = Application.builder().token(self.bot_token).build()
            
            # Setup commands
            await self.setup_bot_commands(app)
            
            # Add handlers
            app.add_handler(CommandHandler("start", self.start_command))
            app.add_handler(CommandHandler("help", self.help_command))
            app.add_handler(InlineQueryHandler(self.inline_query_handler))
            app.add_handler(CallbackQueryHandler(self.callback_query_handler))
            
            # Start bot
            await app.initialize()
            await app.start()
            
            # Set inline mode in bot settings
            me = await app.bot.get_me()
            logger.info(f"Control bot started: @{me.username}")
            logger.info("Inline mode enabled - users can type @botname in any chat")
            
            # Run bot
            await app.updater.start_polling()
            
            logger.info("Control bot is now running with inline mode...")
            
            # Keep running
            await asyncio.Event().wait()
            
        except Exception as e:
            logger.error(f"Failed to start control bot: {e}")
            raise
        finally:
            if self.db_pool:
                await self.db_pool.close()

async def main():
    """Main function"""
    bot = TelegramControlBot()
    
    try:
        await bot.start_bot()
    except KeyboardInterrupt:
        logger.info("Control bot stopped by user")
    except Exception as e:
        logger.error(f"Control bot crashed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
