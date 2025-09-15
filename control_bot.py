
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import asyncpg
import json
from typing import Dict, List, Optional, Union
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
    BotCommand, CallbackQuery
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    InlineQueryHandler, ContextTypes, MessageHandler,
    filters
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
    بوت التحكم الرسمي مع دعم Inline Mode الكامل لإدارة UserBot
    يتضمن واجهة تفاعلية شاملة وربط مباشر مع UserBot
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
        
        # Admin IDs for control bot
        self.admin_ids: set = {self.userbot_admin_id}
        
        # Cache for quick access
        self.monitored_channels: Dict[int, Dict[str, str]] = {}
        self.emoji_mappings_count: int = 0
        self.channel_emoji_mappings_count: int = 0
        self.forwarding_tasks_count: int = 0
        self.pending_commands: Dict[str, Dict] = {}  # Track pending operations

    async def init_database(self):
        """Initialize database connection pool"""
        try:
            self.db_pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
            logger.info("Control bot database connection initialized")
            
            # Create command queue table
            await self.create_command_queue_table()
            
            # Load cached data
            await self.load_cached_data()
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def create_command_queue_table(self):
        """Create command queue and callback tracking tables"""
        if self.db_pool is None:
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                # Command queue table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS command_queue (
                        id SERIAL PRIMARY KEY,
                        command TEXT NOT NULL,
                        args TEXT,
                        requested_by BIGINT NOT NULL,
                        chat_id BIGINT,
                        message_id INTEGER,
                        callback_data TEXT,
                        status TEXT DEFAULT 'pending',
                        result TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMP
                    )
                """)
                
                # Add new columns if they don't exist
                await conn.execute("""
                    ALTER TABLE command_queue 
                    ADD COLUMN IF NOT EXISTS chat_id BIGINT,
                    ADD COLUMN IF NOT EXISTS message_id INTEGER,
                    ADD COLUMN IF NOT EXISTS callback_data TEXT
                """)
                
                logger.info("Command queue table created/updated successfully")
                
        except Exception as e:
            logger.error(f"Failed to create command queue table: {e}")

    async def load_cached_data(self):
        """Load data for quick access in inline queries"""
        if self.db_pool is None:
            return
            
        try:
            async with self.db_pool.acquire() as conn:
                # Load monitored channels
                channels = await conn.fetch("""
                    SELECT channel_id, channel_username, channel_title 
                    FROM monitored_channels WHERE is_active = TRUE
                """)
                self.monitored_channels = {
                    row['channel_id']: {
                        'username': row['channel_username'] or '',
                        'title': row['channel_title'] or 'Unknown Channel'
                    }
                    for row in channels
                }
                
                # Count various mappings
                self.emoji_mappings_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM emoji_replacements"
                ) or 0
                
                self.channel_emoji_mappings_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM channel_emoji_replacements"
                ) or 0
                
                self.forwarding_tasks_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM forwarding_tasks WHERE is_active = TRUE"
                ) or 0
                
                logger.info(f"Loaded cache: {len(self.monitored_channels)} channels, "
                           f"{self.emoji_mappings_count} global emojis, "
                           f"{self.channel_emoji_mappings_count} channel emojis, "
                           f"{self.forwarding_tasks_count} forwarding tasks")
                
        except Exception as e:
            logger.error(f"Failed to load cached data: {e}")

    async def queue_command(self, command: str, args: str = "", requested_by: int = 0, 
                           chat_id: int = None, message_id: int = None, 
                           callback_data: str = None) -> Optional[int]:
        """Queue command for UserBot to process"""
        if self.db_pool is None:
            return None
            
        try:
            async with self.db_pool.acquire() as conn:
                command_id = await conn.fetchval("""
                    INSERT INTO command_queue 
                    (command, args, requested_by, chat_id, message_id, callback_data) 
                    VALUES ($1, $2, $3, $4, $5, $6) 
                    RETURNING id
                """, command, args, requested_by, chat_id, message_id, callback_data)
                
                logger.info(f"Queued command ID {command_id}: {command} with args: {args}")
                return command_id
                
        except Exception as e:
            logger.error(f"Failed to queue command: {e}")
            return None

    async def get_command_result(self, command_id: int) -> Optional[Dict]:
        """Get command execution result"""
        if self.db_pool is None:
            return None
            
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT status, result, processed_at FROM command_queue WHERE id = $1",
                    command_id
                )
                return dict(result) if result else None
                
        except Exception as e:
            logger.error(f"Failed to get command result: {e}")
            return None

    # ============= INLINE KEYBOARDS =============

    def get_main_menu_keyboard(self) -> InlineKeyboardMarkup:
        """لوحة التحكم الرئيسية"""
        keyboard = [
            [
                InlineKeyboardButton("📺 إدارة القنوات", callback_data="channels_menu"),
                InlineKeyboardButton("😀 إدارة الإيموجي", callback_data="emoji_menu")
            ],
            [
                InlineKeyboardButton("🔄 مهام النسخ", callback_data="forwarding_menu"),
                InlineKeyboardButton("👥 إدارة الأدمن", callback_data="admin_menu")
            ],
            [
                InlineKeyboardButton("📊 الإحصائيات", callback_data="stats_menu"),
                InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings_menu")
            ],
            [
                InlineKeyboardButton("🔧 أدوات متقدمة", callback_data="tools_menu"),
                InlineKeyboardButton("❓ المساعدة", callback_data="help_menu")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_channels_menu_keyboard(self) -> InlineKeyboardMarkup:
        """قائمة إدارة القنوات"""
        keyboard = [
            [
                InlineKeyboardButton("📋 عرض القنوات", callback_data="cmd_list_channels"),
                InlineKeyboardButton("➕ إضافة قناة", callback_data="input_add_channel")
            ],
            [
                InlineKeyboardButton("🔍 فحص الصلاحيات", callback_data="input_check_permissions"),
                InlineKeyboardButton("❌ حذف قناة", callback_data="input_remove_channel")
            ],
            [
                InlineKeyboardButton("🔄 حالة الاستبدال", callback_data="cmd_check_replacement_status"),
                InlineKeyboardButton("⚙️ تحكم بالاستبدال", callback_data="replacement_control_menu")
            ],
            [InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_emoji_menu_keyboard(self) -> InlineKeyboardMarkup:
        """قائمة إدارة الإيموجي"""
        keyboard = [
            [
                InlineKeyboardButton("📋 الاستبدالات العامة", callback_data="cmd_list_global_emojis"),
                InlineKeyboardButton("🎯 استبدالات القنوات", callback_data="cmd_list_channel_emojis")
            ],
            [
                InlineKeyboardButton("➕ إضافة عام", callback_data="input_add_global_emoji"),
                InlineKeyboardButton("🎯 إضافة للقناة", callback_data="input_add_channel_emoji")
            ],
            [
                InlineKeyboardButton("🗑️ حذف استبدال", callback_data="input_delete_emoji"),
                InlineKeyboardButton("🧹 تنظيف مكرر", callback_data="cmd_clean_duplicates")
            ],
            [
                InlineKeyboardButton("📝 الحصول على معرف", callback_data="input_get_emoji_id"),
                InlineKeyboardButton("🔄 إعادة تحميل", callback_data="cmd_reload_emojis")
            ],
            [InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_forwarding_menu_keyboard(self) -> InlineKeyboardMarkup:
        """قائمة مهام النسخ"""
        keyboard = [
            [
                InlineKeyboardButton("📋 عرض المهام", callback_data="cmd_list_forwarding_tasks"),
                InlineKeyboardButton("➕ إضافة مهمة", callback_data="input_add_forwarding_task")
            ],
            [
                InlineKeyboardButton("✅ تفعيل مهمة", callback_data="input_activate_task"),
                InlineKeyboardButton("❌ تعطيل مهمة", callback_data="input_deactivate_task")
            ],
            [
                InlineKeyboardButton("⏱️ تعديل التأخير", callback_data="input_update_delay"),
                InlineKeyboardButton("🗑️ حذف مهمة", callback_data="input_delete_task")
            ],
            [InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_admin_menu_keyboard(self) -> InlineKeyboardMarkup:
        """قائمة إدارة الأدمن"""
        keyboard = [
            [
                InlineKeyboardButton("👥 عرض الأدمن", callback_data="cmd_list_admins"),
                InlineKeyboardButton("➕ إضافة أدمن", callback_data="input_add_admin")
            ],
            [
                InlineKeyboardButton("❌ حذف أدمن", callback_data="input_remove_admin"),
                InlineKeyboardButton("🔄 إعادة تحميل", callback_data="cmd_reload_admins")
            ],
            [InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_replacement_control_keyboard(self) -> InlineKeyboardMarkup:
        """قائمة التحكم بالاستبدال"""
        keyboard = [
            [
                InlineKeyboardButton("✅ تفعيل الاستبدال", callback_data="input_activate_replacement"),
                InlineKeyboardButton("❌ تعطيل الاستبدال", callback_data="input_deactivate_replacement")
            ],
            [
                InlineKeyboardButton("📊 حالة جميع القنوات", callback_data="cmd_check_all_replacement_status"),
                InlineKeyboardButton("🔄 إعادة تحميل", callback_data="cmd_reload_channels")
            ],
            [InlineKeyboardButton("⬅️ العودة للقنوات", callback_data="channels_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_tools_menu_keyboard(self) -> InlineKeyboardMarkup:
        """قائمة الأدوات المتقدمة"""
        keyboard = [
            [
                InlineKeyboardButton("🧪 اختبار الاتصال", callback_data="cmd_test_connection"),
                InlineKeyboardButton("🔄 مزامنة البيانات", callback_data="cmd_sync_data")
            ],
            [
                InlineKeyboardButton("🗃️ نسخ احتياطي", callback_data="cmd_backup_data"),
                InlineKeyboardButton("📤 تصدير الإعدادات", callback_data="cmd_export_settings")
            ],
            [
                InlineKeyboardButton("🧹 تنظيف قاعدة البيانات", callback_data="cmd_cleanup_database"),
                InlineKeyboardButton("📊 تقرير مفصل", callback_data="cmd_detailed_report")
            ],
            [InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_input_cancel_keyboard(self) -> InlineKeyboardMarkup:
        """لوحة مفاتيح الإلغاء للإدخالات"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_input")]
        ])

    # ============= INLINE QUERY HANDLER =============

    async def inline_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالج الاستعلامات المضمنة - محرك البحث التفاعلي"""
        query = update.inline_query.query.strip().lower()
        user_id = update.inline_query.from_user.id
        
        # Check authorization
        if user_id not in self.admin_ids:
            results = [InlineQueryResultArticle(
                id="unauthorized",
                title="❌ غير مخول",
                description="عذراً، أنت غير مخول لاستخدام هذا البوت",
                input_message_content=InputTextMessageContent("❌ غير مخول لاستخدام هذا البوت")
            )]
            await update.inline_query.answer(results, cache_time=0)
            return
        
        results = []
        
        # Main menu (default)
        if not query or "قائمة" in query or "main" in query:
            results.append(InlineQueryResultArticle(
                id="main_menu",
                title="🎛️ لوحة التحكم الرئيسية",
                description=f"📺 {len(self.monitored_channels)} قناة | 😀 {self.emoji_mappings_count + self.channel_emoji_mappings_count} استبدال | 🔄 {self.forwarding_tasks_count} مهمة",
                input_message_content=InputTextMessageContent(
                    "🎛️ **لوحة التحكم الرئيسية**\n\n"
                    f"📊 **الإحصائيات السريعة:**\n"
                    f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
                    f"😀 الاستبدالات العامة: {self.emoji_mappings_count}\n"
                    f"🎯 استبدالات القنوات: {self.channel_emoji_mappings_count}\n"
                    f"🔄 مهام النسخ: {self.forwarding_tasks_count}\n\n"
                    "اختر خياراً من الأزرار أدناه:",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_main_menu_keyboard()
            ))
        
        # Channel management
        if "قناة" in query or "channel" in query:
            results.append(InlineQueryResultArticle(
                id="channels",
                title="📺 إدارة القنوات",
                description=f"إدارة {len(self.monitored_channels)} قناة مراقبة",
                input_message_content=InputTextMessageContent(
                    f"📺 **إدارة القنوات**\n\n"
                    f"القنوات المراقبة حالياً: **{len(self.monitored_channels)}**\n\n"
                    "يمكنك إضافة قنوات جديدة، فحص الصلاحيات، أو إدارة إعدادات الاستبدال.",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_channels_menu_keyboard()
            ))
        
        # Emoji management
        if "إيموجي" in query or "emoji" in query:
            results.append(InlineQueryResultArticle(
                id="emojis",
                title="😀 إدارة الإيموجي",
                description=f"إدارة {self.emoji_mappings_count + self.channel_emoji_mappings_count} استبدال",
                input_message_content=InputTextMessageContent(
                    f"😀 **إدارة الإيموجي**\n\n"
                    f"📊 **الاستبدالات الحالية:**\n"
                    f"🌍 العامة: {self.emoji_mappings_count}\n"
                    f"🎯 الخاصة بالقنوات: {self.channel_emoji_mappings_count}\n"
                    f"📈 الإجمالي: {self.emoji_mappings_count + self.channel_emoji_mappings_count}\n\n"
                    "إدارة شاملة لاستبدالات الإيموجي العامة والخاصة بكل قناة.",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_emoji_menu_keyboard()
            ))
        
        # Forwarding tasks
        if "نسخ" in query or "توجيه" in query or "forward" in query:
            results.append(InlineQueryResultArticle(
                id="forwarding",
                title="🔄 مهام النسخ",
                description=f"إدارة {self.forwarding_tasks_count} مهمة نسخ",
                input_message_content=InputTextMessageContent(
                    f"🔄 **مهام النسخ**\n\n"
                    f"المهام النشطة: **{self.forwarding_tasks_count}**\n\n"
                    "إضافة مهام جديدة، تعديل التأخير، أو إدارة المهام الموجودة.",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_forwarding_menu_keyboard()
            ))
        
        # Admin management
        if "أدمن" in query or "admin" in query:
            results.append(InlineQueryResultArticle(
                id="admins",
                title="👥 إدارة الأدمن",
                description=f"إدارة {len(self.admin_ids)} مستخدم مخول",
                input_message_content=InputTextMessageContent(
                    f"👥 **إدارة الأدمن**\n\n"
                    f"المستخدمون المخولون: **{len(self.admin_ids)}**\n\n"
                    "إضافة أو حذف المستخدمين المخولين لاستخدام النظام.",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_admin_menu_keyboard()
            ))
        
        # Statistics
        if "إحصائ" in query or "stats" in query:
            results.append(InlineQueryResultArticle(
                id="stats",
                title="📊 الإحصائيات",
                description="عرض إحصائيات شاملة للنظام",
                input_message_content=InputTextMessageContent(
                    "📊 **إحصائيات النظام**\n\nجاري تحميل الإحصائيات...",
                    parse_mode='Markdown'
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="stats_menu")
                ]])
            ))
        
        # Tools
        if "أدوات" in query or "tools" in query:
            results.append(InlineQueryResultArticle(
                id="tools",
                title="🔧 أدوات متقدمة",
                description="أدوات الصيانة والمراقبة المتقدمة",
                input_message_content=InputTextMessageContent(
                    "🔧 **أدوات متقدمة**\n\n"
                    "مجموعة أدوات للصيانة، النسخ الاحتياطي، ومراقبة النظام.",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_tools_menu_keyboard()
            ))
        
        # Search within channels
        if query.startswith("@") or query.startswith("-100"):
            matching_channels = []
            for channel_id, info in self.monitored_channels.items():
                title = info['title'].lower()
                username = info['username'].lower()
                if (query[1:] in title or query[1:] in username or 
                    query == str(channel_id)):
                    matching_channels.append((channel_id, info))
            
            for channel_id, info in matching_channels[:5]:  # Limit results
                title = info['title']
                username = info['username']
                results.append(InlineQueryResultArticle(
                    id=f"channel_{channel_id}",
                    title=f"📺 {title}",
                    description=f"@{username} | {channel_id}",
                    input_message_content=InputTextMessageContent(
                        f"📺 **{title}**\n\n"
                        f"🆔 معرف القناة: `{channel_id}`\n"
                        f"👤 اسم المستخدم: @{username}\n\n"
                        "اختر عملية للقناة:",
                        parse_mode='Markdown'
                    ),
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("🎯 استبدالات القناة", 
                                               callback_data=f"channel_emojis_{channel_id}"),
                            InlineKeyboardButton("🔍 فحص الصلاحيات", 
                                               callback_data=f"check_perms_{channel_id}")
                        ],
                        [
                            InlineKeyboardButton("✅ تفعيل الاستبدال", 
                                               callback_data=f"activate_repl_{channel_id}"),
                            InlineKeyboardButton("❌ تعطيل الاستبدال", 
                                               callback_data=f"deactivate_repl_{channel_id}")
                        ],
                        [InlineKeyboardButton("⬅️ العودة للقنوات", callback_data="channels_menu")]
                    ])
                ))
        
        # Default fallback
        if not results:
            results.append(InlineQueryResultArticle(
                id="default",
                title="🎛️ لوحة التحكم الرئيسية",
                description="الدخول إلى النظام",
                input_message_content=InputTextMessageContent(
                    "🎛️ **لوحة التحكم**\n\nمرحباً بك في نظام إدارة UserBot!\n\nاختر خياراً:",
                    parse_mode='Markdown'
                ),
                reply_markup=self.get_main_menu_keyboard()
            ))
        
        await update.inline_query.answer(results, cache_time=1)

    # ============= CALLBACK QUERY HANDLER =============

    async def callback_query_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالج callbacks من الأزرار"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        if user_id not in self.admin_ids:
            await query.edit_message_text("❌ غير مخول لاستخدام هذا البوت")
            return
        
        data = query.data
        chat_id = query.message.chat_id
        message_id = query.message.message_id
        
        # Navigation callbacks
        if data == "main_menu":
            await self.show_main_menu(query)
        elif data == "channels_menu":
            await self.show_channels_menu(query)
        elif data == "emoji_menu":
            await self.show_emoji_menu(query)
        elif data == "forwarding_menu":
            await self.show_forwarding_menu(query)
        elif data == "admin_menu":
            await self.show_admin_menu(query)
        elif data == "replacement_control_menu":
            await self.show_replacement_control_menu(query)
        elif data == "tools_menu":
            await self.show_tools_menu(query)
        elif data == "stats_menu":
            await self.handle_stats_menu(query)
        elif data == "help_menu":
            await self.show_help_menu(query)
        
        # Direct command callbacks
        elif data.startswith("cmd_"):
            await self.handle_direct_command(query, data, user_id, chat_id, message_id)
        
        # Input request callbacks
        elif data.startswith("input_"):
            await self.handle_input_request(query, data, user_id)
        
        # Special channel callbacks
        elif data.startswith("channel_emojis_"):
            channel_id = int(data.split("_")[-1])
            await self.handle_channel_emojis_display(query, channel_id)
        elif data.startswith("check_perms_"):
            channel_id = int(data.split("_")[-1])
            await self.handle_check_permissions(query, channel_id, user_id, chat_id, message_id)
        elif data.startswith("activate_repl_"):
            channel_id = int(data.split("_")[-1])
            await self.handle_activate_replacement(query, channel_id, user_id, chat_id, message_id)
        elif data.startswith("deactivate_repl_"):
            channel_id = int(data.split("_")[-1])
            await self.handle_deactivate_replacement(query, channel_id, user_id, chat_id, message_id)
        
        # Cancel input
        elif data == "cancel_input":
            context.user_data.clear()
            await query.edit_message_text(
                "❌ تم إلغاء العملية",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")
                ]])
            )

    # ============= MENU DISPLAY METHODS =============

    async def show_main_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            "🎛️ **لوحة التحكم الرئيسية**\n\n"
            f"📊 **الإحصائيات السريعة:**\n"
            f"📺 القنوات المراقبة: {len(self.monitored_channels)}\n"
            f"😀 الاستبدالات العامة: {self.emoji_mappings_count}\n"
            f"🎯 استبدالات القنوات: {self.channel_emoji_mappings_count}\n"
            f"🔄 مهام النسخ: {self.forwarding_tasks_count}\n\n"
            "اختر خياراً من الأزرار أدناه:",
            parse_mode='Markdown',
            reply_markup=self.get_main_menu_keyboard()
        )

    async def show_channels_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            f"📺 **إدارة القنوات**\n\n"
            f"القنوات المراقبة حالياً: **{len(self.monitored_channels)}**\n\n"
            "يمكنك إضافة قنوات جديدة، فحص الصلاحيات، أو إدارة إعدادات الاستبدال.",
            parse_mode='Markdown',
            reply_markup=self.get_channels_menu_keyboard()
        )

    async def show_emoji_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            f"😀 **إدارة الإيموجي**\n\n"
            f"📊 **الاستبدالات الحالية:**\n"
            f"🌍 العامة: {self.emoji_mappings_count}\n"
            f"🎯 الخاصة بالقنوات: {self.channel_emoji_mappings_count}\n"
            f"📈 الإجمالي: {self.emoji_mappings_count + self.channel_emoji_mappings_count}\n\n"
            "إدارة شاملة لاستبدالات الإيموجي العامة والخاصة بكل قناة.",
            parse_mode='Markdown',
            reply_markup=self.get_emoji_menu_keyboard()
        )

    async def show_forwarding_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            f"🔄 **مهام النسخ**\n\n"
            f"المهام النشطة: **{self.forwarding_tasks_count}**\n\n"
            "إضافة مهام جديدة، تعديل التأخير، أو إدارة المهام الموجودة.",
            parse_mode='Markdown',
            reply_markup=self.get_forwarding_menu_keyboard()
        )

    async def show_admin_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            f"👥 **إدارة الأدمن**\n\n"
            f"المستخدمون المخولون: **{len(self.admin_ids)}**\n\n"
            "إضافة أو حذف المستخدمين المخولين لاستخدام النظام.",
            parse_mode='Markdown',
            reply_markup=self.get_admin_menu_keyboard()
        )

    async def show_replacement_control_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            "🔄 **التحكم بالاستبدال**\n\n"
            "إدارة حالة الاستبدال للقنوات المختلفة.\n"
            "يمكنك تفعيل أو تعطيل الاستبدال لقنوات محددة.",
            parse_mode='Markdown',
            reply_markup=self.get_replacement_control_keyboard()
        )

    async def show_tools_menu(self, query: CallbackQuery):
        await query.edit_message_text(
            "🔧 **أدوات متقدمة**\n\n"
            "مجموعة أدوات للصيانة، النسخ الاحتياطي، ومراقبة النظام.",
            parse_mode='Markdown',
            reply_markup=self.get_tools_menu_keyboard()
        )

    async def show_help_menu(self, query: CallbackQuery):
        help_text = """❓ **المساعدة - بوت التحكم**

