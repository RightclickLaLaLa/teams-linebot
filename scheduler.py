import requests
import auth
import datetime
import pytz

def get_upcoming_events():
    token = auth.get_token()
    now = datetime.datetime.utcnow()
    start_time = now.isoformat() + "Z"
    end_time = (now + datetime.timedelta(minutes=31)).isoformat() + "Z"

    url = f"https://graph.microsoft.com/v1.0/me/calendarview?startDateTime={start_time}&endDateTime={end_time}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.timezone="UTC"'
    }
    r = requests.get(url, headers=headers)
    data = r.json()

    meetings = []
    for item in data.get("value", []):
        if "onlineMeeting" in item:
            meetings.append({
                "subject": item.get("subject", "無標題"),
                "start": item["start"]["dateTime"],
                "joinUrl": item["onlineMeeting"]["joinUrl"]
            })
    return meetings
