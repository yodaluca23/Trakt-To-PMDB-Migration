import os
import json
from webbrowser import open_new_tab
import requests
from datetime import datetime
from time import sleep
from main import sync_lists, sync_movie_resume_points, sync_movie_watch_history, sync_show_resume_points, sync_show_watch_history, sync_watchlist, add_user_information, create_trakt_headers, build_sync_context, trakt_api_url
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Cookie, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import base64

load_dotenv()

app = FastAPI()

session = requests.Session()

def set_trakt_cookies(response: Response, data: dict) -> Response:
    refresh_token_data = {
        "refresh_token": data.get("refresh_token", ""),
        "created_at": data.get("created_at", 0),
        "expires_in": data.get("expires_in", 0)
    }

    cookies = base64.b64encode(json.dumps(data).encode()).decode()
    refresh_token = base64.b64encode(json.dumps(refresh_token_data).encode()).decode()
    response.set_cookie(key="trakt-auth", value=cookies, httponly=True, max_age=data.get("expires_in", 3600), samesite="strict")
    response.set_cookie(key="trakt-auth-refresh", value=refresh_token, httponly=True, max_age=30*24*3600, samesite="strict")  # Set refresh token cookie for 30 days

    return response

@app.get("/trakt/auth")
async def generate_trakt_authorization_url() -> dict:
    global trakt_api_url, userAgent

    client_id = os.getenv("trakt_client")
    redirect_uri = "http://127.0.0.1:8000" + os.getenv("trakt_redirect_uri", "/trakt/callback")
    
    user_url = f"{trakt_api_url}/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"

    return {"url": user_url}

@app.post("/trakt/auth")
async def authenticate_trakt_user(response: Response, Authorization: str = Header(default=None)) -> dict:
    global trakt_api_url, userAgent

    client_id = os.getenv("trakt_client")
    client_secret = os.getenv("trakt_secret")
    if not Authorization:
        raise HTTPException(status_code=400, detail="Missing Authorization header")

    code = Authorization.split(" ")[-1]  # Extract the code from the header
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    url = trakt_api_url + "/oauth/token"

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "http://127.0.0.1:8000" + os.getenv("trakt_redirect_uri", "/trakt/callback"),
        "grant_type": "authorization_code"
    }
    headers = {"Content-Type": "application/json"}

    res = session.request("POST", url, json=payload, headers=headers)

    if res.status_code == 200:
        data = res.json()

        response = set_trakt_cookies(response, data)
        return {"success": True, "message": "Cookies set successfully"}
    else:
        raise HTTPException(status_code=res.status_code, detail={"error": "Failed to authenticate with Trakt", "details": res.text})

# This mounts the "static" directory to serve static files (like the callback HTML page) at the root URL.
# `html=True` makes `/` resolve to `static/index.html` automatically.
app.mount("/", StaticFiles(directory="static", html=True), name="static")