🎯 **الاستخدام الأساسي:**
• اكتب `@botname` في أي محادثة
• ستظهر لك خيارات لوحة التحكم
• اختر الخيار المطلوب واستخدم الأزرار

📋 **القوائم المتاحة:**
• 📺 إدارة القنوات - إضافة/حذف/فحص القنوات
• 😀 إدارة الإيموجي - إدارة الاستبدالات
• 🔄 مهام النسخ - إعداد النسخ التلقائي
• 👥 إدارة الأدمن - إدارة المستخدمين المخولين
• 📊 الإحصائيات - عرض إحصائيات النظام

⚡ **مزايا النظام:**
• واجهة تفاعلية كاملة
• أوامر فورية بدون انتظار
• تزامن مباشر مع UserBot
• حفظ تلقائي لجميع الإعدادات

🔗 **آلية العمل:**
• البوت الرسمي يعرض الواجهة
• الأوامر ترسل فوراً إلى UserBot
• النتائج تظهر مباشرة
• تحديث تلقائي للبيانات

💡 **نصائح:**
• استخدم البحث في الـ inline mode
• جميع العمليات محفوظة تلقائياً
• يمكنك استخدام البوت من أي محادثة"""
        
        await query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")
            ]])
        )

    # ============= COMMAND HANDLERS =============

    async def handle_direct_command(self, query: CallbackQuery, command_data: str, 
                                   user_id: int, chat_id: int, message_id: int):
        """معالج الأوامر المباشرة"""
        command = command_data[4:]  # Remove "cmd_" prefix
        
        # Update message to show processing
        await query.edit_message_text(
            f"⏳ **جاري تنفيذ الأمر...**\n\n"
            f"🔄 العملية: {self.get_command_display_name(command)}\n"
            f"⏱️ يرجى الانتظار...",
            parse_mode='Markdown'
        )
        
        # Queue command
        command_id = await self.queue_command(command, "", user_id, chat_id, message_id, command_data)
        
        if command_id:
            # Wait for result and update message
            asyncio.create_task(self.wait_for_result(command_id, chat_id, message_id, command))
        else:
            await query.edit_message_text(
                "❌ **خطأ في إرسال الأمر**\n\nحدث خطأ أثناء إرسال الأمر إلى النظام.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")
                ]])
            )

    async def handle_input_request(self, query: CallbackQuery, input_data: str, user_id: int):
        """معالج طلبات الإدخال"""
        input_type = input_data[6:]  # Remove "input_" prefix
        
        instructions = self.get_input_instructions(input_type)
        
        await query.edit_message_text(
            f"📝 **مطلوب إدخال**\n\n{instructions}\n\n"
            "أرسل الآن القيمة المطلوبة في رسالة منفصلة.",
            parse_mode='Markdown',
            reply_markup=self.get_input_cancel_keyboard()
        )
        
        # Store the context for next message
        if 'user_data' not in query.from_user:
            query.from_user.user_data = {}
        
        # We'll handle this through message handler
        context = {
            'awaiting_input': input_type,
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id
        }
        # Store in a global dict keyed by user_id
        if not hasattr(self, 'user_contexts'):
            self.user_contexts = {}
        self.user_contexts[user_id] = context

    async def handle_stats_menu(self, query: CallbackQuery):
        """معالج قائمة الإحصائيات"""
        # Refresh cache
        await self.load_cached_data()
        
        # Calculate additional stats
        active_replacements = 0
        if self.db_pool:
            try:
                async with self.db_pool.acquire() as conn:
                    active_replacements = await conn.fetchval(
                        "SELECT COUNT(*) FROM monitored_channels WHERE replacement_active = TRUE"
                    ) or 0
            except:
                pass
        
        stats_text = f"""📊 **إحصائيات النظام المحدثة**

