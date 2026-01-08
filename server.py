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
    """Send verification code via email - Updated for Render (Port 587)"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'NullChat - Verify Your Email'
        msg['From'] = f"NullChat <{APP_EMAIL}>"
        msg['To'] = email
        
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap');
                body {{ margin: 0; padding: 20px; background-color: #f8f9fa; font-family: 'IBM Plex Mono', monospace, Arial, sans-serif; }}
                .email-container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08); border: 1px solid #e9ecef; }}
                .header {{ padding: 30px 40px 20px; text-align: center; background: linear-gradient(135deg, #ff0d9e 0%, #0019a3 100%); color: white; }}
                .nullchat-logo {{ font-size: 42px; font-weight: 600; letter-spacing: -1px; margin: 0; line-height: 1; }}
                .content {{ padding: 40px; color: #333333; line-height: 1.6; }}
                .verification-section {{ background: #f8f9ff; border: 2px solid #e0e7ff; border-radius: 8px; padding: 25px; margin: 30px 0; text-align: center; }}
                .verification-code {{ font-size: 42px; font-weight: 600; letter-spacing: 10px; color: #0019a3; margin: 15px 0; padding: 10px; background: white; border-radius: 6px; border: 1px solid #d0d7ff; }}
                .footer {{ padding: 25px 40px; background-color: #f8f9fa; border-top: 1px solid #e9ecef; text-align: center; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="header"><h1 class="nullchat-logo">nullchat</h1></div>
                <div class="content">
                    <p>Thank you for creating your NullChat account! To complete your setup, please verify your email address.</p>
                    <div class="verification-section">
                        <div style="font-size: 14px; color: #666; text-transform: uppercase;">Verification Code</div>
                        <div class="verification-code">{code}</div>
                        <div style="color: #ff0d9e; font-weight: 500;">⏰ Expires in 15 minutes</div>
                    </div>
                </div>
                <div class="footer"><p>Discord-to-web bridge service</p></div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        # Render Standard: Connect to 587 and use STARTTLS
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(APP_EMAIL, APP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"RENDER EMAIL ERROR: {e}")
        return False

def verify_user_token(req):
    """Verify user session token"""
    auth = req.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '):
        return None
    token = auth.replace('Bearer ', '')
    session = sessions_collection.find_one({'token': token})
    if not session:
        return None
    if time.time() > session['expiry']:
        sessions_collection.delete_one({'token': token})
        return None
    sessions_collection.update_one({'token': token}, {'$set': {'last_used': time.time()}})
    return session

def create_session(username):
    """Create a new session token for user"""
    token = generate_token()
    expiry = time.time() + (24 * 60 * 60)
    sessions_collection.insert_one({
        'token': token, 'username': username, 'expiry': expiry,
        'created_at': time.time(), 'last_used': time.time()
    })
    return token, expiry

async def get_avatar_base64(user):
    """Get user avatar as base64"""
    try:
        if user.avatar:
            async with aiohttp.ClientSession() as session:
                async with session.get(user.avatar.url) as resp:
                    if resp.status == 200:
                        return base64.b64encode(await resp.read()).decode('utf-8')
        return None
    except: return None

async def get_or_create_webhook(channel):
    """Get existing webhook or create new one for channel"""
    if channel.id in webhooks_cache:
        return webhooks_cache[channel.id]
    webhooks = await channel.webhooks()
    webhook = next((wh for wh in webhooks if wh.user == bot.user), None)
    if not webhook:
        webhook = await channel.create_webhook(name="Voidagon Bridge")
    webhooks_cache[channel.id] = webhook
    return webhook

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    bot_status['ready'] = True
    bot_status['start_time'] = time.time()

@bot.event
async def on_message(message):
    if message.author.bot: return
    bot_status['last_message_time'] = time.time()
    channel_id = str(message.channel.id)
    if channel_id not in message_cache:
        message_cache[channel_id] = deque(maxlen=MAX_CACHED_MESSAGES)
    
    avatar_b64 = await get_avatar_base64(message.author)
    roles = []
    if hasattr(message.author, 'roles'):
        roles = [{'id': str(r.id), 'name': r.name, 'color': str(r.color)} 
                 for r in message.author.roles if r.name != '@everyone']
    
    msg_data = {
        'id': str(message.id), 'content': message.content,
        'author': {
            'id': str(message.author.id), 'username': message.author.name,
            'display_name': message.author.display_name, 'avatar_base64': avatar_b64,
            'roles': roles
        },
        'timestamp': message.created_at.isoformat(), 'channel_id': channel_id,
        'guild_id': str(message.guild.id) if message.guild else None
    }
    message_cache[channel_id].append(msg_data)
    await bot.process_commands(message)

# ============= WEB REGISTRATION PAGE =============

REGISTRATION_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Voidagon - Create Account</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .container { background: white; padding: 40px; border-radius: 15px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); max-width: 450px; width: 100%; }
        h1 { color: #5865F2; margin-bottom: 10px; font-size: 32px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 5px; color: #333; font-weight: 600; font-size: 14px; }
        input { width: 100%; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 14px; }
        button { width: 100%; padding: 14px; background: #5865F2; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }
        .message { padding: 12px; border-radius: 8px; margin-bottom: 20px; display: none; }
        .message.error { background: #fee; color: #c33; border: 1px solid #fcc; }
        .message.success { background: #efe; color: #3c3; border: 1px solid #cfc; }
        .step { display: none; }
        .step.active { display: block; }
        .code-input { font-size: 24px; text-align: center; letter-spacing: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌌 Voidagon</h1>
        <p class="subtitle">Create your account to access Discord bridge</p>
        <div id="message" class="message"></div>
        
        <div id="step1" class="step active">
            <form id="signupForm">
                <div class="form-group"><label>Email Address</label><input type="email" id="email" required></div>
                <div class="form-group"><label>Username</label><input type="text" id="username" required minlength="3"></div>
                <div class="form-group"><label>Password</label><input type="password" id="password" required minlength="6"></div>
                <button type="submit" id="signupBtn">Create Account</button>
            </form>
        </div>
        
        <div id="step2" class="step">
            <p style="margin-bottom: 20px;">Code sent to <strong id="emailDisplay"></strong></p>
            <form id="verifyForm">
                <div class="form-group"><input type="text" id="code" required maxlength="6" class="code-input"></div>
                <button type="submit">Verify & Login</button>
            </form>
        </div>
        
        <div id="step3" class="step">
            <div style="text-align: center;">
                <h2 style="color: #3c3;">✅ Account Created!</h2>
                <div style="background: #f5f5f5; padding: 15px; border-radius: 8px; margin-top: 20px;">
                    <strong>Your Access Token:</strong>
                    <div style="background: white; padding: 10px; margin-top: 10px; word-break: break-all; font-family: monospace; font-size: 12px;" id="tokenDisplay"></div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentEmail = '';
        function showMessage(text, type) {
            const msg = document.getElementById('message');
            msg.textContent = text; msg.className = 'message ' + type; msg.style.display = 'block';
        }
        function showStep(step) {
            document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
            document.getElementById('step' + step).classList.add('active');
        }
        document.getElementById('signupForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('signupBtn');
            btn.disabled = true;
            try {
                const res = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        email: document.getElementById('email').value,
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value
                    })
                });
                if (res.ok) {
                    currentEmail = document.getElementById('email').value;
                    document.getElementById('emailDisplay').textContent = currentEmail;
                    showStep(2); showMessage('Check your email!', 'success');
                } else {
                    const data = await res.json(); showMessage(data.error, 'error');
                    btn.disabled = false;
                }
            } catch (err) { showMessage('Network error', 'error'); btn.disabled = false; }
        });
        document.getElementById('verifyForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            try {
                const res = await fetch('/api/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email: currentEmail, code: document.getElementById('code').value})
                });
                const data = await res.json();
                if (res.ok) {
                    document.getElementById('tokenDisplay').textContent = data.token;
                    showStep(3);
                } else { showMessage(data.error, 'error'); }
            } catch (err) { showMessage('Network error', 'error'); }
        });
    </script>
</body>
</html>
"""

