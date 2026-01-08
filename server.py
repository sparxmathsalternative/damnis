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
import aiohttp
from collections import deque
from pymongo import MongoClient
from datetime import datetime

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
PORT = int(os.getenv('PORT', 5000))
MONGODB_URI = os.getenv('MONGODB')

# MongoDB setup
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client['nullchat']
users_collection = db['users']
sessions_collection = db['sessions']

# Bot Setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
app = Flask(__name__)
CORS(app)

# Global Cache
message_cache = {}
MAX_CACHED_MESSAGES = 50
webhooks_cache = {}
start_time = time.time()

# ============= HELPERS =============

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    return secrets.token_urlsafe(32)

def generate_quick_code():
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

def verify_user_token(req):
    # Check for Quick Code in URL params first
    quick_code = req.args.get('code')
    if quick_code:
        user = users_collection.find_one({'quick_code': quick_code})
        if user: return {'username': user['username'], 'id': str(user['_id'])}

    # Check for Bearer Token in Headers
    auth = req.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '): return None
    token = auth.replace('Bearer ', '')
    session = sessions_collection.find_one({'token': token})
    if not session or time.time() > session['expiry']: return None
    return session

def create_session(username):
    token = generate_token()
    expiry = time.time() + (24 * 60 * 60) # 24 Hour session
    sessions_collection.insert_one({'token': token, 'username': username, 'expiry': expiry})
    return token

# ============= CORE API ROUTES =============

@app.route('/')
def index():
    # Serves the Dashboard UI (Same as previous version)
    return render_template_string(UI_HTML)

@app.route('/api/discord/status', methods=['GET'])
def get_discord_status():
    """COMPLETELY PUBLIC: Returns 'up' or 'down'"""
    return jsonify({'status': 'up' if bot.is_ready() else 'down'})

@app.route('/api/health', methods=['GET'])
def health_check():
    """Detailed system health"""
    return jsonify({
        'status': 'healthy',
        'uptime': int(time.time() - start_time),
        'discord_latency': round(bot.latency * 1000, 2) if bot.is_ready() else 0,
        'db_connected': True if mongo_client.server_info() else False
    })

# ============= AUTH & USER ROUTES =============

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    u, e, p = data.get('username'), data.get('email'), data.get('password')
    if not u or not e or not p: return jsonify({'error': 'Missing fields'}), 400
    if users_collection.find_one({'$or': [{'username': u}, {'email': e}]}):
        return jsonify({'error': 'Username or Email already taken'}), 400
    
    users_collection.insert_one({
        'username': u, 'email': e, 'password_hash': hash_password(p),
        'quick_code': generate_quick_code(), 'pfp': None, 'created_at': time.time()
    })
    return jsonify({'success': True, 'token': create_session(u)})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    u, p = data.get('username'), data.get('password')
    user = users_collection.find_one({'username': u, 'password_hash': hash_password(p)})
    if not user: return jsonify({'error': 'Invalid credentials'}), 401
    return jsonify({'success': True, 'token': create_session(u)})

@app.route('/api/user/me')
def get_me():
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    user = users_collection.find_one({'username': session['username']})
    return jsonify({
        'username': user['username'], 
        'quick_code': user['quick_code'], 
        'pfp': user.get('pfp')
    })

@app.route('/api/user/update-pfp', methods=['POST'])
def update_pfp():
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    users_collection.update_one({'username': session['username']}, {'$set': {'pfp': request.json.get('pfp')}})
    return jsonify({'success': True})

@app.route('/api/user/regen-code', methods=['POST'])
def regen_code():
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    new_code = generate_quick_code()
    users_collection.update_one({'username': session['username']}, {'$set': {'quick_code': new_code}})
    return jsonify({'success': True, 'code': new_code})

# ============= DISCORD DATA ROUTES =============

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    guilds = [{'id': str(g.id), 'name': g.name, 'icon': str(g.icon.url) if g.icon else None} for g in bot.guilds]
    return jsonify({'guilds': guilds})

@app.route('/api/guilds/<guild_id>/channels', methods=['GET'])
def get_channels(guild_id):
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    guild = bot.get_guild(int(guild_id))
    if not guild: return jsonify({'error': 'Guild not found'}), 404
    channels = [{'id': str(c.id), 'name': c.name} for c in guild.text_channels]
    return jsonify({'channels': channels})

@app.route('/api/channels/<channel_id>/messages', methods=['GET'])
def get_messages(channel_id):
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    msgs = list(message_cache.get(str(channel_id), []))
    return jsonify({'messages': msgs})