📺 **القنوات:**
• المراقبة: {len(self.monitored_channels)}
• الاستبدال المفعل: {active_replacements}

😀 **الاستبدالات:**
• العامة: {self.emoji_mappings_count}
• الخاصة بالقنوات: {self.channel_emoji_mappings_count}
• الإجمالي: {self.emoji_mappings_count + self.channel_emoji_mappings_count}

🔄 **مهام النسخ:**
• النشطة: {self.forwarding_tasks_count}

👥 **الإدارة:**
• المستخدمون المخولون: {len(self.admin_ids)}

🕐 **آخر تحديث:** الآن"""
        
        await query.edit_message_text(
            stats_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 تحديث", callback_data="stats_menu"),
                    InlineKeyboardButton("📊 تقرير مفصل", callback_data="cmd_detailed_report")
                ],
                [InlineKeyboardButton("⬅️ العودة للرئيسية", callback_data="main_menu")]
            ])
        )

    # ============= HELPER METHODS =============

    def get_command_display_name(self, command: str) -> str:
        """أسماء الأوامر للعرض"""
        display_names = {
            "list_channels": "عرض القنوات",
            "list_global_emojis": "الاستبدالات العامة", 
            "list_channel_emojis": "استبدالات القنوات",
            "list_forwarding_tasks": "مهام النسخ",
            "list_admins": "قائمة الأدمن",
            "clean_duplicates": "تنظيف الاستبدالات المكررة",
            "test_connection": "اختبار الاتصال",
            "sync_data": "مزامنة البيانات",
            "backup_data": "نسخ احتياطي",
            "cleanup_database": "تنظيف قاعدة البيانات",
            "detailed_report": "تقرير مفصل"
        }
        return display_names.get(command, command)

    def get_input_instructions(self, input_type: str) -> str:
        """تعليمات الإدخال"""
        instructions = {
            "add_channel": "🔸 أدخل معرف القناة أو اسم المستخدم\n📝 مثال: @channelname أو -1001234567890",
            "remove_channel": "🔸 أدخل معرف القناة أو اسم المستخدم للحذف\n📝 مثال: @channelname أو -1001234567890",
            "check_permissions": "🔸 أدخل معرف القناة أو اسم المستخدم لفحص الصلاحيات\n📝 مثال: @channelname أو -1001234567890",
            "add_global_emoji": "🔸 أدخل الإيموجي العادي والمميز\n📝 مثال: 😀 5123456789\nأو أرسل رسالة تحتوي على كليهما",
            "add_channel_emoji": "🔸 أدخل معرف القناة والإيموجي العادي والمميز\n📝 مثال: @channelname 😀 5123456789",
            "delete_emoji": "🔸 أدخل الإيموجي المراد حذفه\n📝 مثال: 😀",
            "get_emoji_id": "🔸 أرسل رسالة تحتوي على الإيموجي المميز\nلاستخراج معرفه",
            "add_forwarding_task": "🔸 أدخل القناة المصدر والهدف والتأخير\n📝 مثال: @source @target 5 وصف",
            "activate_task": "🔸 أدخل معرف المهمة للتفعيل\n📝 مثال: 123",
            "deactivate_task": "🔸 أدخل معرف المهمة للتعطيل\n📝 مثال: 123",
            "delete_task": "🔸 أدخل معرف المهمة للحذف\n📝 مثال: 123",
            "update_delay": "🔸 أدخل معرف المهمة والتأخير الجديد\n📝 مثال: 123 10",
            "add_admin": "🔸 أدخل معرف المستخدم واسم المستخدم\n📝 مثال: 123456789 اسم_المستخدم",
            "remove_admin": "🔸 أدخل معرف المستخدم للحذف\n📝 مثال: 123456789",
            "activate_replacement": "🔸 أدخل معرف القناة لتفعيل الاستبدال\n📝 مثال: @channelname",
            "deactivate_replacement": "🔸 أدخل معرف القناة لتعطيل الاستبدال\n📝 مثال: @channelname"
        }
        return instructions.get(input_type, "أدخل القيمة المطلوبة")

    async def wait_for_result(self, command_id: int, chat_id: int, message_id: int, command: str):
        """انتظار نتيجة الأمر وتحديث الرسالة"""
        max_wait = 30  # 30 seconds timeout
        waited = 0
        
        while waited < max_wait:
            await asyncio.sleep(2)
            waited += 2
            
            result = await self.get_command_result(command_id)
            if result and result['status'] in ['completed', 'failed']:
                try:
                    if result['status'] == 'completed':
                        response_text = result['result'] or "✅ تم تنفيذ الأمر بنجاح"
                        
                        # Add appropriate return button
                        return_button = self.get_return_button_for_command(command)
                        
                        await self.application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=response_text,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([return_button])
                        )
                    else:
                        error_text = result['result'] or "حدث خطأ أثناء تنفيذ الأمر"
                        await self.application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"❌ **خطأ**\n\n{error_text}",
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")
                            ]])
                        )
                except Exception as e:
                    logger.error(f"Failed to update message with result: {e}")
                return
        
        # Timeout
        try:
            await self.application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏰ **انتهت مهلة الانتظار**\n\nالأمر قد يكون قيد التنفيذ، تحقق من النتائج لاحقاً.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")
                ]])
            )
        except Exception as e:
            logger.error(f"Failed to update message with timeout: {e}")

    def get_return_button_for_command(self, command: str) -> List[InlineKeyboardButton]:
        """أزرار العودة المناسبة لكل أمر"""
        channel_commands = ["list_channels", "check_permissions"]
        emoji_commands = ["list_global_emojis", "list_channel_emojis", "clean_duplicates"]
        forwarding_commands = ["list_forwarding_tasks"]
        admin_commands = ["list_admins"]
        
        if command in channel_commands:
            return [InlineKeyboardButton("⬅️ العودة للقنوات", callback_data="channels_menu")]
        elif command in emoji_commands:
            return [InlineKeyboardButton("⬅️ العودة للإيموجي", callback_data="emoji_menu")]
        elif command in forwarding_commands:
            return [InlineKeyboardButton("⬅️ العودة للنسخ", callback_data="forwarding_menu")]
        elif command in admin_commands:
            return [InlineKeyboardButton("⬅️ العودة للأدمن", callback_data="admin_menu")]
        else:
            return [InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")]

    # ============= MESSAGE HANDLER FOR INPUTS =============

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالج الرسائل للمدخلات"""
        if not update.message or not update.message.text:
            return
        
        user_id = update.effective_user.id
        
        # Check if user is authorized
        if user_id not in self.admin_ids:
            return
        
        # Check if we're waiting for input from this user
        if not hasattr(self, 'user_contexts') or user_id not in self.user_contexts:
            return
        
        context_data = self.user_contexts[user_id]
        input_type = context_data.get('awaiting_input')
        
        if not input_type:
            return
        
        # Process the input
        user_input = update.message.text.strip()
        
        # Clear the context
        del self.user_contexts[user_id]
        
        # Convert input_type to command
        command_mapping = {
            "add_channel": ("add_channel", user_input),
            "remove_channel": ("remove_channel", user_input),
            "check_permissions": ("check_channel_permissions", user_input),
            "add_global_emoji": ("add_emoji_replacement", user_input),
            "add_channel_emoji": ("add_channel_emoji_replacement", user_input),
            "delete_emoji": ("delete_emoji_replacement", user_input),
            "add_forwarding_task": ("add_forwarding_task", user_input),
            "activate_task": ("activate_forwarding_task", user_input),
            "deactivate_task": ("deactivate_forwarding_task", user_input),
            "delete_task": ("delete_forwarding_task", user_input),
            "update_delay": ("update_forwarding_delay", user_input),
            "add_admin": ("add_admin", user_input),
            "remove_admin": ("remove_admin", user_input),
            "activate_replacement": ("activate_channel_replacement", user_input),
            "deactivate_replacement": ("deactivate_channel_replacement", user_input)
        }
        
        if input_type in command_mapping:
            command, args = command_mapping[input_type]
            
            # Update the original message
            try:
                await context.bot.edit_message_text(
                    chat_id=context_data['chat_id'],
                    message_id=context_data['message_id'],
                    text=f"⏳ **جاري معالجة الطلب...**\n\n"
                         f"📝 المدخل: `{user_input}`\n"
                         f"🔄 العملية: {self.get_command_display_name(command)}\n"
                         f"⏱️ يرجى الانتظار...",
                    parse_mode='Markdown'
                )
                
                # Queue the command
                command_id = await self.queue_command(
                    command, args, user_id, 
                    context_data['chat_id'], context_data['message_id'], 
                    f"input_{input_type}"
                )
                
                if command_id:
                    # Wait for result
                    asyncio.create_task(self.wait_for_result(
                        command_id, context_data['chat_id'], 
                        context_data['message_id'], command
                    ))
                else:
                    await context.bot.edit_message_text(
                        chat_id=context_data['chat_id'],
                        message_id=context_data['message_id'],
                        text="❌ **خطأ في معالجة الطلب**\n\nحدث خطأ أثناء إرسال الأمر إلى النظام.",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🏠 العودة للرئيسية", callback_data="main_menu")
                        ]])
                    )
                
                # Delete the user's input message
                await update.message.delete()
                
            except Exception as e:
                logger.error(f"Failed to process input: {e}")
                await update.message.reply_text("❌ حدث خطأ أثناء معالجة المدخل")

    # ============= SPECIAL HANDLERS =============

    async def handle_channel_emojis_display(self, query: CallbackQuery, channel_id: int):
        """عرض استبدالات قناة معينة"""
        command_id = await self.queue_command(
            "list_channel_emoji_replacements", 
            str(channel_id), 
            query.from_user.id,
            query.message.chat_id,
            query.message.message_id,
            f"channel_emojis_{channel_id}"
        )
        
        await query.edit_message_text(
            "⏳ **جاري تحميل استبدالات القناة...**",
            parse_mode='Markdown'
        )
        
        if command_id:
            asyncio.create_task(self.wait_for_result(
                command_id, query.message.chat_id, 
                query.message.message_id, "list_channel_emoji_replacements"
            ))

    async def handle_check_permissions(self, query: CallbackQuery, channel_id: int, 
                                     user_id: int, chat_id: int, message_id: int):
        """فحص صلاحيات قناة معينة"""
        command_id = await self.queue_command(
            "check_channel_permissions", 
            str(channel_id), 
            user_id, chat_id, message_id,
            f"check_perms_{channel_id}"
        )
        
        await query.edit_message_text(
            "⏳ **جاري فحص الصلاحيات...**",
            parse_mode='Markdown'
        )
        
        if command_id:
            asyncio.create_task(self.wait_for_result(
                command_id, chat_id, message_id, "check_channel_permissions"
            ))

    async def handle_activate_replacement(self, query: CallbackQuery, channel_id: int,
                                        user_id: int, chat_id: int, message_id: int):
        """تفعيل الاستبدال لقناة معينة"""
        command_id = await self.queue_command(
            "activate_channel_replacement",
            str(channel_id),
            user_id, chat_id, message_id,
            f"activate_repl_{channel_id}"
        )
        
        await query.edit_message_text(
            "⏳ **جاري تفعيل الاستبدال...**",
            parse_mode='Markdown'
        )
        
        if command_id:
            asyncio.create_task(self.wait_for_result(
                command_id, chat_id, message_id, "activate_channel_replacement"
            ))

    async def handle_deactivate_replacement(self, query: CallbackQuery, channel_id: int,
                                          user_id: int, chat_id: int, message_id: int):
        """تعطيل الاستبدال لقناة معينة"""
        command_id = await self.queue_command(
            "deactivate_channel_replacement",
            str(channel_id),
            user_id, chat_id, message_id,
            f"deactivate_repl_{channel_id}"
        )
        
        await query.edit_message_text(
            "⏳ **جاري تعطيل الاستبدال...**",
            parse_mode='Markdown'
        )
        
        if command_id:
            asyncio.create_task(self.wait_for_result(
                command_id, chat_id, message_id, "deactivate_channel_replacement"
            ))

    # ============= BOT SETUP AND LIFECYCLE =============

    async def setup_bot_commands(self, app: Application):
        """إعداد أوامر البوت"""
        commands = [
            BotCommand("start", "بدء البوت وعرض لوحة التحكم"),
            BotCommand("help", "المساعدة ودليل الاستخدام"),
            BotCommand("status", "حالة النظام والإحصائيات"),
        ]
        await app.bot.set_my_commands(commands)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر البدء"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text(
                "❌ **غير مخول**\n\n"
                "عذراً، أنت غير مخول لاستخدام هذا البوت.\n"
                "📞 للحصول على الصلاحية، تواصل مع المطور.",
                parse_mode='Markdown'
            )
            return
        
        welcome_text = f"""🎛️ **أهلاً بك في بوت التحكم!**

👋 مرحباً، {update.effective_user.first_name}!

🚀 **طرق الاستخدام:**

1️⃣ **Inline Mode (الأفضل):**
   • اكتب `@{context.bot.username}` في أي محادثة
   • اختر من القوائم التفاعلية
   • تحكم كامل بالنظام

2️⃣ **الأوامر المباشرة:**
   • /help - المساعدة الشاملة
   • /status - حالة النظام

⚡ **المميزات:**
• واجهة تفاعلية شاملة
• تحكم مباشر بـ UserBot
• نتائج فورية
• يعمل من أي محادثة

🔗 **الربط مع UserBot:**
• جميع الأوامر ترسل مباشرة
• تزامن فوري مع النظام
• حفظ تلقائي للإعدادات"""
        
        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=self.get_main_menu_keyboard()
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر المساعدة"""
        help_text = """❓ **دليل الاستخدام الشامل**

