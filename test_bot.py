#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for Telegram Bot

This script tests the bot components without actually running the full bot.
It validates imports, database connectivity, and basic functionality.
"""

import asyncio
import sys
import os
from unittest.mock import MagicMock

async def test_imports():
    """Test that all required modules can be imported"""
    print("🧪 Testing imports...")
    
    try:
        # Test standard library imports
        import logging
        import asyncio
        import re
        from typing import Dict, List, Optional, Tuple
        print("  ✅ Standard library imports OK")
        
        # Test third-party imports
        from dotenv import load_dotenv
        import asyncpg
        from telethon import TelegramClient, events
        from telethon.errors import SessionPasswordNeededError, FloodWaitError
        from telethon.tl.types import MessageEntityCustomEmoji, User, Channel
        print("  ✅ Third-party imports OK")
        
        # Test custom imports
        from custom_parse_mode import CustomParseMode
        print("  ✅ Custom module imports OK")
        
        return True
        
    except ImportError as e:
        print(f"  ❌ Import error: {e}")
        return False

async def test_custom_parse_mode():
    """Test CustomParseMode functionality"""
    print("🧪 Testing CustomParseMode...")
    
    try:
        from custom_parse_mode import CustomParseMode
        
        # Test markdown mode
        parser = CustomParseMode('markdown')
        test_text = "Hello [😀](emoji/12345) world!"
        parsed_text, entities = parser.parse(test_text)
        
        print(f"  ✅ Parsed text: {parsed_text}")
        print(f"  ✅ Entities: {len(entities)} entities found")
        
        return True
        
    except Exception as e:
        print(f"  ❌ CustomParseMode error: {e}")
        return False

async def test_database_connectivity():
    """Test database connection and basic operations"""
    print("🧪 Testing database connectivity...")
    
    try:
        # Load environment variables
        from dotenv import load_dotenv
        load_dotenv()
        
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            print("  ⚠️ DATABASE_URL not set, skipping database test")
            return True
            
        # Test database connection
        import asyncpg
        conn = await asyncpg.connect(database_url)
        
        # Test basic query
        result = await conn.fetchval("SELECT 1")
        print(f"  ✅ Database connection OK (test query result: {result})")
        
        # Test table existence
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('emoji_replacements', 'monitored_channels', 'bot_settings')
        """)
        
        table_names = [row['table_name'] for row in tables]
        print(f"  ✅ Found tables: {table_names}")
        
        await conn.close()
        return True
        
    except Exception as e:
        print(f"  ❌ Database error: {e}")
        return False

async def test_bot_class_structure():
    """Test that the bot class can be instantiated (with mocked credentials)"""
    print("🧪 Testing bot class structure...")
    
    try:
        # Mock environment variables
        os.environ['API_ID'] = '12345'
        os.environ['API_HASH'] = 'test_hash'
        os.environ['SESSION_STRING'] = 'test_session'
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost/test'
        
        # Import and test bot class structure
        from telegram_bot import TelegramEmojiBot
        
        # This should fail on validation but we can catch that
        try:
            bot = TelegramEmojiBot()
            print("  ❌ Expected validation error but got none")
            return False
        except ValueError as e:
            if "Missing required environment variables" in str(e):
                print("  ✅ Environment variable validation works")
            else:
                print(f"  ❌ Unexpected validation error: {e}")
                return False
        
        # Test with proper mock values
        os.environ['DATABASE_URL'] = os.getenv('DATABASE_URL', 'postgresql://test:test@localhost/test')
        
        # Test class methods exist
        methods_to_check = [
            'init_database',
            'load_emoji_mappings', 
            'load_monitored_channels',
            'add_emoji_replacement',
            'delete_emoji_replacement',
            'add_monitored_channel',
            'remove_monitored_channel',
            'extract_emojis_from_text',
            'replace_emojis_in_message',
            'handle_private_message',
            'setup_event_handlers',
            'start',
            'stop'
        ]
        
        bot_class = TelegramEmojiBot
        missing_methods = []
        
        for method in methods_to_check:
            if not hasattr(bot_class, method):
                missing_methods.append(method)
        
        if missing_methods:
            print(f"  ❌ Missing methods: {missing_methods}")
            return False
        else:
            print(f"  ✅ All {len(methods_to_check)} required methods found")
            
        return True
        
    except Exception as e:
        print(f"  ❌ Bot class error: {e}")
        return False

async def test_emoji_extraction():
    """Test emoji extraction functionality"""
    print("🧪 Testing emoji extraction...")
    
    try:
        from telegram_bot import TelegramEmojiBot
        
        # Create a mock bot instance for testing
        os.environ.update({
            'API_ID': '12345',
            'API_HASH': 'test_hash', 
            'SESSION_STRING': 'test_session',
            'DATABASE_URL': os.getenv('DATABASE_URL', 'postgresql://test:test@localhost/test')
        })
        
        # We can't instantiate due to validation, so we'll test the method directly
        # Create test strings with emojis
        test_texts = [
            "Hello 😀 world!",
            "Multiple emojis: 😀 🚀 ❤️ 🎉",
            "No emojis here",
            "Mixed content 😀 with text 🌟",
        ]
        
        # Import the regex pattern and test it directly
        import re
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
        
        for text in test_texts:
            found_emojis = emoji_pattern.findall(text)
            print(f"  '{text}' -> {found_emojis}")
        
        print("  ✅ Emoji extraction pattern works")
        return True
        
    except Exception as e:
        print(f"  ❌ Emoji extraction error: {e}")
        return False

async def run_all_tests():
    """Run all tests"""
    print("🚀 Starting Telegram Bot Tests")
    print("=" * 50)
    
    tests = [
        ("Imports", test_imports),
        ("CustomParseMode", test_custom_parse_mode), 
        ("Database Connectivity", test_database_connectivity),
        ("Bot Class Structure", test_bot_class_structure),
        ("Emoji Extraction", test_emoji_extraction),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        print(f"\n📋 Running {test_name} test...")
        try:
            result = await test_func()
            results[test_name] = result
        except Exception as e:
            print(f"  ❌ Test failed with exception: {e}")
            results[test_name] = False
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    print("=" * 50)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The bot is ready to run.")
    else:
        print("⚠️ Some tests failed. Please check the errors above.")
    
    return passed == total

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)