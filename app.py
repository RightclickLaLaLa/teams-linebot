from flask import Flask, request
from linebot import LineBotApi
from linebot.models import TextSendMessage
import os
import auth
import scheduler

app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
GROUP_ID = os.getenv("LINE_GROUP_ID")

@app.route("/")
def index():
    return "Bot is running."

@app.route("/callback", methods=["POST"])
def callback():
    return "Callback OK"

@app.route("/cron", methods=["GET"])
def cron():
    events = scheduler.get_upcoming_events()
    for event in events:
        msg = f"""📅 會議提醒：{event['subject']}
🕒 時間：{event['start']}
🔗 連結：{event['joinUrl']}"""
        line_bot_api.push_message(GROUP_ID, TextSendMessage(text=msg))
    return "Pushed"

if __name__ == "__main__":
    app.run()
