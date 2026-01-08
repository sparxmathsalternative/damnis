import discord
from discord.ext import commands
from flask import Flask, jsonify, request
from flask_cors import CORS
import asyncio
import threading
import base64
import os
from io import BytesIO
import aiohttp
from collections import deque
import time

# Configuration from environment variables
DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
API_SECRET = os.getenv('API_SECRET_KEY', 'change-me-in-production')
PORT = int(os.getenv('PORT', 5000))

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable is required!")

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Flask API setup
app = Flask(__name__)
CORS(app)

# Store recent messages per channel (channel_id: deque of messages)
message_cache = {}
MAX_CACHED_MESSAGES = 50

# Store webhooks per channel
webhooks_cache = {}

# Bot status tracking
bot_status = {
    'ready': False,
    'start_time': None,
    'latency': 0,
    'guilds_count': 0,
    'last_message_time': None
}

def verify_api_key(req):
    """Simple API key verification"""
    auth = req.headers.get('Authorization')
    return auth == f'Bearer {API_SECRET}'

async def get_avatar_base64(user):
    """Get user avatar as base64"""
    try:
        if user.avatar:
            avatar_url = user.avatar.url
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        return base64.b64encode(img_data).decode('utf-8')
        return None
    except:
        return None

async def get_or_create_webhook(channel):
    """Get existing webhook or create new one for channel"""
    if channel.id in webhooks_cache:
        return webhooks_cache[channel.id]
    
    webhooks = await channel.webhooks()
    webhook = None
    
    # Look for existing webhook
    for wh in webhooks:
        if wh.user == bot.user:
            webhook = wh
            break
    
    # Create new webhook if none exists
    if not webhook:
        webhook = await channel.create_webhook(name="Voidagon Bridge")
    
    webhooks_cache[channel.id] = webhook
    return webhook

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    bot_status['ready'] = True
    bot_status['start_time'] = time.time()
    bot_status['guilds_count'] = len(bot.guilds)

@bot.event
async def on_message(message):
    """Cache messages as they come in"""
    if message.author.bot:
        return
    
    bot_status['last_message_time'] = time.time()
    
    channel_id = str(message.channel.id)
    
    if channel_id not in message_cache:
        message_cache[channel_id] = deque(maxlen=MAX_CACHED_MESSAGES)
    
    # Get avatar as base64
    avatar_b64 = await get_avatar_base64(message.author)
    
    # Get user roles
    roles = []
    if hasattr(message.author, 'roles'):
        roles = [{'id': str(r.id), 'name': r.name, 'color': str(r.color)} 
                 for r in message.author.roles if r.name != '@everyone']
    
    msg_data = {
        'id': str(message.id),
        'content': message.content,
        'author': {
            'id': str(message.author.id),
            'username': message.author.name,
            'display_name': message.author.display_name,
            'avatar_base64': avatar_b64,
            'discriminator': message.author.discriminator,
            'roles': roles
        },
        'timestamp': message.created_at.isoformat(),
        'channel_id': channel_id,
        'guild_id': str(message.guild.id) if message.guild else None
    }
    
    message_cache[channel_id].append(msg_data)
    
    await bot.process_commands(message)

# API Routes

@app.route('/api/health', methods=['GET'])
def health_check():
    """Comprehensive health check endpoint"""
    uptime = None
    if bot_status['start_time']:
        uptime = int(time.time() - bot_status['start_time'])
    
    return jsonify({
        'status': 'healthy' if bot_status['ready'] else 'starting',
        'bot': {
            'ready': bot_status['ready'],
            'connected': bot.is_ready(),
            'latency_ms': round(bot.latency * 1000, 2) if bot.is_ready() else None,
            'uptime_seconds': uptime,
            'user': {
                'id': str(bot.user.id) if bot.user else None,
                'name': bot.user.name if bot.user else None
            } if bot.user else None
        },
        'guilds': {
            'count': len(bot.guilds),
            'cached_messages': sum(len(cache) for cache in message_cache.values())
        },
        'api': {
            'version': '1.0.0',
            'endpoints_available': True
        }
    })

