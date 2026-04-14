#!/usr/bin/env python3
"""
Social Media Downloader Bot - FIXED YouTube Issue
Author: @UnknownGuy9876
Channel: https://t.me/+zGWXoEQXRo02YmRl
"""

import os
import sys
import json
import logging
import time
import sqlite3
import threading
import schedule
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Rich for console output
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Telegram Bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# Downloader
from yt_dlp import YoutubeDL

# Initialize
console = Console()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ CONFIGURATION ============
class Config:
    TELEGRAM_TOKEN = "8026936183:AAFBUumehkGz9kO8wbUayEU9nnvoMYIMZIg"
    ADMIN_IDS = [8470395686]  # Add your Telegram ID here
    CHANNEL_LINK = "https://t.me/+zGWXoEQXRo02YmRl"
    BOT_USERNAME = "@UnknownGuy9876"
    
    # Storage limits
    MAX_STORAGE_MB = 1000
    AUTO_CLEANUP_HOURS = 1
    MAX_FILE_SIZE_MB = 50
    
    # User limits
    MAX_DOWNLOADS_PER_DAY = 50
    RATE_LIMIT_PER_HOUR = 30
    
    # Paths
    DOWNLOAD_DIR = "downloads"
    TEMP_DIR = "temp"
    DB_PATH = "downloads.db"
    
    # yt-dlp settings - FIXED FOR YOUTUBE
    YDL_OPTIONS = {
        'quiet': True,
        'no_warnings': False,
        'ignoreerrors': False,
        'no_color': True,
        
        # Fix for YouTube bot detection
        'cookiefile': 'cookies.txt',
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['configs', 'webpage'],
            }
        },
        
        # Headers to mimic browser
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        },
        
        # Retry settings
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
        'retry_sleep_functions': {
            'http': lambda n: 3,
            'fragment': lambda n: 3,
            'file_access': lambda n: 3,
        },
        
        # Network settings
        'socket_timeout': 30,
        'extract_timeout': 180,
    }

