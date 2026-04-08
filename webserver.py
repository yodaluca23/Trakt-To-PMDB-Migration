import os
import json
from webbrowser import open_new_tab
import requests
from datetime import datetime
from time import sleep
from main import check_pmdb_token, sync_lists, sync_movie_resume_points, sync_movie_watch_history, sync_show_resume_points, sync_show_watch_history, sync_watchlist, add_user_information, create_trakt_headers, build_sync_context, trakt_api_url
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Cookie, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import base64
from pydantic import BaseModel

load_dotenv()

app = FastAPI()

session = requests.Session()

class sync_options(BaseModel):
    sync_lists_choice: bool = False
    sync_movie_resume_points_choice: bool = False
    sync_movie_watch_history_choice: bool = False
    sync_show_resume_points_choice: bool = False
    sync_show_watch_history_choice: bool = False
    sync_watchlist_choice: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "sync_lists_choice": True,
                    "sync_movie_resume_points_choice": True,
                    "sync_movie_watch_history_choice": False,
                    "sync_show_resume_points_choice": True,
                    "sync_show_watch_history_choice": True,
                    "sync_watchlist_choice": False
                }
            ]
        }
    }

def set_trakt_cookies(response: Response, data: dict) -> Response:

    data = add_user_information(data, create_trakt_headers(data))

    refresh_token_data = {
        "refresh_token": data.get("refresh_token", ""),
        "created_at": data.get("created_at", 0),
        "expires_in": data.get("expires_in", 0)
    }

    cookies = base64.b64encode(json.dumps(data).encode()).decode()
    refresh_token = base64.b64encode(json.dumps(refresh_token_data).encode()).decode()
    response.set_cookie(key="trakt_auth", value=cookies, httponly=True, max_age=data.get("expires_in", 3600), samesite="strict")
    response.set_cookie(key="trakt_auth_refresh", value=refresh_token, httponly=True, max_age=30*24*3600, samesite="strict")  # Set refresh token cookie for 30 days

    return response

def refresh_trakt_token(response: Response, refresh_token: str) -> tuple[Response, bool]:
    global trakt_api_url

    client_id = os.getenv("trakt_client")
    client_secret = os.getenv("trakt_secret")

    url = trakt_api_url + "/oauth/token"

    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token"
    }
    headers = {"Content-Type": "application/json"}

    res = session.request("POST", url, json=payload, headers=headers)

    if res.status_code == 200:
        data = res.json()
        response = set_trakt_cookies(response, data)
        return response, True
    else:
        response.delete_cookie(key="trakt_auth")
        response.delete_cookie(key="trakt_auth_refresh")

        return response, False

@app.get("/trakt/auth")
def generate_trakt_authorization_url() -> dict:
    global trakt_api_url, userAgent

    client_id = os.getenv("trakt_client")
    redirect_uri = os.getenv("domain", "http://127.0.0.1:8000") + os.getenv("trakt_redirect_uri", "/trakt/callback")
    
    user_url = f"{trakt_api_url}/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}"

    return {"url": user_url}

@app.post("/trakt/auth")
def authenticate_trakt_user(response: Response, Authorization: str = Header(default=None)) -> dict:
    global trakt_api_url, userAgent

    client_id = os.getenv("trakt_client")
    client_secret = os.getenv("trakt_secret")
    if not Authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    code = Authorization.split(" ")[-1]  # Extract the code from the header
    if not code:
        raise HTTPException(status_code=401, detail="Missing authorization code")

    url = trakt_api_url + "/oauth/token"

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": os.getenv("domain", "http://127.0.0.1:8000") + os.getenv("trakt_redirect_uri", "/trakt/callback"),
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
    
@app.post("/pmdb/auth")
def authenticate_pmdb_user(response: Response, Authorization: str = Header(default=None)) -> dict:
    if not Authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    api_key = Authorization.split(" ")[-1]  # Extract the API key from the header
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    success = check_pmdb_token(api_key)
    if not success:
        raise HTTPException(status_code=401, detail="Invalid PMDB API key")

    pmdb_auth = {
        "api_key": api_key
    }
    cookie = base64.b64encode(json.dumps(pmdb_auth).encode()).decode()

    response.set_cookie(key="pmdb_auth", value=cookie, httponly=True, max_age=30*24*3600, samesite="strict")  # Set PMDB auth cookie for 30 days
    return {"success": True, "message": "PMDB authentication successful"}

