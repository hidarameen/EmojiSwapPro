# Telegram Premium Emoji Bot with Control System

A comprehensive dual-bot system: a Telegram UserBot built with Telethon that monitors channels and replaces emojis, combined with an official Bot API control interface for easy management through inline queries and interactive buttons.

## Features

### 🤖 Dual Bot System
- **UserBot (Telethon)**: Handles actual channel monitoring and emoji replacement
- **Control Bot (Official Bot API)**: Provides interactive management interface
- **Seamless Integration**: Commands flow from Control Bot to UserBot automatically

### 🔄 Emoji Replacement (UserBot)
- Monitors specified Telegram channels for new posts
- Automatically replaces normal emojis with premium emojis based on database mappings  
- Edits original messages in real-time
- Supports message edits as well as new messages
- Channel-specific and global replacement rules

### 🎛️ Interactive Control Interface (Control Bot)
- **Full Inline Mode Support**: Type `@controlbot` anywhere to access controls
- **Interactive Buttons**: No need to remember commands
- **Real-time Results**: Instant feedback from UserBot operations
- **Comprehensive Management**: All UserBot functions accessible through UI

#### Available Control Menus:
- 📺 **Channel Management**: Add/remove channels, check permissions
- 😀 **Emoji Management**: Global and channel-specific replacements
- 🔄 **Forwarding Tasks**: Message copying between channels with delays
- 👥 **Admin Management**: User authorization control
- 📊 **Statistics**: System status and detailed reports
- 🔧 **Advanced Tools**: Backup, sync, and maintenance

### 🗄️ Database Storage
- PostgreSQL database for persistent storage
- Emoji replacement mappings (global and channel-specific)
- Monitored channels list
- Forwarding task configurations
- Admin user management
- Command queue for bot communication

### 🔐 Security Features
- Session string authentication (no phone/password required)
- Multi-level user authorization
- Environment variable configuration
- Database connection pooling
- Comprehensive error handling and logging
- Separate bot tokens for enhanced security

## Setup Instructions

### 1. Prerequisites
- Python 3.11+
- PostgreSQL database (provided by Replit)
- Telegram API credentials
- Telegram session string

### 2. Get Telegram API Credentials
1. Go to https://my.telegram.org/apps
2. Log in with your Telegram account
3. Create a new application
4. Note down your `API_ID` and `API_HASH`

### 3. Generate Session String
You need to generate a session string for your Telegram account. You can use this script:

```python
# generate_session.py
from telethon import TelegramClient
import asyncio

API_ID = your_api_id
API_HASH = 'your_api_hash'

async def main():
    client = TelegramClient('session', API_ID, API_HASH)
    await client.start()
    session_string = client.session.save()
    print(f"Session string: {session_string}")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
```

### 4. Create Control Bot
1. Message @BotFather on Telegram
2. Create a new bot with `/newbot`
3. Get your bot token
4. Enable inline mode with `/setinline` and provide a description

### 5. Environment Setup
1. Copy `.env.example` to `.env`
2. Fill in your credentials:
   ```
   # UserBot credentials
   API_ID=your_api_id
   API_HASH=your_api_hash
   SESSION_STRING=your_session_string
   
   # Database
   DATABASE_URL=your_database_url
   
   # Control Bot
   CONTROL_BOT_TOKEN=your_bot_token_from_botfather
   USERBOT_ADMIN_ID=your_user_id
   ```

### 5. Install Dependencies
```bash
pip install -r requirements.txt
```

### 6. Run the Bot
```bash
python telegram_bot.py
```

## Usage Guide

### Adding Emoji Replacements
Send a private message to the bot:
```
إضافة_استبدال 😀 12345 وصف اختياري
```
- `😀` - The normal emoji to replace
- `12345` - The premium emoji ID 
- `وصف اختياري` - Optional description

### Managing Channels
Add a channel to monitor:
```
إضافة_قناة @channelname
```
or
```
إضافة_قناة -1001234567890
```

List monitored channels:
```
عرض_القنوات
```

Remove a channel:
```
حذف_قناة -1001234567890
```

### Viewing Replacements
List all emoji replacements:
```
عرض_الاستبدالات
```

Delete an emoji replacement:
```
حذف_استبدال 😀
```

## Database Schema

### emoji_replacements
- `id` - Primary key
- `normal_emoji` - The emoji to replace (unique)
- `premium_emoji_id` - Premium emoji ID
- `description` - Optional description
- `created_at` - Creation timestamp

### monitored_channels  
- `id` - Primary key
- `channel_id` - Telegram channel ID (unique)
- `channel_username` - Channel username (@channelname)
- `channel_title` - Channel display name
- `is_active` - Whether monitoring is active
- `created_at` - Creation timestamp

### bot_settings
- `id` - Primary key  
- `setting_key` - Setting name (unique)
- `setting_value` - Setting value
- `updated_at` - Last update timestamp

## Logging

The bot logs all activities to:
- Console output
- `telegram_bot.log` file

Log levels include:
- INFO: General bot operations
- ERROR: Error conditions and exceptions
- DEBUG: Detailed debugging information

## Architecture

### Components
- **TelegramEmojiBot**: Main bot class
- **CustomParseMode**: Premium emoji parsing (from custom_parse_mode.py)
- **Database Layer**: AsyncPG for PostgreSQL operations
- **Event Handlers**: Telethon event processing
- **Command Handlers**: Arabic command processing

### Key Features
- **Session String Authentication**: No need for phone/password
- **Database Caching**: In-memory caching for performance  
- **Real-time Processing**: Immediate emoji replacement
- **Error Recovery**: Comprehensive exception handling
- **Arabic Interface**: Native Arabic command support

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is open source and available under the MIT License.

## Support

For issues and support, please:
1. Check the logs in `telegram_bot.log`
2. Verify your environment variables are correct
3. Ensure database connectivity
4. Check Telegram API rate limits

## Troubleshooting

### Common Issues

**Bot doesn't start:**
- Check environment variables in `.env`
- Verify session string is valid
- Check database connectivity

**Emoji replacement not working:**
- Verify channel is in monitoring list
- Check emoji mappings exist in database
- Review bot permissions in channels

**Commands not responding:**
- Ensure you're sending commands in private messages
- Check command spelling (Arabic)
- Review bot logs for errors

**Database connection fails:**
- Verify DATABASE_URL is correct
- Check PostgreSQL service is running
- Review database permissions