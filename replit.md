# Overview

This is a comprehensive Telegram bot built with Telethon that automatically monitors specified Telegram channels and replaces normal emojis with premium emojis in real-time. The bot features an Arabic command interface for easy management and uses PostgreSQL for persistent data storage. It operates using session string authentication, eliminating the need for phone/password authentication during runtime.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Core Architecture Pattern
The application follows a single-class architecture centered around the `TelegramEmojiBot` class, which encapsulates all bot functionality including database operations, event handling, and message processing.

## Authentication & Session Management
- **Session String Authentication**: Uses Telethon's StringSession for persistent authentication without requiring phone/password during runtime
- **Session Generation**: Includes a separate utility script (`generate_session.py`) for one-time session string generation
- **Environment-based Configuration**: All sensitive credentials stored as environment variables

## Message Processing Pipeline
- **Real-time Monitoring**: Event-driven architecture using Telethon's event handlers to monitor channel messages
- **Emoji Replacement Engine**: Automatic detection and replacement of standard emojis with premium variants based on database mappings
- **Message Editing**: In-place editing of original messages to maintain message history and context

## Custom Parsing System
- **CustomParseMode Class**: Handles both Markdown and HTML parsing for advanced message formatting
- **Premium Emoji Support**: Specialized handling for Telegram's premium emoji system using custom entity types
- **Spoiler Support**: Additional support for spoiler text formatting

## Command Interface
- **Arabic Command System**: User-friendly Arabic commands for bot management
- **CRUD Operations**: Complete management of emoji mappings and channel monitoring lists
- **Administrative Controls**: Channel addition/removal, emoji mapping management, and system status commands

## Database Architecture
- **PostgreSQL Backend**: Relational database for reliable data persistence
- **Connection Management**: Async database operations with proper connection handling
- **Data Models**: 
  - Emoji replacement mappings (normal emoji → premium emoji)
  - Monitored channels list
  - Bot configuration and settings

## Error Handling & Logging
- **Comprehensive Logging**: Multi-level logging to both file and console
- **Exception Management**: Proper handling of Telegram API errors (flood limits, authentication, etc.)
- **Graceful Degradation**: System continues operation even when individual operations fail

# External Dependencies

## Core Framework
- **Telethon**: Primary Telegram client library for bot functionality and API interactions
- **asyncpg**: Asynchronous PostgreSQL driver for database operations

## Configuration & Environment
- **python-dotenv**: Environment variable management for secure configuration

## Database
- **PostgreSQL**: Primary data storage for emoji mappings, channel lists, and bot settings
- **Connection Pooling**: Efficient database connection management for concurrent operations

## Telegram API Integration
- **Telegram Bot API**: Core messaging and channel monitoring capabilities
- **Premium Emoji API**: Access to Telegram's premium emoji system for advanced emoji replacement
- **Channel Management API**: Channel monitoring and message editing capabilities

## Development & Testing
- **Built-in Testing Framework**: Custom test utilities for validating imports and functionality
- **Session Management Tools**: Utilities for generating and managing Telegram session strings