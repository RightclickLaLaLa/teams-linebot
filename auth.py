import requests
import os

def get_token():
    url = "https://login.microsoftonline.com/" + os.getenv("TENANT_ID") + "/oauth2/v2.0/token"
    data = {
        "client_id": os.getenv("CLIENT_ID"),
        "scope": "https://graph.microsoft.com/.default",
        "client_secret": os.getenv("CLIENT_SECRET"),
        "grant_type": "client_credentials"
    }
    response = requests.post(url, data=data)
    return response.json().get("access_token")