@app.route('/api/discord/status', methods=['GET'])
def discord_status():
    """Check Discord connection status"""
    if not verify_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    return jsonify({
        'status': 'up' if bot.is_ready() else 'down'
    })

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    """Get all guilds (servers) the bot is in"""
    if not verify_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    guilds = []
    for guild in bot.guilds:
        # Get server icon as base64
        icon_b64 = None
        if guild.icon:
            icon_url = str(guild.icon.url)
            guilds.append({
                'id': str(guild.id),
                'name': guild.name,
                'icon_url': icon_url,
                'member_count': guild.member_count
            })
        else:
            guilds.append({
                'id': str(guild.id),
                'name': guild.name,
                'icon_url': None,
                'member_count': guild.member_count
            })
    
    return jsonify({'guilds': guilds})

@app.route('/api/guilds/<guild_id>/channels', methods=['GET'])
def get_channels(guild_id):
    """Get all channels in a guild"""
    if not verify_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'error': 'Guild not found'}), 404
    
    channels = []
    for channel in guild.channels:
        if isinstance(channel, discord.TextChannel):
            channels.append({
                'id': str(channel.id),
                'name': channel.name,
                'type': 'text',
                'category': channel.category.name if channel.category else None
            })
    
    return jsonify({'channels': channels})

@app.route('/api/channels/<channel_id>/messages', methods=['GET'])
def get_messages(channel_id):
    """Get last messages from a channel"""
    if not verify_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    limit = int(request.args.get('limit', 10))
    limit = min(limit, 50)  # Cap at 50
    
    if channel_id not in message_cache:
        return jsonify({'messages': []})
    
    messages = list(message_cache[channel_id])[-limit:]
    return jsonify({'messages': messages})

@app.route('/api/channels/<channel_id>/send', methods=['POST'])
def send_message(channel_id):
    """Send a message via webhook with custom username and avatar"""
    if not verify_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    content = data.get('content')
    username = data.get('username', 'Unknown User')
    avatar_base64 = data.get('avatar_base64')
    
    if not content:
        return jsonify({'error': 'Content required'}), 400
    
    # Run async function in bot's event loop
    future = asyncio.run_coroutine_threadsafe(
        send_webhook_message(channel_id, content, username, avatar_base64),
        bot.loop
    )
    
    try:
        result = future.result(timeout=10)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

async def send_webhook_message(channel_id, content, username, avatar_base64):
    """Send message via webhook"""
    try:
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return {'error': 'Channel not found'}
        
        webhook = await get_or_create_webhook(channel)
        
        # Convert base64 avatar to bytes if provided
        avatar_bytes = None
        if avatar_base64:
            try:
                avatar_bytes = base64.b64decode(avatar_base64)
            except:
                pass
        
        await webhook.send(
            content=content,
            username=username,
            avatar_url=f"data:image/png;base64,{avatar_base64}" if avatar_base64 else None
        )
        
        return {'success': True, 'message': 'Message sent'}
    except Exception as e:
        return {'error': str(e)}

@app.route('/api/guilds/<guild_id>/members/<user_id>', methods=['GET'])
def get_member_info(guild_id, user_id):
    """Get member information including roles"""
    if not verify_api_key(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return jsonify({'error': 'Guild not found'}), 404
    
    member = guild.get_member(int(user_id))
    if not member:
        return jsonify({'error': 'Member not found'}), 404
    
    roles = [{'id': str(r.id), 'name': r.name, 'color': str(r.color)} 
             for r in member.roles if r.name != '@everyone']
    
    return jsonify({
        'id': str(member.id),
        'username': member.name,
        'display_name': member.display_name,
        'roles': roles
    })

@app.route('/', methods=['GET'])
def root():
    """Root endpoint"""
    return jsonify({
        'name': 'Voidagon Discord Bridge API',
        'version': '1.0.0',
        'status': 'online',
        'endpoints': {
            'health': '/api/health',
            'discord_status': '/api/discord/status',
            'guilds': '/api/guilds',
            'channels': '/api/guilds/{guild_id}/channels',
            'messages': '/api/channels/{channel_id}/messages',
            'send': '/api/channels/{channel_id}/send',
            'member_info': '/api/guilds/{guild_id}/members/{user_id}'
        }
    })

def run_flask():
    """Run Flask in a separate thread"""
    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == '__main__':
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Discord bot
    print("Starting Discord bot...")
    print(f"API will be available on port {PORT}")
    bot.run(DISCORD_TOKEN)