@app.route('/api/channels/<channel_id>/send', methods=['POST'])
def send_msg(channel_id):
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    user_db = users_collection.find_one({'username': session['username']})
    
    async def run():
        channel = bot.get_channel(int(channel_id))
        if not channel: return {'error': 'Channel not found'}
        
        webhooks = await channel.webhooks()
        webhook = next((wh for wh in webhooks if wh.user == bot.user), None)
        if not webhook: webhook = await channel.create_webhook(name="Voidagon Bridge")
        
        pfp = user_db.get('pfp')
        await webhook.send(
            content=data.get('content'),
            username=session['username'],
            avatar_url=f"data:image/png;base64,{pfp}" if pfp else None
        )
        return {'success': True}

    future = asyncio.run_coroutine_threadsafe(run(), bot.loop)
    return jsonify(future.result(timeout=10))

# ============= DISCORD EVENTS =============

@bot.event
async def on_message(message):
    if message.author.bot: return
    cid = str(message.channel.id)
    if cid not in message_cache: message_cache[cid] = deque(maxlen=50)
    
    message_cache[cid].append({
        'id': str(message.id),
        'author': message.author.name,
        'content': message.content,
        'timestamp': message.created_at.isoformat()
    })

# ============= UI HTML (Same as previous for consistency) =============
UI_HTML = """... (Include Dashboard HTML from previous response here) ..."""

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    return secrets.token_urlsafe(32)

def generate_quick_code():
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

def verify_user_token(req):
    # Support both Bearer Token AND Quick Code via Query Params
    quick_code = req.args.get('code')
    if quick_code:
        user = users_collection.find_one({'quick_code': quick_code})
        if user: return {'username': user['username'], 'is_quick': True}

    auth = req.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '): return None
    token = auth.replace('Bearer ', '')
    session = sessions_collection.find_one({'token': token})
    if not session or time.time() > session['expiry']: return None
    return session

def create_session(username):
    token = generate_token()
    expiry = time.time() + (24 * 60 * 60)
    sessions_collection.insert_one({
        'token': token, 'username': username, 'expiry': expiry
    })
    return token

# ============= DASHBOARD & REG HTML =============

UI_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Voidagon Bridge</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f0c29; color: white; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .card { background: #1a1a2e; padding: 30px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); width: 400px; text-align: center; }
        input { width: 90%; padding: 12px; margin: 10px 0; border-radius: 5px; border: none; }
        button { width: 100%; padding: 12px; background: #5865F2; color: white; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; margin-top: 10px; }
        .hidden { display: none; }
        .pfp-preview { width: 80px; height: 80px; border-radius: 50%; object-fit: cover; margin: 10px auto; border: 2px solid #5865F2; }
        .code-box { background: #16213e; padding: 15px; border-radius: 8px; font-size: 24px; letter-spacing: 5px; color: #00d2ff; margin: 15px 0; }
    </style>
</head>
<body>
    <div id="auth-section" class="card">
        <h1>🌌 Voidagon</h1>
        <div id="login-form">
            <input type="text" id="user-in" placeholder="Username">
            <input type="password" id="pass-in" placeholder="Password">
            <button onclick="handleAuth('login')">Login</button>
            <p onclick="toggleAuth()" style="cursor:pointer; font-size: 12px; margin-top:15px;">No account? Register</p>
        </div>
        <div id="reg-form" class="hidden">
            <input type="email" id="reg-email" placeholder="Email">
            <input type="text" id="reg-user" placeholder="Username">
            <input type="password" id="reg-pass" placeholder="Password">
            <button onclick="handleAuth('register')">Create Account</button>
            <p onclick="toggleAuth()" style="cursor:pointer; font-size: 12px; margin-top:15px;">Have account? Login</p>
        </div>
    </div>

    <div id="dash-section" class="card hidden">
        <h1>Dashboard</h1>
        <img id="dash-pfp" class="pfp-preview" src="">
        <h3 id="dash-welcome"></h3>
        
        <div style="margin: 20px 0; text-align: left; font-size: 13px;">
            <label>Update Profile Picture:</label>
            <input type="file" id="pfp-upload" accept="image/*" onchange="uploadPfp()">
        </div>

        <div class="code-box" id="quick-code-display">------</div>
        <p style="font-size: 11px; color: #888;">Your Quick Sign-in Code<br>Use <code>?code=YOURCODE</code> in API requests</p>
        <button onclick="regenCode()" style="background: #e94560;">Regenerate Code</button>
        <button onclick="logout()" style="background: transparent; border: 1px solid #555; margin-top: 20px;">Logout</button>
    </div>

    <script>
        let token = localStorage.getItem('token');
        
        async function handleAuth(type) {
            const body = type === 'register' ? 
                { email: document.getElementById('reg-email').value, username: document.getElementById('reg-user').value, password: document.getElementById('reg-pass').value } :
                { username: document.getElementById('user-in').value, password: document.getElementById('pass-in').value };

            const res = await fetch('/api/auth/' + type, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json();
            if(data.success) {
                localStorage.setItem('token', data.token);
                location.reload();
            } else alert(data.error);
        }

        async function loadDash() {
            if(!token) return;
            const res = await fetch('/api/user/me', { headers: { 'Authorization': 'Bearer ' + token } });
            const user = await res.json();
            if(user.error) return logout();

            document.getElementById('auth-section').classList.add('hidden');
            document.getElementById('dash-section').classList.remove('hidden');
            document.getElementById('dash-welcome').innerText = "Hello, " + user.username;
            document.getElementById('quick-code-display').innerText = user.quick_code;
            if(user.pfp) document.getElementById('dash-pfp').src = "data:image/png;base64," + user.pfp;
        }

        async function uploadPfp() {
            const file = document.getElementById('pfp-upload').files[0];
            const reader = new FileReader();
            reader.onloadend = async () => {
                const base64String = reader.result.split(',')[1];
                await fetch('/api/user/update-pfp', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pfp: base64String })
                });
                location.reload();
            };
            reader.readAsDataURL(file);
        }

        async function regenCode() {
            await fetch('/api/user/regen-code', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token } });
            location.reload();
        }

        function toggleAuth() {
            document.getElementById('login-form').classList.toggle('hidden');
            document.getElementById('reg-form').classList.toggle('hidden');
        }

        function logout() { localStorage.clear(); location.reload(); }
        if(token) loadDash();
    </script>