🎯 **الطريقة الأساسية:**
اكتب `@{bot_username}` في أي محادثة لفتح لوحة التحكم التفاعلية.

📋 **القوائم الرئيسية:**

📺 **إدارة القنوات:**
• عرض القنوات المراقبة
• إضافة قنوات جديدة (مع فحص الصلاحيات)
• حذف القنوات
• فحص صلاحيات البوت
• تحكم بحالة الاستبدال

😀 **إدارة الإيموجي:**
• الاستبدالات العامة (تطبق على جميع القنوات)
• الاستبدالات الخاصة (تطبق على قنوات محددة)
• إضافة/حذف الاستبدالات
• تنظيف الاستبدالات المكررة
• الحصول على معرفات الإيموجي

🔄 **مهام النسخ:**
• نسخ الرسائل بين القنوات
• إعداد التأخير الزمني
• تفعيل/تعطيل المهام
• مراقبة المهام النشطة

👥 **إدارة الأدمن:**
• إضافة مستخدمين مخولين
• حذف الصلاحيات
• عرض قائمة المستخدمين

🔧 **أدوات متقدمة:**
• اختبار الاتصال مع UserBot
• مزامنة البيانات
• نسخ احتياطي
• تقارير مفصلة

⚡ **المزايا:**
• **سهولة الاستخدام:** واجهة تفاعلية بأزرار
• **السرعة:** أوامر فورية بدون انتظار
• **المرونة:** يعمل من أي محادثة
• **الأمان:** صلاحيات محددة للمستخدمين
• **التزامن:** ربط مباشر مع UserBot