# ============ STORAGE MANAGER ============
class StorageManager:
    def __init__(self):
        self.download_dir = Config.DOWNLOAD_DIR
        self.temp_dir = Config.TEMP_DIR
        self.db_path = Config.DB_PATH
        
        # Create directories
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Initialize database
        self.init_database()
        
        # Start cleanup scheduler
        self.start_cleanup_scheduler()
        console.print("[green]✓ Storage Manager initialized[/green]")
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                platform TEXT,
                url TEXT,
                filename TEXT,
                file_path TEXT,
                file_size INTEGER,
                status TEXT DEFAULT 'pending',
                download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_time TIMESTAMP,
                deleted BOOLEAN DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT,
                total_downloads INTEGER DEFAULT 0,
                downloads_today INTEGER DEFAULT 0,
                last_download_date DATE,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def log_download(self, user_id: int, user_name: str, platform: str, 
                    url: str, filename: str, file_path: str) -> int:
        """Log a new download in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        cursor.execute('''
            INSERT INTO downloads 
            (user_id, user_name, platform, url, filename, file_path, file_size, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'downloaded')
        ''', (user_id, user_name, platform, url, filename, file_path, file_size))
        
        download_id = cursor.lastrowid
        
        # Update user stats
        today = datetime.now().date()
        cursor.execute('''
            INSERT OR IGNORE INTO user_stats (user_id, user_name, joined_date)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, user_name))
        
        cursor.execute('''
            UPDATE user_stats 
            SET total_downloads = total_downloads + 1,
                downloads_today = CASE 
                    WHEN last_download_date = DATE(?) THEN downloads_today + 1 
                    ELSE 1 
                END,
                last_download_date = DATE(?)
            WHERE user_id = ?
        ''', (today, today, user_id))
        
        conn.commit()
        conn.close()
        
        return download_id
    
    def mark_as_sent(self, download_id: int):
        """Mark download as sent to user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE downloads 
            SET status = 'sent', 
                sent_time = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (download_id,))
        
        conn.commit()
        conn.close()
    
    def cleanup_old_files(self, hours_old: int = None):
        """Clean up files older than specified hours"""
        if hours_old is None:
            hours_old = Config.AUTO_CLEANUP_HOURS
        
        cutoff_time = datetime.now() - timedelta(hours=hours_old)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, file_path FROM downloads 
            WHERE download_time < ? AND deleted = 0
        ''', (cutoff_time,))
        
        deleted_count = 0
        for file_id, file_path in cursor.fetchall():
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                cursor.execute('UPDATE downloads SET deleted = 1 WHERE id = ?', (file_id,))
                deleted_count += 1
            except:
                pass
        
        conn.commit()
        conn.close()
        
        # Clean empty directories
        self.clean_empty_dirs()
        
        if deleted_count > 0:
            console.print(f"[cyan]🧹 Cleaned {deleted_count} old files[/cyan]")
        
        return deleted_count
    
    def clean_empty_dirs(self):
        """Remove empty directories"""
        for dirpath, dirnames, filenames in os.walk(self.download_dir, topdown=False):
            for dirname in dirnames:
                full_path = os.path.join(dirpath, dirname)
                try:
                    if not os.listdir(full_path):
                        os.rmdir(full_path)
                except:
                    pass
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Get user download statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT total_downloads, downloads_today, joined_date
            FROM user_stats 
            WHERE user_id = ?
        ''', (user_id,))
        
        result = cursor.fetchone()
        
        if result:
            total_downloads, downloads_today, joined_date = result
        else:
            total_downloads = downloads_today = 0
            joined_date = datetime.now()
        
        conn.close()
        
        return {
            'total_downloads': total_downloads,
            'downloads_today': downloads_today,
            'max_per_day': Config.MAX_DOWNLOADS_PER_DAY,
            'joined_date': joined_date,
            'remaining_today': max(0, Config.MAX_DOWNLOADS_PER_DAY - downloads_today)
        }
    
    def can_user_download(self, user_id: int) -> tuple[bool, str]:
        """Check if user can download"""
        stats = self.get_user_stats(user_id)
        
        if stats['downloads_today'] >= Config.MAX_DOWNLOADS_PER_DAY:
            return False, f"You've reached your daily limit ({Config.MAX_DOWNLOADS_PER_DAY} downloads). Try again tomorrow!"
        
        return True, ""
    
    def start_cleanup_scheduler(self):
        """Start automatic cleanup scheduler"""
        def cleanup_job():
            self.cleanup_old_files()
        
        schedule.every(30).minutes.do(cleanup_job)
        
        def run_scheduler():
            while True:
                schedule.run_pending()
                time.sleep(60)
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