</body>
</html>
"""

# ============= API ROUTES =============

@app.route('/')
def home(): return render_template_string(UI_HTML)

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    u, e, p = data.get('username'), data.get('email'), data.get('password')
    if users_collection.find_one({'$or': [{'username': u}, {'email': e}]}):
        return jsonify({'error': 'User already exists'}), 400
    
    users_collection.insert_one({
        'username': u, 'email': e, 'password_hash': hash_password(p),
        'quick_code': generate_quick_code(), 'pfp': None
    })
    return jsonify({'success': True, 'token': create_session(u)})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    u, p = data.get('username'), data.get('password')
    user = users_collection.find_one({'username': u, 'password_hash': hash_password(p)})
    if not user: return jsonify({'error': 'Invalid login'}), 401
    return jsonify({'success': True, 'token': create_session(u)})

@app.route('/api/user/me')
def get_me():
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    user = users_collection.find_one({'username': session['username']})
    return jsonify({'username': user['username'], 'quick_code': user['quick_code'], 'pfp': user.get('pfp')})

@app.route('/api/user/update-pfp', methods=['POST'])
def update_pfp():
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    users_collection.update_one({'username': session['username']}, {'$set': {'pfp': request.json.get('pfp')}})
    return jsonify({'success': True})

@app.route('/api/user/regen-code', methods=['POST'])
def regen_code():
    session = verify_user_token(request)
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    new_code = generate_quick_code()
    users_collection.update_one({'username': session['username']}, {'$set': {'quick_code': new_code}})
    return jsonify({'success': True, 'code': new_code})

# ============= DISCORD LOGIC =============

@app.route('/api/channels/<channel_id>/send', methods=['POST'])
def send_msg(channel_id):
    session = verify_user_token(request) # Works with Bearer OR ?code=
    if not session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    user_db = users_collection.find_one({'username': session['username']})
    
    async def run():
        channel = bot.get_channel(int(channel_id))
        if not channel: return {'error': 'Channel not found'}
        
        # Webhook Logic
        webhooks = await channel.webhooks()
        webhook = next((wh for wh in webhooks if wh.user == bot.user), None)
        if not webhook: webhook = await channel.create_webhook(name="Voidagon")
        
        pfp_data = user_db.get('pfp')
        await webhook.send(
            content=data.get('content'),
            username=session['username'],
            avatar_url=f"data:image/png;base64,{pfp_data}" if pfp_data else None
        )
        return {'success': True}

    future = asyncio.run_coroutine_threadsafe(run(), bot.loop)
    return jsonify(future.result(timeout=10))

# Standard Bot events and cache...
@bot.event
async def on_ready(): print(f'Bot {bot.user} Ready')

@bot.event
async def on_message(message):
    if message.author.bot: return
    cid = str(message.channel.id)
    if cid not in message_cache: message_cache[cid] = deque(maxlen=50)
    message_cache[cid].append({'author': message.author.name, 'content': message.content})

def run_flask(): app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