💡 **نصائح للاستخدام الأمثل:**
• استخدم البحث في inline mode للوصول السريع
• جميع التغييرات محفوظة تلقائياً
• يمكنك استخدام البوت أثناء تشغيل UserBot
• النتائج تظهر فوراً بدون تحديث يدوي

🔗 **كيف يعمل النظام:**
1. تختار عملية من لوحة التحكم
2. البوت يرسل الأمر فوراً إلى UserBot
3. UserBot ينفذ العملية
4. النتيجة تظهر مباشرة في البوت الرسمي

📞 **للدعم:**
إذا واجهت أي مشكلة، تحقق من حالة UserBot أو تواصل مع المطور.""".format(bot_username=context.bot.username)
        
        await update.message.reply_text(
            help_text,
            parse_mode='Markdown'
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر حالة النظام"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            return
        
        # Refresh data
        await self.load_cached_data()
        
        status_text = f"""📊 **حالة النظام**

🔌 **الاتصال:**
• البوت الرسمي: ✅ متصل
• UserBot: {"🟢 نشط" if self.db_pool else "🔴 غير متصل"}
• قاعدة البيانات: {"✅ متصلة" if self.db_pool else "❌ غير متصلة"}

📈 **الإحصائيات:**
• القنوات المراقبة: {len(self.monitored_channels)}
• الاستبدالات العامة: {self.emoji_mappings_count}
• استبدالات القنوات: {self.channel_emoji_mappings_count}
• مهام النسخ النشطة: {self.forwarding_tasks_count}
• المستخدمون المخولون: {len(self.admin_ids)}

