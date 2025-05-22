import os
import json
import sqlite3
from datetime import datetime, timedelta
import requests
from flask import Flask, request, abort, redirect, session
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import schedule
import time
import threading
from urllib.parse import urlencode

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Change this to a secure secret key

# Configuration - Replace with your actual values
LINE_CHANNEL_ACCESS_TOKEN = 'your-line-channel-access-token'
LINE_CHANNEL_SECRET = 'your-line-channel-secret'
MICROSOFT_CLIENT_ID = 'your-microsoft-app-client-id'
MICROSOFT_CLIENT_SECRET = 'your-microsoft-app-client-secret'
MICROSOFT_TENANT_ID = 'common'  # or your specific tenant ID
LINE_GROUP_ID = 'your-line-group-id'  # Where to send meeting notifications

# Microsoft Graph API endpoints
MICROSOFT_AUTH_URL = f'https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}/oauth2/v2.0/authorize'
MICROSOFT_TOKEN_URL = f'https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}/oauth2/v2.0/token'
GRAPH_API_URL = 'https://graph.microsoft.com/v1.0'

# LINE Bot setup
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Database setup
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_tokens (
            user_id TEXT PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_meetings (
            meeting_id TEXT PRIMARY KEY,
            sent_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Database operations
def store_user_token(user_id, access_token, refresh_token, expires_in):
    expires_at = datetime.now() + timedelta(seconds=expires_in)
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_tokens 
        (user_id, access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, access_token, refresh_token, expires_at))
    conn.commit()
    conn.close()

def get_user_token(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT access_token, refresh_token, expires_at FROM user_tokens WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def refresh_access_token(user_id, refresh_token):
    data = {
        'client_id': MICROSOFT_CLIENT_ID,
        'client_secret': MICROSOFT_CLIENT_SECRET,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
        'scope': 'https://graph.microsoft.com/Calendars.Read offline_access'
    }
    
    response = requests.post(MICROSOFT_TOKEN_URL, data=data)
    if response.status_code == 200:
        token_data = response.json()
        store_user_token(
            user_id,
            token_data['access_token'],
            token_data.get('refresh_token', refresh_token),
            token_data['expires_in']
        )
        return token_data['access_token']
    return None

def is_meeting_sent(meeting_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT meeting_id FROM sent_meetings WHERE meeting_id = ?', (meeting_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_meeting_sent(meeting_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO sent_meetings (meeting_id, sent_at) VALUES (?, ?)', 
                   (meeting_id, datetime.now()))
    conn.commit()
    conn.close()

# Microsoft Graph API functions
def get_valid_access_token(user_id):
    token_data = get_user_token(user_id)
    if not token_data:
        return None
    
    access_token, refresh_token, expires_at = token_data
    expires_at = datetime.fromisoformat(expires_at)
    
    if datetime.now() >= expires_at:
        # Token expired, try to refresh
        return refresh_access_token(user_id, refresh_token)
    
    return access_token

def get_upcoming_meetings(user_id):
    access_token = get_valid_access_token(user_id)
    if not access_token:
        return []
    
    # Get meetings for the next 24 hours
    start_time = datetime.now().isoformat() + 'Z'
    end_time = (datetime.now() + timedelta(hours=24)).isoformat() + 'Z'
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    params = {
        '$filter': f"start/dateTime ge '{start_time}' and start/dateTime le '{end_time}'",
        '$select': 'id,subject,start,end,onlineMeeting',
        '$orderby': 'start/dateTime'
    }
    
    response = requests.get(f'{GRAPH_API_URL}/me/events', headers=headers, params=params)
    
    if response.status_code == 200:
        events = response.json().get('value', [])
        # Filter for Teams meetings only
        teams_meetings = []
        for event in events:
            if event.get('onlineMeeting') and event['onlineMeeting'].get('joinUrl'):
                teams_meetings.append(event)
        return teams_meetings
    
    return []

# LINE Bot webhook handler
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    message_text = event.message.text.lower()
    
    if message_text in ['auth', 'authorize', 'login']:
        # Generate Microsoft OAuth URL
        auth_params = {
            'client_id': MICROSOFT_CLIENT_ID,
            'response_type': 'code',
            'redirect_uri': f'{request.url_root}auth/callback',
            'scope': 'https://graph.microsoft.com/Calendars.Read offline_access',
            'state': user_id,
            'response_mode': 'query'
        }
        
        auth_url = f"{MICROSOFT_AUTH_URL}?{urlencode(auth_params)}"
        
        reply_message = TextSendMessage(
            text=f"Please click the link below to authorize access to your Microsoft account:\n{auth_url}"
        )
        line_bot_api.reply_message(event.reply_token, reply_message)
    
    elif message_text in ['check', 'meetings']:
        meetings = get_upcoming_meetings(user_id)
        if meetings:
            meeting_list = "📅 Upcoming Teams meetings:\n\n"
            for meeting in meetings[:5]:  # Show max 5 meetings
                start_time = datetime.fromisoformat(meeting['start']['dateTime'].replace('Z', ''))
                meeting_list += f"🕐 {start_time.strftime('%H:%M')} - {meeting['subject']}\n"
            
            reply_message = TextSendMessage(text=meeting_list)
        else:
            reply_message = TextSendMessage(text="No upcoming Teams meetings found for the next 24 hours.")
        
        line_bot_api.reply_message(event.reply_token, reply_message)
    
    else:
        reply_message = TextSendMessage(
            text="Commands:\n• 'auth' - Authorize Microsoft account\n• 'check' - Check upcoming meetings"
        )
        line_bot_api.reply_message(event.reply_token, reply_message)

# Microsoft OAuth callback
@app.route('/auth/callback')
def auth_callback():
    code = request.args.get('code')
    state = request.args.get('state')  # This is the user_id
    
    if not code or not state:
        return 'Authorization failed', 400
    
    # Exchange code for tokens
    data = {
        'client_id': MICROSOFT_CLIENT_ID,
        'client_secret': MICROSOFT_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': f'{request.url_root}auth/callback',
        'scope': 'https://graph.microsoft.com/Calendars.Read offline_access'
    }
    
    response = requests.post(MICROSOFT_TOKEN_URL, data=data)
    
    if response.status_code == 200:
        token_data = response.json()
        store_user_token(
            state,  # user_id
            token_data['access_token'],
            token_data['refresh_token'],
            token_data['expires_in']
        )
        
        # Send confirmation to user
        line_bot_api.push_message(
            state,
            TextSendMessage(text="✅ Authorization successful! I can now check your Teams meetings.")
        )
        
        return 'Authorization successful! You can close this window.'
    else:
        return 'Authorization failed', 400

# Automatic meeting checker
def check_and_send_meetings():
    """Check for upcoming meetings and send notifications"""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM user_tokens')
    users = cursor.fetchall()
    conn.close()
    
    for (user_id,) in users:
        meetings = get_upcoming_meetings(user_id)
        
        for meeting in meetings:
            meeting_id = meeting['id']
            start_time = datetime.fromisoformat(meeting['start']['dateTime'].replace('Z', ''))
            
            # Check if meeting starts within the next 15 minutes
            time_until_meeting = start_time - datetime.now()
            
            if timedelta(minutes=0) <= time_until_meeting <= timedelta(minutes=15):
                if not is_meeting_sent(meeting_id):
                    # Send meeting notification to LINE group
                    join_url = meeting['onlineMeeting']['joinUrl']
                    message = f"🚨 Meeting Alert!\n\n📋 {meeting['subject']}\n🕐 Starts at {start_time.strftime('%H:%M')}\n🔗 {join_url}"
                    
                    try:
                        line_bot_api.push_message(LINE_GROUP_ID, TextSendMessage(text=message))
                        mark_meeting_sent(meeting_id)
                        print(f"Sent meeting notification: {meeting['subject']}")
                    except Exception as e:
                        print(f"Error sending meeting notification: {e}")

# Schedule the meeting checker
schedule.every(5).minutes.do(check_and_send_meetings)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    # Initialize database
    init_db()
    
    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Run Flask app
    app.run(debug=True, port=5000)
