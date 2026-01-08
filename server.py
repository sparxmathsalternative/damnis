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
    'start_time': @app.route('/api/auth/register', methods=['POST'])
def register_user():
    """Register a new user and send verification email"""
    try:
        data = request.json
        email = data.get('email')
        username = data.get('username')
        password = data.get('password')
        
        if not email or not username or not password:
            return jsonify({'error': 'All fields required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        if len(username) < 3:
            return jsonify({'error': 'Username must be at least 3 characters'}), 400
        
        # Check if user already exists
        existing = users_collection.find_one({'$or': [{'email': email}, {'username': username}]})
        if existing:
            return jsonify({'error': 'Email or username already exists'}), 400
        
        # Generate verification code
        code = generate_verification_code()
        
        # Store pending verification
        verification_codes.delete_many({'email': email})
        verification_codes.insert_one({
            'email': email,
            'username': username,
            'password_hash': hash_password(password),
            'code': code,
            'created_at': time.time(),
            'expires_at': time.time() + (15 * 60)
        })
        
        # Send email in background thread so it doesn't block
        def send_in_background():
            try:
                send_verification_email(email, code)
            except Exception as e:
                print(f"Background email error: {e}")
        
        import threading
        email_thread = threading.Thread(target=send_in_background)
        email_thread.daemon = True
        email_thread.start()
        
        return jsonify({'success': True, 'message': 'Verification code sent to email'})
    
    except Exception as e:
        print(f"ERROR in register_user: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500,
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
    """Send verification code via email"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'NullChat - Verify Your Email'
        msg['From'] = APP_EMAIL
        msg['To'] = email
        
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap');
                
                body {{
                    margin: 0;
                    padding: 20px;
                    background-color: #f8f9fa;
                    font-family: 'IBM Plex Mono', monospace, Arial, sans-serif;
                }}
                
                .email-container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
                    border: 1px solid #e9ecef;
                }}
                
                .header {{
                    padding: 30px 40px 20px;
                    text-align: center;
                    background: linear-gradient(135deg, #ff0d9e 0%, #0019a3 100%);
                    color: white;
                }}
                
                .nullchat-logo {{
                    font-family: 'IBM Plex Mono', monospace;
                    font-size: 42px;
                    font-weight: 600;
                    letter-spacing: -1px;
                    margin: 0;
                    line-height: 1;
                }}
                
                .tagline {{
                    font-size: 14px;
                    opacity: 0.9;
                    margin-top: 8px;
                    font-weight: 400;
                }}
                
                .content {{
                    padding: 40px;
                    color: #333333;
                    line-height: 1.6;
                }}
                
                .greeting {{
                    font-size: 18px;
                    margin-bottom: 25px;
                    color: #0019a3;
                    font-weight: 500;
                }}
                
                .verification-section {{
                    background: #f8f9ff;
                    border: 2px solid #e0e7ff;
                    border-radius: 8px;
                    padding: 25px;
                    margin: 30px 0;
                    text-align: center;
                }}
                
                .code-label {{
                    font-size: 14px;
                    color: #666;
                    margin-bottom: 10px;
                    text-transform: uppercase;
                    letter-spacing: 1px;
                }}
                
                .verification-code {{
                    font-family: 'IBM Plex Mono', monospace;
                    font-size: 42px;
                    font-weight: 600;
                    letter-spacing: 10px;
                    color: #0019a3;
                    margin: 15px 0;
                    padding: 10px;
                    background: white;
                    border-radius: 6px;
                    border: 1px solid #d0d7ff;
                }}
                
                .expiry-note {{
                    font-size: 14px;
                    color: #ff0d9e;
                    margin-top: 15px;
                    font-weight: 500;
                }}
                
                .highlight-box {{
                    background: linear-gradient(135deg, #ff0d9e08 0%, #0019a308 100%);
                    border-left: 4px solid #0019a3;
                    padding: 20px;
                    margin: 30px 0;
                    font-size: 14px;
                    line-height: 1.7;
                }}
                
                .highlight-title {{
                    color: #0019a3;
                    font-weight: 600;
                    margin-bottom: 8px;
                }}
                
                .steps {{
                    margin: 30px 0;
                    padding-left: 20px;
                }}
                
                .steps li {{
                    margin-bottom: 15px;
                    padding-left: 10px;
                }}
                
                .warning {{
                    background-color: #fff5f7;
                    border: 1px solid #ffe3e9;
                    padding: 15px;
                    border-radius: 6px;
                    margin: 25px 0;
                    font-size: 14px;
                }}
                
                .footer {{
                    padding: 25px 40px;
                    background-color: #f8f9fa;
                    border-top: 1px solid #e9ecef;
                    text-align: center;
                    font-size: 12px;
                    color: #666;
                    line-height: 1.5;
                }}
                
                .footer-logo {{
                    font-family: 'IBM Plex Mono', monospace;
                    font-size: 18px;
                    font-weight: 600;
                    background: linear-gradient(135deg, #ff0d9e 0%, #0019a3 100%);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    margin-bottom: 10px;
                }}
                
                .app-description {{
                    font-size: 13px;
                    margin: 15px 0;
                    color: #555;
                }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="header">
                    <h1 class="nullchat-logo">nullchat</h1>
                    <div class="tagline">Discord-to-web bridge</div>
                </div>
                
                <div class="content">
                    <div class="greeting">
                        Hello,
                    </div>
                    
                    <p>Thank you for creating your NullChat account! To complete your setup and start using our Discord-to-web bridge service, please verify your email address.</p>
                    
                    <div class="verification-section">
                        <div class="code-label">Verification Code</div>
                        <div class="verification-code">{code}</div>
                        <div class="expiry-note">⏰ Expires in 15 minutes</div>
                    </div>
                    
                    <p>Enter this code in the NullChat app to activate your account.</p>
                    
                    <div class="highlight-box">
                        <div class="highlight-title">
                            ✨ Why NullChat?
                        </div>
                        Access Discord even when it's blocked. Our web client bridges your Discord experience to any browser, giving you uninterrupted access to your communities.
                    </div>
                    
                    <p><strong>Next steps:</strong></p>
                    <ol class="steps">
                        <li>Return to NullChat app/web client</li>
                        <li>Enter the verification code above</li>
                        <li>Start chatting through our secure web bridge</li>
                    </ol>
                    
                    <div class="warning">
                        <strong>⚠️ Didn't request this?</strong><br>
                        If you didn't create a NullChat account, please ignore this email.
                    </div>
                </div>
                
                <div class="footer">
                    <div class="footer-logo">nullchat</div>
                    <div class="app-description">
                        Discord-to-web bridge service<br>
                        Access Discord anywhere, even when blocked
                    </div>
                    <p style="font-size: 11px; color: #888; margin-top: 15px;">
                        This email was sent to {email} as part of the NullChat account creation process.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        # Set timeout to prevent hanging
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
            server.login(APP_EMAIL, APP_PASS)
            server.send_message(msg)
        
        print(f"✓ Email sent successfully to {email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"✗ Email authentication failed: {e}")
        print(f"Check APP_EMAIL and APP_PASS environment variables")
        return False
    except smtplib.SMTPException as e:
        print(f"✗ SMTP error: {e}")
        return False
    except Exception as e:
        print(f"✗ Email error: {e}")
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
    
    # Check if token expired
    if time.time() > session['expiry']:
        sessions_collection.delete_one({'token': token})
        return None
    
    # Update last used time
    sessions_collection.update_one(
        {'token': token},
        {'$set': {'last_used': time.time()}}
    )
    
    return session

def create_session(username):
    """Create a new session token for user"""
    token = generate_token()
    expiry = time.time() + (24 * 60 * 60)  # 24 hours
    
    sessions_collection.insert_one({
        'token': token,
        'username': username,
        'expiry': expiry,
        'created_at': time.time(),
        'last_used': time.time()
    })
    
    return token, expiry

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
    
    for wh in webhooks:
        if wh.user == bot.user:
            webhook = wh
            break
    
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

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
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
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 450px;
            width: 100%;
        }
        h1 {
            color: #5865F2;
            margin-bottom: 10px;
            font-size: 32px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #333;
            font-weight: 600;
            font-size: 14px;
        }
        input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
            transition: border 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #5865F2;
        }
        button {
            width: 100%;
            padding: 14px;
            background: #5865F2;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.3s;
        }
        button:hover {
            background: #4752C4;
        }
        button:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        .message {
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }
        .message.error {
            background: #fee;
            color: #c33;
            border: 1px solid #fcc;
        }
        .message.success {
            background: #efe;
            color: #3c3;
            border: 1px solid #cfc;
        }
        .step {
            display: none;
        }
        .step.active {
            display: block;
        }
        .code-input {
            font-size: 24px;
            text-align: center;
            letter-spacing: 10px;
        }
        .back-link {
            text-align: center;
            margin-top: 15px;
        }
        .back-link a {
            color: #5865F2;
            text-decoration: none;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌌 Voidagon</h1>
        <p class="subtitle">Create your account to access Discord bridge</p>
        
        <div id="message" class="message"></div>
        
        <!-- Step 1: Email & Password -->
        <div id="step1" class="step active">
            <form id="signupForm">
                <div class="form-group">
                    <label for="email">Email Address</label>
                    <input type="email" id="email" required placeholder="your@email.com">
                </div>
                <div class="form-group">
                    <label for="username">Username</label>
                    <input type="text" id="username" required placeholder="Choose a username" minlength="3">
                </div>
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" required placeholder="At least 6 characters" minlength="6">
                </div>
                <button type="submit" id="signupBtn">Create Account</button>
            </form>
        </div>
        
        <!-- Step 2: Verification Code -->
        <div id="step2" class="step">
            <p style="margin-bottom: 20px; color: #666;">We've sent a verification code to <strong id="emailDisplay"></strong></p>
            <form id="verifyForm">
                <div class="form-group">
                    <label for="code">Verification Code</label>
                    <input type="text" id="code" required placeholder="000000" maxlength="6" class="code-input">
                </div>
                <button type="submit" id="verifyBtn">Verify & Login</button>
            </form>
            <div class="back-link">
                <a href="#" id="resendCode">Resend code</a>
            </div>
        </div>
        
        <!-- Step 3: Success -->
        <div id="step3" class="step">
            <div style="text-align: center;">
                <h2 style="color: #3c3; margin-bottom: 15px;">✅ Account Created!</h2>
                <p style="color: #666; margin-bottom: 20px;">Your account has been verified successfully.</p>
                <div style="background: #f5f5f5; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong>Your Access Token:</strong>
                    <div style="background: white; padding: 10px; margin-top: 10px; border-radius: 5px; word-break: break-all; font-family: monospace; font-size: 12px;" id="tokenDisplay"></div>
                </div>
                <p style="font-size: 12px; color: #888;">Save this token to use in your TurboWarp app!</p>
            </div>
        </div>
    </div>
    
    <script>
        let currentEmail = '';
        
        function showMessage(text, type) {
            const msg = document.getElementById('message');
            msg.textContent = text;
            msg.className = 'message ' + type;
            msg.style.display = 'block';
        }
        
        function showStep(step) {
            document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
            document.getElementById('step' + step).classList.add('active');
        }
        
        document.getElementById('signupForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('signupBtn');
            btn.disabled = true;
            btn.textContent = 'Sending verification email...';
            
            const email = document.getElementById('email').value;
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            
            try {
                const res = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email, username, password})
                });
                
                const data = await res.json();
                
                if (res.ok) {
                    currentEmail = email;
                    document.getElementById('emailDisplay').textContent = email;
                    showStep(2);
                    showMessage('Verification code sent! Check your email.', 'success');
                } else {
                    showMessage(data.error || 'Registration failed', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Create Account';
                }
            } catch (err) {
                showMessage('Network error. Please try again.', 'error');
                btn.disabled = false;
                btn.textContent = 'Create Account';
            }
        });
        
        document.getElementById('verifyForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('verifyBtn');
            btn.disabled = true;
            btn.textContent = 'Verifying...';
            
            const code = document.getElementById('code').value;
            
            try {
                const res = await fetch('/api/auth/verify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email: currentEmail, code})
                });
                
                const data = await res.json();
                
                if (res.ok) {
                    document.getElementById('tokenDisplay').textContent = data.token;
                    showStep(3);
                    showMessage('', '');
                } else {
                    showMessage(data.error || 'Invalid code', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Verify & Login';
                }
            } catch (err) {
                showMessage('Network error. Please try again.', 'error');
                btn.disabled = false;
                btn.textContent = 'Verify & Login';
            }
        });
        
        document.getElementById('resendCode').addEventListener('click', async (e) => {
            e.preventDefault();
            showMessage('Resending code...', 'success');
            
            try {
                const res = await fetch('/api/auth/resend', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({email: currentEmail})
                });
                
                if (res.ok) {
                    showMessage('New code sent! Check your email.', 'success');
                } else {
                    showMessage('Failed to resend code', 'error');
                }
            } catch (err) {
                showMessage('Network error', 'error');
            }
        });
    </script>
</body>
</html>
"""

@app.route('/', methods=['GET'])
def registration_page():
    """Serve registration page"""
    return render_template_string(REGISTRATION_HTML)

# ============= AUTHENTICATION ENDPOINTS =============

@app.route('/api/auth/register', methods=['POST'])
def register_user():
    """Register a new user and send verification email"""
    try:
        data = request.json
        email = data.get('email')
        username = data.get('username')
        password = data.get('password')
        
        print(f"Registration attempt: email={email}, username={username}")
        
        if not email or not username or not password:
            return jsonify({'error': 'All fields required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        if len(username) < 3:
            return jsonify({'error': 'Username must be at least 3 characters'}), 400
        
        # Check if user already exists
        existing = users_collection.find_one({'$or': [{'email': email}, {'username': username}]})
        if existing:
            print(f"User already exists: {existing}")
            return jsonify({'error': 'Email or username already exists'}), 400
        
        # Generate verification code
        code = generate_verification_code()
        print(f"Generated verification code: {code}")
        
        # Store pending verification
        verification_codes.delete_many({'email': email})  # Remove old codes
        verification_codes.insert_one({
            'email': email,
            'username': username,
            'password_hash': hash_password(password),
            'code': code,
            'created_at': time.time(),
            'expires_at': time.time() + (15 * 60)  # 15 minutes
        })
        print(f"Stored verification code in database")
        
        # Send email
        print(f"Attempting to send email to {email}...")
        if not send_verification_email(email, code):
            print(f"Failed to send email to {email}")
            return jsonify({'error': 'Failed to send verification email. Check server logs.'}), 500
        
        print(f"Successfully sent verification email to {email}")
        return jsonify({'success': True, 'message': 'Verification code sent to email'})
    
    except Exception as e:
        print(f"ERROR in register_user: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/api/auth/verify', methods=['POST'])
def verify_email():
    """Verify email with code and create account"""
    data = request.json
    email = data.get('email')
    code = data.get('code')
    
    if not email or not code:
        return jsonify({'error': 'Email and code required'}), 400
    
    # Find verification code
    verification = verification_codes.find_one({'email': email, 'code': code})
    
    if not verification:
        return jsonify({'error': 'Invalid verification code'}), 401
    
    # Check if expired
    if time.time() > verification['expires_at']:
        verification_codes.delete_one({'_id': verification['_id']})
        return jsonify({'error': 'Verification code expired'}), 401
    
    # Create user account
    users_collection.insert_one({
        'email': email,
        'username': verification['username'],
        'password_hash': verification['password_hash'],
        'created_at': time.time(),
        'verified': True
    })
    
    # Delete verification code
    verification_codes.delete_one({'_id': verification['_id']})
    
    # Create session
    token, expiry = create_session(verification['username'])
    
    return jsonify({
        'success': True,
        'token': token,
        'expires_at': expiry,
        'username': verification['username']
    })

@app.route('/api/auth/resend', methods=['POST'])
def resend_code():
    """Resend verification code"""
    data = request.json
    email = data.get('email')
    
    verification = verification_codes.find_one({'email': email})
    if not verification:
        return jsonify({'error': 'No pending verification found'}), 404
    
    # Generate new code
    code = generate_verification_code()
    
    verification_codes.update_one(
        {'email': email},
        {'$set': {
            'code': code,
            'created_at': time.time(),
            'expires_at': time.time() + (15 * 60)
        }}
    )
    
    if not send_verification_email(email, code):
        return jsonify({'error': 'Failed to send email'}), 500
    
    return jsonify({'success': True})

@app.route('/api/auth/login', methods=['POST'])
def login_user():
    """Login with username/email and password"""
    data = request.json
    username_or_email = data.get('username')
    password = data.get('password')
    
    if not username_or_email or not password:
        return jsonify({'error': 'Username/email and password required'}), 400
    
    # Find user by username or email
    user = users_collection.find_one({
        '$or': [
            {'username': username_or_email},
            {'email': username_or_email}
        ]
    })
    
    if not user or user['password_hash'] != hash_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    # Create session
    token, expiry = create_session(user['username'])
    
    return jsonify({
        'success': True,
        'token': token,
        'expires_at': expiry,
        'username': user['username']
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout_user():
    """Logout and invalidate token"""
    session = verify_user_token(request)
    if not session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    auth = request.headers.get('Authorization', '').replace('Bearer ', '')
    sessions_collection.delete_one({'token': auth})
    
    return jsonify({'success': True})

# ============= DISCORD API ENDPOINTS =============

@app.route('/api/test/email', methods=['GET'])
def test_email():
    """Test email sending"""
    try:
        test_email_addr = request.args.get('email', 'test@example.com')
        code = '123456'
        
        result = send_verification_email(test_email_addr, code)
        
        if result:
            return jsonify({'success': True, 'message': f'Test email sent to {test_email_addr}'})
        else:
            return jsonify({'success': False, 'message': 'Email failed - check server logs'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Public health check"""
    uptime = None
    if bot_status['start_time']:
        uptime = int(time.time() - bot_status['start_time'])
    
    # Check if critical env vars are set
    env_status = {
        'DISCORD_BOT_TOKEN': 'set' if DISCORD_TOKEN else 'missing',
        'MONGODB': 'set' if MONGODB_URI else 'missing',
        'APP_EMAIL': 'set' if APP_EMAIL else 'missing',
        'APP_PASS': 'set' if APP_PASS else 'missing'
    }
    
    return jsonify({
        'status': 'healthy' if bot_status['ready'] else 'starting',
        'uptime_seconds': uptime,
        'active_sessions': sessions_collection.count_documents({}),
        'registered_users': users_collection.count_documents({}),
        'environment': env_status
    })

@app.route('/api/discord/status', methods=['GET'])
def discord_status():
    """Check Discord connection status"""
    session = verify_user_token(request)
    if not session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    return jsonify({
        'status': 'up' if bot.is_ready() else 'down'
    })

@app.route('/api/guilds', methods=['GET'])
def get_guilds():
    """Get all guilds"""
    session = verify_user_token(request)
    if not session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    guilds = []
    for guild in bot.guilds:
        guilds.append({
            'id': str(guild.id),
            'name': guild.name,
            'icon_url': str(guild.icon.url) if guild.icon else None,
            'member_count': guild.member_count
        })
    
    return jsonify({'guilds': guilds})

@app.route('/api/guilds/<guild_id>/channels', methods=['GET'])
def get_channels(guild_id):
    """Get all channels in a guild"""
    session = verify_user_token(request)
    if not session:
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
    session = verify_user_token(request)
    if not session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    limit = int(request.args.get('limit', 10))
    limit = min(limit, 50)
    
    if channel_id not in message_cache:
        return jsonify({'messages': []})
    
    messages = list(message_cache[channel_id])[-limit:]
    return jsonify({'messages': messages})

@app.route('/api/channels/<channel_id>/send', methods=['POST'])
def send_message(channel_id):
    """Send a message via webhook"""
    session = verify_user_token(request)
    if not session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    content = data.get('content')
    username = data.get('username', session['username'])
    avatar_base64 = data.get('avatar_base64')
    
    if not content:
        return jsonify({'error': 'Content required'}), 400
    
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
    """Get member information"""
    session = verify_user_token(request)
    if not session:
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

def run_flask():
    """Run Flask in a separate thread"""
    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("Starting Discord bot...")
    print(f"API will be available on port {PORT}")
    print(f"Registration page: http://localhost:{PORT}/")
    bot.run(DISCORD_TOKEN)