@app.get("/auth/status")
def get_authentication_status(response: Response, pmdb_auth: str | None = Cookie(default=None), trakt_auth: str | None = Cookie(default=None), trakt_auth_refresh: str | None = Cookie(default=None)) -> dict:

    if (not trakt_auth) and trakt_auth_refresh:
        trakt_auth = trakt_auth_refresh  # Use refresh token if access token is missing

    trakt_logged_in = trakt_auth is not None
    pmdb_logged_in = pmdb_auth is not None

    # Decode the auth cookies from base64
    if trakt_auth:
        try:
            decoded_trakt_auth = base64.b64decode(trakt_auth).decode()
            trakt_auth = json.loads(decoded_trakt_auth)
        except Exception as e:
            print(f"Error decoding trakt_auth cookie: {e}")
            trakt_logged_in = False
    
    if pmdb_auth:
        try:
            decoded_pmdb_auth = base64.b64decode(pmdb_auth).decode()
            pmdb_auth = json.loads(decoded_pmdb_auth)
            pmdb_auth = pmdb_auth.get("api_key", "")
        except Exception as e:
            print(f"Error decoding pmdb_auth cookie: {e}")
            pmdb_logged_in = False

    if trakt_logged_in and ((trakt_auth.get("expires_in", 0) + trakt_auth.get("created_at", 0) + 300) < datetime.now().timestamp()):  # If token expires in less than 5 minutes
        response, refreshed = refresh_trakt_token(response, trakt_auth_refresh)
        if refreshed:
            trakt_logged_in = True
        else:
            trakt_logged_in = False

    if pmdb_logged_in:
        pmdb_logged_in = check_pmdb_token(pmdb_auth)

    trakt_user = None
    pmdb_user = None

    if trakt_logged_in and trakt_auth:
        trakt_user = trakt_auth.get("user_info", {}).get("user", {}).get("username")

    if pmdb_logged_in and pmdb_auth:
        pmdb_user = pmdb_auth[:15] + "..."  # Show only the first 15 characters of the PMDB API key for privacy
    else:
        response.delete_cookie(key="pmdb_auth")

    return {
        "trakt": trakt_logged_in,
        "trakt_user": trakt_user,
        "pmdb": pmdb_logged_in,
        "pmdb_user": pmdb_user
    }

@app.post("/migrates")
def migrate_data(sync_options: sync_options, response: Response, pmdb_auth: str | None = Cookie(default=None), trakt_auth: str | None = Cookie(default=None), trakt_auth_refresh: str | None = Cookie(default=None)) -> dict:
    if (not trakt_auth) and trakt_auth_refresh:
        trakt_auth = trakt_auth_refresh  # Use refresh token if access token is missing

    if not trakt_auth:
        raise HTTPException(status_code=401, detail="Not authenticated with Trakt")
    if not pmdb_auth:
        raise HTTPException(status_code=401, detail="Not authenticated with PMDB")

    # Decode the auth cookies from base64
    try:
        decoded_trakt_auth = base64.b64decode(trakt_auth).decode()
        trakt_auth = json.loads(decoded_trakt_auth)
    except Exception as e:
        print(f"Error decoding trakt_auth cookie: {e}")
        raise HTTPException(status_code=400, detail="Invalid Trakt authentication cookie")
    
    try:
        decoded_pmdb_auth = base64.b64decode(pmdb_auth).decode()
        pmdb_auth = json.loads(decoded_pmdb_auth)
        pmdb_api_key = pmdb_auth.get("api_key", "")
    except Exception as e:
        print(f"Error decoding pmdb_auth cookie: {e}")
        raise HTTPException(status_code=400, detail="Invalid PMDB authentication cookie")
    
    if (trakt_auth.get("expires_in", 0) + trakt_auth.get("created_at", 0) + 300) < datetime.now().timestamp():  # If token expires in less than 5 minutes
        response, refreshed = refresh_trakt_token(response, trakt_auth_refresh)
        if refreshed:
            decoded_trakt_auth = base64.b64decode(response.cookies.get("trakt_auth")).decode()
            trakt_auth = json.loads(decoded_trakt_auth)
        else:
            raise HTTPException(status_code=401, detail="Trakt authentication expired and refresh failed")

    sync_context = build_sync_context(trakt_auth, pmdb_api_key)

    results = {}

    if sync_options.get("sync_watchlist"):
        results["watchlist"] = sync_watchlist(sync_context)
    
    if sync_options.get("sync_movie_resume_points"):
        results["movie_resume_points"] = sync_movie_resume_points(sync_context)

    if sync_options.get("sync_movie_watch_history"):
        results["movie_watch_history"] = sync_movie_watch_history(sync_context)

    if sync_options.get("sync_show_resume_points"):
        results["show_resume_points"] = sync_show_resume_points(sync_context)

    if sync_options.get("sync_show_watch_history"):
        results["show_watch_history"] = sync_show_watch_history(sync_context)

    return {"success": True, "results": results}

# This mounts the "static" directory to serve static files (like the callback HTML page) at the root URL.
# `html=True` makes `/` resolve to `static/index.html` automatically.
app.mount("/", StaticFiles(directory="static", html=True), name="static")