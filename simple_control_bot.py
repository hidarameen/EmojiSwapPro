#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import asyncpg
from typing import Dict, Optional
from dotenv import load_dotenv

# Try different import approaches for telegram bot
try:
    from telegram.ext import Application, CommandHandler, InlineQueryHandler, CallbackQueryHandler, ContextTypes
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
    TELEGRAM_IMPORT_SUCCESS = True
    print("✅ Successfully imported telegram modules")
except ImportError as e:
    print(f"❌ Failed to import telegram: {e}")
    TELEGRAM_IMPORT_SUCCESS = False

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('simple_control_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SimpleControlBot:
    """بوت تحكم بسيط مع Inline Mode"""
    
    def __init__(self):
        self.bot_token = os.getenv('CONTROL_BOT_TOKEN', '')
        self.database_url = os.getenv('DATABASE_URL', '')
        self.userbot_admin_id = int(os.getenv('USERBOT_ADMIN_ID', '6602517122'))
        
        if not all([self.bot_token, self.database_url]):
            logger.error("Missing required environment variables: CONTROL_BOT_TOKEN, DATABASE_URL")
            raise ValueError("Missing required environment variables")
        
        self.db_pool: Optional[asyncpg.Pool] = None
        self.admin_ids: set = {self.userbot_admin_id}
        
        # Cache for quick access
        self.monitored_channels: Dict = {}
        self.emoji_mappings_count: int = 0
        self.forwarding_tasks_count: int = 0

    async def init_database(self):
        """Initialize database connection"""
        try:
            self.db_pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
            logger.info("Database connection initialized")
            await self.load_cached_data()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

    async def load_cached_data(self):
        """Load cached data from database"""
        if not self.db_pool:
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                # Load monitored channels
                channels = await conn.fetch("SELECT channel_id, channel_name FROM monitored_channels")
                self.monitored_channels = {row['channel_id']: row['channel_name'] for row in channels}
                
                # Load emoji mappings count
                emoji_count = await conn.fetchval("SELECT COUNT(*) FROM emoji_replacements")
                self.emoji_mappings_count = emoji_count or 0
                
                # Load forwarding tasks count
                task_count = await conn.fetchval("SELECT COUNT(*) FROM forwarding_tasks WHERE active = true")
                self.forwarding_tasks_count = task_count or 0
                
                logger.info(f"Loaded: {len(self.monitored_channels)} channels, {self.emoji_mappings_count} emojis, {self.forwarding_tasks_count} tasks")
        except Exception as e:
            logger.error(f"Failed to load cached data: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text("❌ غير مخول لاستخدام هذا البوت")
            return
            
        message = (
            "🎛️ **مرحباً بك في لوحة التحكم**\n\n"
            "استخدم هذا البوت عبر Inline Mode:\n"
            "اكتب اسم البوت في أي محادثة للوصول للوحة التحكم\n\n"
            f"📊 **الإحصائيات:**\n"
            f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
            f"😀 استبدالات الإيموجي: {self.emoji_mappings_count}\n"
            f"🔄 مهام النسخ: {self.forwarding_tasks_count}"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')

    async def inline_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline queries"""
        query = update.inline_query.query.strip()
        user_id = update.inline_query.from_user.id
        
        logger.info(f"Inline query from user {user_id}: '{query}'")
        
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
        
        # Refresh cached data
        await self.load_cached_data()
        
        # Create inline query results
        results = []
        
        # Always show main menu first
        results.append(
            InlineQueryResultArticle(
                id="main_menu",
                title="🎛️ لوحة التحكم الرئيسية",
                description=f"📺 {len(self.monitored_channels)} قناة | 😀 {self.emoji_mappings_count} استبدال | 🔄 {self.forwarding_tasks_count} مهمة",
                input_message_content=InputTextMessageContent(
                    "🎛️ **لوحة التحكم الرئيسية**\n\n"
                    f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                    f"😀 استبدالات الإيموجي: {self.emoji_mappings_count}\n"
                    f"🔄 مهام النسخ النشطة: {self.forwarding_tasks_count}\n\n"
                    "**📱 استخدم الأوامر العربية مع البوت الرئيسي:**\n"
                    "• `عرض_القنوات` - مشاهدة القنوات المراقبة\n"
                    "• `عرض_الاستبدالات` - مشاهدة استبدالات الإيموجي\n"
                    "• `عرض_مهام_التوجيه` - مشاهدة مهام النسخ\n"
                    "• `مساعدة` - عرض جميع الأوامر المتاحة\n\n"
                    "💡 **نصيحة:** البوت الرئيسي يدعم الأوامر العربية الكاملة!",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_main_menu_keyboard()
            )
        )
        
        # Add specific menus based on query
        if "قناة" in query or "channel" in query.lower():
            results.append(
                InlineQueryResultArticle(
                    id="channels_menu",
                    title="📺 إدارة القنوات",
                    description=f"إدارة {len(self.monitored_channels)} قناة مراقبة",
                    input_message_content=InputTextMessageContent(
                        f"📺 **إدارة القنوات**\n\n"
                        f"القنوات المراقبة: {len(self.monitored_channels)}\n\n"
                        "**الأوامر المتاحة:**\n"
                        "• `عرض_القنوات` - عرض جميع القنوات\n"
                        "• `إضافة_قناة @channel` - إضافة قناة جديدة\n"
                        "• `حذف_قناة channel_id` - حذف قناة\n"
                        "• `تفعيل_استبدال_قناة channel_id` - تفعيل الاستبدال\n"
                        "• `تعطيل_استبدال_قناة channel_id` - تعطيل الاستبدال",
                        parse_mode='Markdown'
                    ),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
                )
            )
        
        if "إيموجي" in query or "emoji" in query.lower():
            results.append(
                InlineQueryResultArticle(
                    id="emoji_menu",
                    title="😀 إدارة الإيموجي",
                    description=f"إدارة {self.emoji_mappings_count} استبدال إيموجي",
                    input_message_content=InputTextMessageContent(
                        f"😀 **إدارة الإيموجي**\n\n"
                        f"استبدالات الإيموجي: {self.emoji_mappings_count}\n\n"
                        "**الأوامر المتاحة:**\n"
                        "• `عرض_الاستبدالات` - عرض جميع الاستبدالات\n"
                        "• `إضافة_استبدال 😀 premium_id` - إضافة استبدال جديد\n"
                        "• `حذف_استبدال 😀` - حذف استبدال\n"
                        "• `إضافة_استبدال_قناة channel_id 😀 premium_id` - استبدال خاص بقناة\n"
                        "• `معرف_ايموجي` - الحصول على معرف إيموجي مميز",
                        parse_mode='Markdown'
                    ),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
                )
            )

        logger.info(f"Returning {len(results)} inline results")
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
            await self.load_cached_data()
            await query.edit_message_text(
                "🎛️ **لوحة التحكم الرئيسية**\n\n"
                f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                f"😀 استبدالات الإيموجي: {self.emoji_mappings_count}\n"
                f"🔄 مهام النسخ النشطة: {self.forwarding_tasks_count}\n\n"
                "استخدم الأوامر العربية مع البوت الرئيسي لإدارة كاملة",
                parse_mode='Markdown',
                reply_markup=self.get_main_menu_keyboard()
            )
        elif data == "channels_menu":
            await query.edit_message_text(
                f"📺 **إدارة القنوات**\n\n"
                f"القنوات المراقبة: {len(self.monitored_channels)}\n\n"
                "استخدم الأوامر العربية التالية مع البوت الرئيسي:\n"
                "• `عرض_القنوات`\n"
                "• `إضافة_قناة @channel`\n" 
                "• `حذف_قناة channel_id`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
            )
        elif data == "emoji_menu":
            await query.edit_message_text(
                f"😀 **إدارة الإيموجي**\n\n"
                f"استبدالات الإيموجي: {self.emoji_mappings_count}\n\n"
                "استخدم الأوامر العربية التالية مع البوت الرئيسي:\n"
                "• `عرض_الاستبدالات`\n"
                "• `إضافة_استبدال 😀 id`\n"
                "• `حذف_استبدال 😀`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
            )
        else:
            await query.edit_message_text(
                "🔧 **قيد التطوير**\n\nهذه الميزة قيد التطوير حالياً\n\n"
                "استخدم الأوامر العربية مع البوت الرئيسي للحصول على وظائف كاملة",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ العودة للقائمة الرئيسية", callback_data="main_menu")]])
            )

    async def start_bot(self):
        """Start the control bot"""
        if not TELEGRAM_IMPORT_SUCCESS:
            logger.error("Cannot start bot - telegram modules not imported successfully")
            return
            
        try:
            logger.info("Starting Simple Control Bot...")
            
            # Initialize database
            await self.init_database()
            
            # Create application
            app = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            app.add_handler(CommandHandler("start", self.start_command))
            app.add_handler(InlineQueryHandler(self.inline_query_handler))
            app.add_handler(CallbackQueryHandler(self.callback_query_handler))
            
            # Start bot
            await app.initialize()
            await app.start()
            
            # Get bot info
            me = await app.bot.get_me()
            logger.info(f"Simple Control bot started: @{me.username}")
            logger.info("Inline mode enabled - users can type @botname in any chat")
            
            # Run bot
            await app.updater.start_polling()
            
            logger.info("Simple Control bot is now running with inline mode...")
            
            # Keep running
            await asyncio.Event().wait()
            
        except Exception as e:
            logger.error(f"Failed to start simple control bot: {e}")
            raise
        finally:
            if self.db_pool:
                await self.db_pool.close()

async def main():
    """Main function"""
    try:
        if not TELEGRAM_IMPORT_SUCCESS:
            print("❌ Cannot start control bot - telegram import failed")
            print("📱 Use Arabic commands directly with the main bot instead")
            return
            
        bot = SimpleControlBot()
        await bot.start_bot()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    if TELEGRAM_IMPORT_SUCCESS:
        asyncio.run(main())
    else:
        print("❌ Telegram import failed - cannot start control bot")
        print("📱 Use Arabic commands directly with the main bot instead")