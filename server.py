import discord
from discord.ext import commands
from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS
import asyncio
import threading
import base64
import os
import secrets
import hashlib
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
import aiohttp
from collections import deque
from pymongo import MongoClient
from datetime import datetime

# Configuration from environment variables
DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
PORT = int(os.getenv('PORT', 5000))
MONGODB_URI = os.getenv('MONGODB')
APP_EMAIL = os.getenv('APP_EMAIL')
APP_PASS = os.getenv('APP_PASS')

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable is required!")
if not MONGODB_URI:
    raise ValueError("MONGODB environment variable is required!")
if not APP_EMAIL or not APP_PASS:
    raise ValueError("APP_EMAIL and APP_PASS environment variables are required!")

# MongoDB setup
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client['nullchat']
users_collection = db['users']
sessions_collection = db['sessions']
verification_codes = db['verification_codes']

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Flask API setup
app = Flask(__name__)
CORS(app)

# Store recent messages per channel
message_cache = {}
MAX_CACHED_MESSAGES = 50

# Store webhooks per channel
webhooks_cache = {}

# Bot status tracking
bot_status = {
    'ready': False,
    'start_time': None,
    'last_message_time': None
}

def hash_password(password):
    """Hash password with SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    """Generate secure random token"""
    return secrets.token_urlsafe(32)

def generate_verification_code():
    """Generate 6-digit verification code"""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

def send_verification_email(email, code):
    """Send verification code via email optimized for Render/Cloud hosting"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'NullChat - Verify Your Email'
        msg['From'] = f"NullChat <{APP_EMAIL}>"
        msg['To'] = email
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2 style="color: #0019a3;">nullchat</h2>
            <p>Your verification code is:</p>
            <h1 style="letter-spacing: 5px; color: #ff0d9e;">{code}</h1>
            <p>Expires in 15 minutes.</p>
        </body>
        </html>
        """
        msg.attach(MIMEText(html, 'html'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls() 
        server.login(APP_EMAIL, APP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR: {str(e)}")
        return False

# ============= AUTHENTICATION ENDPOINTS =============

@app.route('/api/auth/register', methods=['POST'])
def register_user():
    data = request.json
    email = data.get('email')
    username = data.get('username')
    password = data.get('password')
    
    if not all([email, username, password]): 
        return jsonify({'error': 'Missing fields'}), 400
        
    if users_collection.find_one({'$or': [{'email': email}, {'username': username}]}):
        return jsonify({'error': 'User already exists'}), 400
    
    code = generate_verification_code()
    verification_codes.delete_many({'email': email})
    verification_codes.insert_one({
        'email': email, 
        'username': username, 
        'password_hash': hash_password(password),
        'code': code, 
        'expires_at': time.time() + 900
    })
    
    # Send verification email
    if not send_verification_email(email, code):
        # We print the code to the console as a backup for the dev
        print(f"DEBUG: Verification code for {email} is {code}")
        return jsonify({'error': 'Email failed to send. Check Render logs.'}), 500
        
    return jsonify({'success': True})

@app.route('/api/auth/verify', methods=['POST'])
def verify_email():
    data = request.json
    email = data.get('email')
    code = data.get('code')
    
    v = verification_codes.find_one({'email': email, 'code': code})
    if not v or time.time() > v['expires_at']:
        return jsonify({'error': 'Invalid or expired code'}), 401
    
    users_collection.insert_one({
        'email': email, 
        'username': v['username'], 
        'password_hash': v['password_hash'],
        'created_at': time.time(), 
        'verified': True
    })
    verification_codes.delete_one({'_id': v['_id']})
    token, expiry = create_session(v['username'])
    return jsonify({'success': True, 'token': token, 'username': v['username']})

@app.route('/api/auth/login', methods=['POST'])
def login_user():
    data = request.json
    u = data.get('username')
    p = data.get('password')
    user = users_collection.find_one({'$or': [{'username': u}, {'email': u}]})
    if not user or user['password_hash'] != hash_password(p):
        return jsonify({'error': 'Invalid credentials'}), 401
    token, expiry = create_session(user['username'])
    return jsonify({'success': True, 'token': token, 'username': user['username']})

# ============= DISCORD CORE LOGIC =============

def create_session(username):
    token = generate_token()
    expiry = time.time() + (24 * 60 * 60)
    sessions_collection.insert_one({
        'token': token, 'username': username, 'expiry': expiry,
        'created_at': time.time(), 'last_used': time.time()
    })
    return token, expiry

def verify_user_token(req):
    auth = req.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '): return None
    token = auth.replace('Bearer ', '')
    session = sessions_collection.find_one({'token': token})
    if not session or time.time() > session['expiry']: return None
    return session

async def get_avatar_base64(user):
    try:
        if user.avatar:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(user.avatar.url) as resp:
                    if resp.status == 200:
                        return base64.b64encode(await resp.read()).decode('utf-8')
    except: pass
    return None

async def get_or_create_webhook(channel):
    if channel.id in webhooks_cache: return webhooks_cache[channel.id]
    webhooks = await channel.webhooks()
    webhook = next((wh for wh in webhooks if wh.user == bot.user), None)
    if not webhook:
        webhook = await channel.create_webhook(name="Voidagon Bridge")
    webhooks_cache[channel.id] = webhook
    return webhook

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    bot_status['ready'] = True
    bot_status['start_time'] = time.time()

@bot.event
async def on_message(message):
    if message.author.bot: return
    channel_id = str(message.channel.id)
    if channel_id not in message_cache:
        message_cache[channel_id] = deque(maxlen=MAX_CACHED_MESSAGES)
    
    avatar = await get_avatar_base64(message.author)
    msg_data = {
        'id': str(message.id), 'content': message.content,
        'author': {'username': message.author.name, 'avatar_base64': avatar},
        'timestamp': message.created_at.isoformat(), 'channel_id': channel_id
    }
    message_cache[channel_id].append(msg_data)
    await bot.process_commands(message)

# ============= DISCORD API ENDPOINTS =============

@app.route('/', methods=['GET'])
def index():
    return "<h1>NullChat Bridge Active</h1>"

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'guilds': [{'id': str(g.id), 'name': g.name} for g in bot.guilds]})

@app.route('/api/channels/<channel_id>/messages', methods=['GET'])
def get_messages(channel_id):
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'messages': list(message_cache.get(channel_id, []))})

@app.route('/api/channels/<channel_id>/send', methods=['POST'])
def send_message(channel_id):
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    
    async def web_send():
        channel = bot.get_channel(int(channel_id))
        webhook = await get_or_create_webhook(channel)
        await webhook.send(content=data.get('content'), username=session['username'])
        return {'success': True}

    future = asyncio.run_coroutine_threadsafe(web_send(), bot.loop)
    return jsonify(future.result(timeout=10))

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