# ============ DOWNLOAD MANAGER ============
class DownloadManager:
    def __init__(self):
        self.user_sessions: Dict[int, Dict] = {}
        self.platforms = [
            "YouTube", "Instagram", "TikTok", "Twitter/X",
            "Facebook", "Reddit", "LinkedIn", "Pinterest",
            "Vimeo", "Dailymotion", "SoundCloud", "Twitch",
            "Snapchat", "Likee", "Bilibili"
        ]
        
        # Test YouTube connection on startup
        self.test_youtube_connection()
    
    def test_youtube_connection(self):
        """Test YouTube connection on startup"""
        console.print("[cyan]Testing YouTube connection...[/cyan]")
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rickroll test
        
        try:
            test_opts = Config.YDL_OPTIONS.copy()
            test_opts['quiet'] = True
            test_opts['extract_flat'] = True
            
            with YoutubeDL(test_opts) as ydl:
                info = ydl.extract_info(test_url, download=False)
                if info:
                    console.print("[green]✓ YouTube connection successful![/green]")
                else:
                    console.print("[yellow]⚠ YouTube test failed (no info)[/yellow]")
        except Exception as e:
            console.print(f"[red]✗ YouTube test failed: {str(e)[:100]}[/red]")
            console.print("[yellow]You may need to add cookies.txt file[/yellow]")
    
    def detect_platform(self, url: str) -> str:
        """Detect social media platform from URL"""
        url_lower = url.lower()
        
        if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            return 'YouTube'
        elif 'instagram.com' in url_lower:
            return 'Instagram'
        elif 'tiktok.com' in url_lower:
            return 'TikTok'
        elif 'twitter.com' in url_lower or 'x.com' in url_lower:
            return 'Twitter/X'
        elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
            return 'Facebook'
        elif 'reddit.com' in url_lower:
            return 'Reddit'
        else:
            return 'Unknown'
    
    def get_format_keyboard(self, platform: str) -> InlineKeyboardMarkup:
        """Get format selection keyboard"""
        buttons = []
        
        # Simple format options for all platforms
        buttons = [
            [
                InlineKeyboardButton("🎬 Video (Best)", callback_data="format_video"),
                InlineKeyboardButton("🎵 Audio Only", callback_data="format_audio")
            ],
            [
                InlineKeyboardButton("📱 Medium Quality", callback_data="format_medium"),
                InlineKeyboardButton("💾 Small Size", callback_data="format_small")
            ]
        ]
        
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
        return InlineKeyboardMarkup(buttons)
    
    def get_ydl_options(self, format_choice: str, platform: str) -> Dict:
        """Get yt-dlp options"""
        # Create timestamp for unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"%(title)s_{timestamp}.%(ext)s"
        output_path = os.path.join(storage_manager.download_dir, filename)
        
        ydl_opts = Config.YDL_OPTIONS.copy()
        ydl_opts['outtmpl'] = output_path
        
        # Platform-specific settings
        if platform == "YouTube":
            # YouTube specific settings
            ydl_opts.update({
                'format': self.get_youtube_format(format_choice),
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'web'],
                        'player_skip': ['configs', 'webpage'],
                    }
                }
            })
        elif platform == "Instagram":
            ydl_opts['format'] = 'best'
            ydl_opts['extractor_args'] = {'instagram': {'post': 'single'}}
        elif platform == "TikTok":
            ydl_opts['format'] = 'best'
            ydl_opts['extractor_args'] = {'tiktok': {'app_version': '29.7.4'}}
        else:
            ydl_opts['format'] = 'best'
        
        # Audio extraction
        if format_choice == "format_audio":
            ydl_opts['format'] = 'bestaudio'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        return ydl_opts
    
    def get_youtube_format(self, format_choice: str) -> str:
        """Get YouTube format string"""
        format_map = {
            "format_video": "bv*+ba/b",  # Best video + audio
            "format_audio": "ba",        # Best audio only
            "format_medium": "best[height<=720]/best",  # 720p or best
            "format_small": "best[height<=480]/best",   # 480p or best
        }
        return format_map.get(format_choice, "bv*+ba/b")
    
    def progress_hook(self, d):
        """Progress hook for downloads"""
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%').strip()
            speed = d.get('_speed_str', 'N/A').strip()
            console.print(f"[cyan]Progress: {percent} | Speed: {speed}[/cyan]", end="\r")
        elif d['status'] == 'finished':
            console.print("\n[green]✓ Download completed[/green]")

# ============ GLOBAL INSTANCES ============
storage_manager = StorageManager()
download_manager = DownloadManager()

# ============ TELEGRAM HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user = update.effective_user
    
    welcome_text = f"""
👋 *Welcome {user.first_name}!* 

🎬 *Social Media Downloader Bot*
Download videos from 15+ platforms instantly!

*Supported Platforms:*
• YouTube (Videos/Shorts)
• Instagram (Reels/Posts)
• TikTok (Without Watermark)
• Twitter/X (Videos)
• Facebook (Videos)
• Reddit, and more!

*How to use:*
1. Send any social media link
2. Choose format quality
3. Get your video/audio!

*Commands:*
/start - Show this message
/stats - Your download statistics
/help - Help & instructions

*Bot by:* {Config.BOT_USERNAME}
*Channel:* {Config.CHANNEL_LINK}
    """
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Join Channel", url=Config.CHANNEL_LINK)
    ]])
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help instructions"""
    help_text = """
📚 *How to Download:*

1. Copy any social media video link
2. Send it to this bot
3. Choose your preferred quality
4. Wait for download (10-30 seconds)
5. Receive your video/audio!

*Tips for YouTube:*
• If you get "Sign in" error, use Medium or Small quality
• Some videos might require cookies (contact admin)
• Audio Only option works best for restricted videos

*Supported Sites:*
• YouTube, Instagram, TikTok
• Twitter/X, Facebook, Reddit
• And 10+ more platforms!