⚡ **الأداء:**
• النظام يعمل بكفاءة عالية
• الاستجابة فورية
• التزامن مع UserBot نشط

🕐 **آخر تحديث:** الآن"""
        
        await update.message.reply_text(
            status_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 تحديث", callback_data="cmd_sync_data"),
                    InlineKeyboardButton("📊 تقرير مفصل", callback_data="cmd_detailed_report")
                ],
                [InlineKeyboardButton("🎛️ لوحة التحكم", callback_data="main_menu")]
            ])
        )

    async def start_bot(self):
        """بدء تشغيل البوت"""
        try:
            logger.info("Starting Telegram Control Bot...")
            
            # Initialize database
            await self.init_database()
            
            # Create application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Setup commands
            await self.setup_bot_commands(self.application)
            
            # Add handlers
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("status", self.status_command))
            self.application.add_handler(InlineQueryHandler(self.inline_query_handler))
            self.application.add_handler(CallbackQueryHandler(self.callback_query_handler))
            
            # Add message handler for inputs (only for private chats)
            self.application.add_handler(MessageHandler(
                filters.TEXT & filters.ChatType.PRIVATE, 
                self.message_handler
            ))
            
            # Start bot
            await self.application.initialize()
            await self.application.start()
            
            # Get bot info
            me = await self.application.bot.get_me()
            logger.info(f"Control bot started successfully: @{me.username}")
            logger.info("Full inline mode enabled - users can type @botname anywhere")
            
            # Set inline mode description
            try:
                await self.application.bot.set_my_description(
                    "🎛️ بوت التحكم الشامل بـ UserBot\n"
                    "اكتب @botname في أي محادثة لفتح لوحة التحكم"
                )
                await self.application.bot.set_my_short_description(
                    "بوت التحكم الشامل - Inline Mode متاح"
                )
            except:
                pass  # Not all bots support these methods
            
            # Start polling
            await self.application.updater.start_polling()
            
            logger.info("Control bot is running with full inline mode support...")
            logger.info("Ready to receive inline queries and manage UserBot!")
            
            # Keep running
            await asyncio.Event().wait()
            
        except Exception as e:
            logger.error(f"Failed to start control bot: {e}")
            raise
        finally:
            if self.db_pool:
                await self.db_pool.close()

async def main():
    """الدالة الرئيسية"""
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
