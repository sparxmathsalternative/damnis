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

# ================= CONFIGURATION =================
DISCORD_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
PORT = int(os.getenv('PORT', 5000))
MONGODB_URI = os.getenv('MONGODB')

if not DISCORD_TOKEN or not MONGODB_URI:
    raise ValueError("Missing environment variables DISCORD_BOT_TOKEN or MONGODB!")

# ================= DB & BOT SETUP =================
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client['nullchat']
users_collection = db['users']
sessions_collection = db['sessions']

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
app = Flask(__name__)
CORS(app)

# Cache Systems
message_cache = {} # Stores last 50 messages per channel
MAX_CACHED_MESSAGES = 50
start_time = time.time()

# ================= HELPERS =================

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    return secrets.token_urlsafe(32)

def generate_quick_code():
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])

def verify_user_token(req):
    """Checks for Quick Code (?code=) or Bearer Token (Header)"""
    # 1. Check Quick Code
    quick_code = req.args.get('code')
    if quick_code:
        user = users_collection.find_one({'quick_code': quick_code})
        if user: return {'username': user['username'], 'id': str(user['_id'])}

    # 2. Check Bearer Token
    auth = req.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '): return None
    token = auth.replace('Bearer ', '')
    session = sessions_collection.find_one({'token': token})
    if not session or time.time() > session['expiry']: return None
    return session

def create_session(username):
    token = generate_token()
    expiry = time.time() + (24 * 60 * 60) # 24 Hours
    sessions_collection.insert_one({'token': token, 'username': username, 'expiry': expiry})
    return token

# ================= DASHBOARD UI =================