*Need help?* Contact @UnknownGuy9876
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    user = update.effective_user
    user_stats = storage_manager.get_user_stats(user.id)
    
    stats_text = f"""
📊 *Your Statistics*

👤 User: `{user.username or user.first_name}`
🆔 ID: `{user.id}`

*Download Stats:*
📥 Today: `{user_stats['downloads_today']}/{user_stats['max_per_day']}`
📈 Total: `{user_stats['total_downloads']}`

*Remaining today:* `{user_stats['remaining_today']}` downloads
    """
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages with URLs"""
    user = update.effective_user
    url = update.message.text.strip()
    
    # Check if it's a URL
    if not url.startswith(('http://', 'https://', 'www.')):
        await update.message.reply_text(
            "❌ Please send a valid URL starting with http:// or https://\n\n"
            "Example: `https://www.youtube.com/watch?v=...`",
            parse_mode='Markdown'
        )
        return
    
    # Check rate limit
    can_download, reason = storage_manager.can_user_download(user.id)
    if not can_download:
        await update.message.reply_text(f"⚠️ {reason}")
        return
    
    # Detect platform
    platform = download_manager.detect_platform(url)
    
    if platform == "Unknown":
        await update.message.reply_text(
            "⚠️ *Platform not recognized*\n"
            "Trying to download anyway...",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"✅ *{platform}* link detected!",
            parse_mode='Markdown'
        )
    
    # Store user session
    download_manager.user_sessions[user.id] = {
        'url': url,
        'platform': platform,
        'user_name': user.username or user.first_name,
        'chat_id': update.message.chat_id,
        'message_id': update.message.message_id
    }
    
    # Ask for format
    keyboard = download_manager.get_format_keyboard(platform)
    await update.message.reply_text(
        "🎬 *Select Quality:*\n"
        "• Video (Best): Highest quality\n"
        "• Audio Only: Extract MP3\n"
        "• Medium: 720p for YouTube\n"
        "• Small: 480p for restricted videos",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "cancel":
        await query.edit_message_text("❌ Download cancelled.")
        if user_id in download_manager.user_sessions:
            del download_manager.user_sessions[user_id]
        return
    
    if user_id not in download_manager.user_sessions:
        await query.edit_message_text("❌ Session expired. Please send the link again.")
        return
    
    # Get user session
    session = download_manager.user_sessions[user_id]
    url = session['url']
    platform = session['platform']
    user_name = session['user_name']
    
    # Show downloading message
    await query.edit_message_text(
        f"⏬ *Downloading from {platform}...*\n"
        "⏳ Please wait, this may take 10-30 seconds...",
        parse_mode='Markdown'
    )
    
    try:
        # Get download options
        ydl_opts = download_manager.get_ydl_options(data, platform)
        
        # Add progress hook
        ydl_opts['progress_hooks'] = [download_manager.progress_hook]
        
        console.print(f"\n{'='*50}")
        console.print(f"[cyan]Starting download...[/cyan]")
        console.print(f"[yellow]Platform: {platform}[/yellow]")
        console.print(f"[yellow]URL: {url}[/yellow]")
        console.print(f"[yellow]Format: {data}[/yellow]")
        console.print(f"[yellow]Options: {ydl_opts.get('format', 'default')}[/yellow]")
        
        # Try different methods for YouTube
        if platform == "YouTube":
            await handle_youtube_download(query, context, url, ydl_opts, data, user_id, user_name, platform)
        else:
            await handle_other_platforms(query, context, url, ydl_opts, user_id, user_name, platform)
        
    except Exception as e:
        error_msg = str(e)
        console.print(f"[red]Fatal error: {error_msg}[/red]")
        
        # Try alternative method for YouTube
        if platform == "YouTube" and "Sign in" in error_msg:
            await query.edit_message_text(
                "🔄 *YouTube requires authentication*\n"
                "Trying alternative format...",
                parse_mode='Markdown'
            )
            
            # Try with different format
            try:
                alt_opts = ydl_opts.copy()
                alt_opts['format'] = 'best[height<=720]/best[height<=480]/best'
                alt_opts['quiet'] = True
                
                with YoutubeDL(alt_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    await send_downloaded_file(query, context, info, url, user_id, user_name, platform, ydl)
            except Exception as alt_e:
                await show_error_message(query, error_msg, platform, url)
        else:
            await show_error_message(query, error_msg, platform, url)
    
    finally:
        # Clean up session
        if user_id in download_manager.user_sessions:
            del download_manager.user_sessions[user_id]

async def handle_youtube_download(query, context, url, ydl_opts, format_choice, user_id, user_name, platform):
    """Handle YouTube downloads with fallback methods"""
    
    # Method 1: Try with standard options
    try:
        console.print("[cyan]Trying Method 1: Standard download...[/cyan]")
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            await send_downloaded_file(query, context, info, url, user_id, user_name, platform, ydl)
        return
    except Exception as e1:
        console.print(f"[yellow]Method 1 failed: {str(e1)[:100]}[/yellow]")
    
    # Method 2: Try with simpler format
    try:
        await query.edit_message_text(
            "🔄 *Method 1 failed, trying alternative...*",
            parse_mode='Markdown'
        )
        
        alt_opts = ydl_opts.copy()
        if format_choice == "format_video":
            alt_opts['format'] = 'best[height<=720]'
        elif format_choice == "format_medium":
            alt_opts['format'] = 'best[height<=480]'
        elif format_choice == "format_small":
            alt_opts['format'] = 'worst'
        
        console.print("[cyan]Trying Method 2: Simpler format...[/cyan]")
        with YoutubeDL(alt_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            await send_downloaded_file(query, context, info, url, user_id, user_name, platform, ydl)
        return
    except Exception as e2:
        console.print(f"[yellow]Method 2 failed: {str(e2)[:100]}[/yellow]")
    
    # Method 3: Try audio only
    try:
        await query.edit_message_text(
            "🔄 *Trying audio only...*",
            parse_mode='Markdown'
        )
        
        audio_opts = ydl_opts.copy()
        audio_opts['format'] = 'bestaudio'
        audio_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        
        console.print("[cyan]Trying Method 3: Audio only...[/cyan]")
        with YoutubeDL(audio_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            await send_downloaded_file(query, context, info, url, user_id, user_name, platform, ydl)
        return
    except Exception as e3:
        console.print(f"[red]All methods failed: {str(e3)[:100]}[/red]")
        raise Exception(f"All download methods failed. Last error: {str(e3)}")

async def handle_other_platforms(query, context, url, ydl_opts, user_id, user_name, platform):
    """Handle non-YouTube downloads"""
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        await send_downloaded_file(query, context, info, url, user_id, user_name, platform, ydl)

async def send_downloaded_file(query, context, info, url, user_id, user_name, platform, ydl):
    """Send the downloaded file to user"""
    if not info:
        raise Exception("No video information received")
    
    # Get downloaded file
    file_path = ydl.prepare_filename(info)
    
    # Check if file exists (handle different extensions)
    if not os.path.exists(file_path):
        # Try to find with different extensions
        base_name = os.path.splitext(file_path)[0]
        for ext in ['.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.m4v']:
            test_path = base_name + ext
            if os.path.exists(test_path):
                file_path = test_path
                break
    
    if not os.path.exists(file_path):
        raise Exception("Downloaded file not found")
    
    # Check file size
    file_size = os.path.getsize(file_path)
    if file_size > Config.MAX_FILE_SIZE_MB * 1024 * 1024:
        os.remove(file_path)
        raise Exception(f"File too large ({file_size//1024//1024}MB). Try lower quality.")
    
    # Log download
    download_id = storage_manager.log_download(
        user_id=user_id,
        user_name=user_name,
        platform=platform,
        url=url,
        filename=os.path.basename(file_path),
        file_path=file_path
    )
    
    # Update message
    video_title = info.get('title', 'Video')[:100]
    await query.edit_message_text(
        f"✅ *Download Complete!*\n"
        f"📹 {video_title}\n"
        f"📏 Size: `{file_size//1024//1024}MB`\n"
        f"📤 *Sending to you...*",
        parse_mode='Markdown'
    )
    
    # Send file
    caption = (
        f"✅ Downloaded from {platform}\n"
        f"📹 {video_title}\n\n"
        f"*Bot by:* {Config.BOT_USERNAME}\n"
        f"*Channel:* {Config.CHANNEL_LINK}"
    )
    
    if file_path.endswith(('.mp3', '.m4a', '.ogg', '.wav')):
        await context.bot.send_audio(
            chat_id=query.message.chat_id,
            audio=open(file_path, 'rb'),
            caption=caption,
            parse_mode='Markdown'
        )
    else:
        await context.bot.send_video(
            chat_id=query.message.chat_id,
            video=open(file_path, 'rb'),
            caption=caption,
            supports_streaming=True,
            parse_mode='Markdown'
        )
    
    # Mark as sent
    storage_manager.mark_as_sent(download_id)
    
    # Delete file
    try:
        os.remove(file_path)
        console.print(f"[green]✓ File sent and deleted[/green]")
    except:
        pass
    
    # Send success message
    success_text = (
        f"🎉 *Successfully Sent!*\n\n"
        f"*Credits:*\n"
        f"Bot by {Config.BOT_USERNAME}\n"
        f"Join our channel: {Config.CHANNEL_LINK}\n\n"
        f"✨ *Thank you for using our service!*"
    )
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Join Channel", url=Config.CHANNEL_LINK)
    ]])
    
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=success_text,
        reply_markup=keyboard,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

async def show_error_message(query, error_msg, platform, url):
    """Show error message to user"""
    # Clean error message
    if "Sign in" in error_msg:
        error_msg = "YouTube requires authentication. Try Medium or Small quality."
    elif "Requested format is not available" in error_msg:
        error_msg = "Format not available. Try different quality."
    elif "Unavailable" in error_msg:
        error_msg = "Video not available or restricted."
    elif "Private" in error_msg:
        error_msg = "Video is private."
    elif "too large" in error_msg:
        error_msg = "File too large for Telegram. Try lower quality."
    
    error_text = (
        f"❌ *Download Failed*\n\n"
        f"*Platform:* {platform}\n"
        f"*Error:* `{error_msg[:150]}`\n\n"
        f"*Try these solutions:*\n"
        f"1. Use Medium or Small quality\n"
        f"2. Try Audio Only option\n"
        f"3. Try a different video\n"
        f"4. Wait a few minutes\n\n"
        f"*Need help?* Contact @UnknownGuy9876"
    )
    
    await query.edit_message_text(error_text, parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)

def main():
    """Start the bot."""
    # Display banner
    banner = Panel.fit(
        f"[bold cyan]Social Media Downloader Bot[/bold cyan]\n"
        f"[green]Author: {Config.BOT_USERNAME}[/green]\n"
        f"[yellow]Channel: {Config.CHANNEL_LINK}[/yellow]\n\n"
        f"[cyan]YouTube Fix Applied![/cyan]\n"
        f"✅ Token: Loaded\n"
        f"✅ yt-dlp: Ready\n"
        f"✅ YouTube: 3 fallback methods\n"
        f"✅ Storage: Active cleanup\n"
        f"✅ Database: Ready",
        title="🤖 Bot Status - FIXED",
        border_style="cyan"
    )
    
    console.print(banner)
    
    # Check for cookies.txt
    if os.path.exists('cookies.txt'):
        console.print("[green]✓ cookies.txt found[/green]")
    else:
        console.print("[yellow]⚠ No cookies.txt found[/yellow]")
        console.print("[cyan]Creating cookies instructions...[/cyan]")
        
        # Create instructions file
        with open('cookies_instructions.txt', 'w') as f:
            f.write("""How to create cookies.txt for YouTube:

1. Install Chrome extension: "Get cookies.txt LOCALLY"
2. Go to YouTube.com and login
3. Click the extension icon
4. Click "Export"
5. Save as "cookies.txt" in bot folder
6. Restart bot

This fixes YouTube's "Sign in to confirm you're not a bot" error.
""")
        console.print("[green]✓ Created cookies_instructions.txt[/green]")
    
    # Create application
    application = Application.builder().token(Config.TELEGRAM_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    # Start the bot
    console.print("[green]✓ Bot is now running! Press Ctrl+C to stop.[/green]")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user.[/yellow]")
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")