# ============= ENDPOINTS =============

@app.route('/', methods=['GET'])
def registration_page():
    return render_template_string(REGISTRATION_HTML)

@app.route('/api/auth/register', methods=['POST'])
def register_user():
    data = request.json
    email, username, password = data.get('email'), data.get('username'), data.get('password')
    if not email or not username or not password:
        return jsonify({'error': 'All fields required'}), 400
    if users_collection.find_one({'$or': [{'email': email}, {'username': username}]}):
        return jsonify({'error': 'Email or username already exists'}), 400
    
    code = generate_verification_code()
    verification_codes.delete_many({'email': email})
    verification_codes.insert_one({
        'email': email, 'username': username, 'password_hash': hash_password(password),
        'code': code, 'expires_at': time.time() + 900
    })
    
    if not send_verification_email(email, code):
        print(f"DEBUG: Code for {email} is {code}") # Backup for Render logs
        return jsonify({'error': 'Failed to send email. Code printed to logs.'}), 500
    return jsonify({'success': True})

@app.route('/api/auth/verify', methods=['POST'])
def verify_email():
    data = request.json
    email, code = data.get('email'), data.get('code')
    v = verification_codes.find_one({'email': email, 'code': code})
    if not v or time.time() > v['expires_at']:
        return jsonify({'error': 'Invalid code or expired'}), 401
    
    users_collection.insert_one({
        'email': email, 'username': v['username'], 'password_hash': v['password_hash'],
        'created_at': time.time(), 'verified': True
    })
    verification_codes.delete_one({'_id': v['_id']})
    token, expiry = create_session(v['username'])
    return jsonify({'success': True, 'token': token, 'username': v['username']})

@app.route('/api/auth/login', methods=['POST'])
def login_user():
    data = request.json
    u, p = data.get('username'), data.get('password')
    user = users_collection.find_one({'$or': [{'username': u}, {'email': u}]})
    if not user or user['password_hash'] != hash_password(p):
        return jsonify({'error': 'Invalid credentials'}), 401
    token, expiry = create_session(user['username'])
    return jsonify({'success': True, 'token': token, 'username': user['username']})

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    guilds = [{'id': str(g.id), 'name': g.name, 'icon_url': str(g.icon.url) if g.icon else None} for g in bot.guilds]
    return jsonify({'guilds': guilds})

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
        try:
            channel = bot.get_channel(int(channel_id))
            webhook = await get_or_create_webhook(channel)
            await webhook.send(content=data.get('content'), username=session['username'], avatar_url=data.get('avatar_url'))
            return {'success': True}
        except Exception as e: return {'error': str(e)}

    future = asyncio.run_coroutine_threadsafe(web_send(), bot.loop)
    return jsonify(future.result(timeout=10))

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