UI_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Voidagon Bridge Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #0f0c29; color: white; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .card { background: #1a1a2e; padding: 30px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); width: 450px; text-align: center; position: relative; border: 1px solid #2a2a4e; }
        h1 { color: #5865F2; margin-bottom: 20px; }
        input { width: 100%; padding: 12px; margin: 10px 0; border-radius: 8px; border: none; background: #2a2a4e; color: white; border: 1px solid #3a3a5e; }
        button { width: 100%; padding: 14px; background: #5865F2; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; margin-top: 10px; transition: 0.3s; }
        button:hover { background: #4752C4; }
        .hidden { display: none; }
        .pfp-preview { width: 100px; height: 100px; border-radius: 50%; object-fit: cover; margin: 15px auto; border: 3px solid #5865F2; display: block; }
        .code-box { background: #16213e; padding: 20px; border-radius: 10px; font-size: 28px; letter-spacing: 6px; color: #00d2ff; margin: 20px 0; border: 1px dashed #5865F2; font-family: monospace; }
        .status-badge { position: absolute; top: 15px; right: 20px; font-size: 11px; display: flex; align-items: center; }
        .dot { width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
        .online { background: #43b581; box-shadow: 0 0 8px #43b581; }
        .offline { background: #f04747; box-shadow: 0 0 8px #f04747; }
        .info-text { font-size: 12px; color: #888; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div id="auth-card" class="card">
        <h1>🌌 Voidagon</h1>
        <div id="login-fields">
            <input type="text" id="l-user" placeholder="Username">
            <input type="password" id="l-pass" placeholder="Password">
            <button onclick="auth('login')">Sign In</button>
            <p onclick="swap(true)" style="font-size: 12px; margin-top: 15px; cursor: pointer; color: #888;">New here? Create Account</p>
        </div>
        <div id="reg-fields" class="hidden">
            <input type="email" id="r-email" placeholder="Email Address">
            <input type="text" id="r-user" placeholder="Choose Username">
            <input type="password" id="r-pass" placeholder="Create Password">
            <button onclick="auth('register')">Register Now</button>
            <p onclick="swap(false)" style="font-size: 12px; margin-top: 15px; cursor: pointer; color: #888;">Already have an account? Login</p>
        </div>
    </div>

    <div id="dash-card" class="card hidden">
        <div class="status-badge">
            <div id="s-dot" class="dot offline"></div> Discord: <span id="s-text" style="margin-left:4px">OFFLINE</span>
        </div>
        <h1>Dashboard</h1>
        <img id="u-pfp" class="pfp-preview" src="">
        <h2 id="u-name">User</h2>
        <input type="file" id="pfp-input" class="hidden" accept="image/*" onchange="upload()">
        <button onclick="document.getElementById('pfp-input').click()" style="background:#3a3a5e; font-size:12px; width: auto; padding: 8px 15px;">Update Avatar</button>
        
        <div class="code-box" id="u-code">000000</div>
        <p class="info-text">Quick Sign-in Code<br>Append <code>?code=xxxxxx</code> to API URLs</p>
        
        <button onclick="regen()" style="background: #e94560;">Regenerate Code</button>
        <button onclick="logout()" style="background: transparent; border: 1px solid #444; color: #888; margin-top: 25px;">Logout</button>
    </div>

    <script>
        const tok = localStorage.getItem('token');
        function swap(r) {
            document.getElementById('login-fields').className = r ? 'hidden' : '';
            document.getElementById('reg-fields').className = r ? '' : 'hidden';
        }

        async function auth(mode) {
            const payload = mode === 'login' ? 
                { username: document.getElementById('l-user').value, password: document.getElementById('l-pass').value } :
                { email: document.getElementById('r-email').value, username: document.getElementById('r-user').value, password: document.getElementById('r-pass').value };
            
            const r = await fetch('/api/auth/' + mode, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const d = await r.json();
            if(d.success) { localStorage.setItem('token', d.token); location.reload(); }
            else alert(d.error);
        }

        async function load() {
            if(!tok) return;
            const r = await fetch('/api/user/me', { headers: {'Authorization': 'Bearer '+tok} });
            const d = await r.json();
            if(d.error) return logout();

            document.getElementById('auth-card').classList.add('hidden');
            document.getElementById('dash-card').classList.remove('hidden');
            document.getElementById('u-name').innerText = d.username;
            document.getElementById('u-code').innerText = d.quick_code;
            document.getElementById('u-pfp').src = d.pfp ? 'data:image/png;base64,'+d.pfp : 'https://www.gravatar.com/avatar/0?d=mp';
            updateStatus();
            setInterval(updateStatus, 15000);
        }

        async function updateStatus() {
            const r = await fetch('/api/discord/status');
            const d = await r.json();
            document.getElementById('s-dot').className = d.status === 'up' ? 'dot online' : 'dot offline';
            document.getElementById('s-text').innerText = d.status.toUpperCase();
        }

        async function upload() {
            const f = document.getElementById('pfp-input').files[0];
            const reader = new FileReader();
            reader.onloadend = async () => {
                const b64 = reader.result.split(',')[1];
                await fetch('/api/user/update-pfp', {
                    method: 'POST', headers: {'Authorization': 'Bearer '+tok, 'Content-Type': 'application/json'},
                    body: JSON.stringify({pfp: b64})
                });
                location.reload();
            };
            reader.readAsDataURL(f);
        }

        async function regen() {
            await fetch('/api/user/regen-code', {method:'POST', headers:{'Authorization': 'Bearer '+tok}});
            location.reload();
        }

        function logout() { localStorage.clear(); location.reload(); }
        if(tok) load();
    </script>
</body>
</html>
"""

# ================= PUBLIC API ROUTES =================

@app.route('/')
def home(): return render_template_string(UI_HTML)

@app.route('/api/discord/status', methods=['GET'])
def discord_status():
    """No Auth Required"""
    return jsonify({'status': 'up' if bot.is_ready() else 'down'})

@app.route('/api/health', methods=['GET'])
def health():
    """No Auth Required"""
    return jsonify({
        'uptime': int(time.time() - start_time),
        'bot_ready': bot.is_ready(),
        'cached_channels': len(message_cache)
    })

# ================= AUTHENTICATION =================

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
    if not user: return jsonify({'error': 'Invalid credentials'}), 401
    return jsonify({'success': True, 'token': create_session(u)})

# ================= USER DATA =================

@app.route('/api/user/me')
def me():
    s = verify_user_token(request)
    if not s: return jsonify({'error': 'Unauthorized'}), 401
    u = users_collection.find_one({'username': s['username']})
    return jsonify({'username': u['username'], 'quick_code': u['quick_code'], 'pfp': u.get('pfp')})

@app.route('/api/user/update-pfp', methods=['POST'])
def set_pfp():
    s = verify_user_token(request)
    if not s: return jsonify({'error': 'Unauthorized'}), 401
    users_collection.update_one({'username': s['username']}, {'$set': {'pfp': request.json.get('pfp')}})
    return jsonify({'success': True})

@app.route('/api/user/regen-code', methods=['POST'])
def regen():
    s = verify_user_token(request)
    if not s: return jsonify({'error': 'Unauthorized'}), 401
    c = generate_quick_code()
    users_collection.update_one({'username': s['username']}, {'$set': {'quick_code': c}})
    return jsonify({'success': True, 'code': c})

# ================= DISCORD BRIDGE =================

@app.route('/api/guilds')
def guilds():
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'guilds': [{'id': str(g.id), 'name': g.name} for g in bot.guilds]})

@app.route('/api/guilds/<gid>/channels')
def channels(gid):
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    g = bot.get_guild(int(gid))
    if not g: return jsonify({'error': 'Guild not found'}), 404
    return jsonify({'channels': [{'id': str(c.id), 'name': c.name} for c in g.text_channels]})

@app.route('/api/channels/<cid>/messages')
def get_msgs(cid):
    if not verify_user_token(request): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'messages': list(message_cache.get(cid, []))})

@app.route('/api/channels/<cid>/send', methods=['POST'])
def send(cid):
    s = verify_user_token(request)
    if not s: return jsonify({'error': 'Unauthorized'}), 401
    
    user_db = users_collection.find_one({'username': s['username']})
    
    async def task():
        channel = bot.get_channel(int(cid))
        if not channel: return {'error': 'Invalid Channel'}
        
        webhooks = await channel.webhooks()
        webhook = next((wh for wh in webhooks if wh.user == bot.user), None)
        if not webhook: webhook = await channel.create_webhook(name="Voidagon Bridge")
        
        pfp = user_db.get('pfp')
        await webhook.send(
            content=request.json.get('content'),
            username=s['username'],
            avatar_url=f"data:image/png;base64,{pfp}" if pfp else None
        )
        return {'success': True}

    res = asyncio.run_coroutine_threadsafe(task(), bot.loop)
    return jsonify(res.result(timeout=10))

# ================= BOT EVENTS =================

@bot.event
async def on_message(msg):
    if msg.author.bot: return
    cid = str(msg.channel.id)
    if cid not in message_cache: message_cache[cid] = deque(maxlen=50)
    message_cache[cid].append({
        'author': msg.author.name,
        'content': msg.content,
        'time': datetime.now().strftime("%H:%M")
    })

def flask_run():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    threading.Thread(target=flask_run, daemon=True).start()
    bot.run(DISCORD_TOKEN)
