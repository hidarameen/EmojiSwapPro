#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Session String Generator for Telegram Bot

This script helps generate a session string that can be used with the Telegram bot
instead of phone/password authentication.

Usage:
1. Set your API_ID and API_HASH from https://my.telegram.org/apps
2. Run this script: python generate_session.py  
3. Enter your phone number and verification code
4. Copy the generated session string to your .env file

Note: This script should be run once to generate the session string.
After that, you can use the session string in your bot without phone/password.
"""

import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

# Replace these with your actual values from https://my.telegram.org/apps
API_ID = 12345  # Replace with your actual API ID
API_HASH = 'your_api_hash_here'  # Replace with your actual API hash

async def generate_session_string():
    """Generate and return a session string"""
    
    print("📱 Telegram Session String Generator")
    print("=" * 40)
    
    # Validate API credentials
    if API_ID == 12345 or API_HASH == 'your_api_hash_here':
        print("❌ Error: Please update API_ID and API_HASH with your actual values")
        print("   Get them from: https://my.telegram.org/apps")
        return None
    
    # Create client
    client = TelegramClient('temp_session', API_ID, API_HASH)
    
    try:
        print("🔌 Connecting to Telegram...")
        await client.connect()
        
        if not await client.is_user_authorized():
            print("📞 Please enter your phone number (with country code):")
            phone = input("Phone: ")
            
            print("📤 Sending verification code...")
            await client.send_code_request(phone)
            
            print("📥 Please enter the verification code you received:")
            code = input("Code: ")
            
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                print("🔒 Two-factor authentication enabled.")
                print("Please enter your 2FA password:")
                password = input("Password: ")
                await client.sign_in(password=password)
        
        # Generate session string
        if client.session:
            session_string = client.session.save()
        else:
            print("❌ Error: Session not available")
            return None
        
        print("✅ Session string generated successfully!")
        print("=" * 40)
        print("🔑 Your session string:")
        print(session_string)
        print("=" * 40)
        print("📋 Copy this session string to your .env file as SESSION_STRING")
        print("⚠️  Keep this session string secure and never share it!")
        
        # Get user info
        me = await client.get_me()
        first_name = getattr(me, 'first_name', 'Unknown User')
        print(f"👤 Logged in as: {first_name}")
        username = getattr(me, 'username', None)
        if username:
            print(f"   Username: @{username}")
        
        return session_string
        
    except Exception as e:
        print(f"❌ Error generating session string: {e}")
        return None
        
    finally:
        try:
            is_connected_method = getattr(client, 'is_connected', None)
            disconnect_method = getattr(client, 'disconnect', None)
            if is_connected_method and callable(is_connected_method) and is_connected_method():
                if disconnect_method and callable(disconnect_method):
                    await disconnect_method()
        except Exception as e:
            print(f"Warning: Failed to disconnect client: {e}")
        
        # Clean up temporary session file
        import os
        try:
            os.remove('temp_session.session')
        except:
            pass

def main():
    """Main function"""
    print("Starting session string generation...\n")
    
    # Check if we can import required modules
    try:
        import telethon
    except ImportError:
        print("❌ Error: Telethon not installed")
        print("   Install with: pip install telethon")
        return
    
    # Generate session string
    session_string = asyncio.run(generate_session_string())
    
    if session_string:
        print(f"\n✨ Success! Your session string is ready to use.")
        print("   Add it to your .env file and run the bot.")
    else:
        print(f"\n❌ Failed to generate session string.")
        print("   Please check your API credentials and try again.")

if __name__ == "__main__":
    main()