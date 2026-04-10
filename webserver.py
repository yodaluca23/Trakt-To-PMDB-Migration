import os
import json
from time import sleep
import requests
import queue
import threading
import traceback
from datetime import datetime
from main import check_pmdb_token, sync_lists, sync_movie_resume_points, sync_movie_watch_history, sync_show_resume_points, sync_show_watch_history, sync_watchlist, add_user_information, create_trakt_headers, build_sync_context, trakt_api_url
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Cookie, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, RedirectResponse
import base64
from pydantic import BaseModel
from uuid import uuid4

load_dotenv()

app = FastAPI()

session = requests.Session()

running_jobs = []  # List to keep track of running jobs and their event queues

jobs_lock = threading.Lock()  # Lock to synchronize access to the running_jobs list

def get_running_job(job_id: str) -> dict | None:
    global running_jobs

    with jobs_lock:
        job = next((job for job in running_jobs if job["job_id"] == job_id), None)
    return job

def remove_job(job_id: str):
    global running_jobs

    with jobs_lock:
        running_jobs = [job for job in running_jobs if job["job_id"] != job_id]

def add_job(job_id: str, event_queue: queue.Queue, pmdb_api_key: str):
    global running_jobs

    with jobs_lock:
        running_jobs.append({"job_id": job_id, "event_queue": event_queue, "pmdb_api_key": pmdb_api_key})
    
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

    if data is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve user information from Trakt")

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

def refresh_trakt_token(response: Response, refresh_token: str) -> tuple[Response, bool, dict | None]:
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
        return response, True, data
    else:
        response.delete_cookie(key="trakt_auth")
        response.delete_cookie(key="trakt_auth_refresh")

        return response, False, None

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
        response, refreshed, trakt_auth = refresh_trakt_token(response, trakt_auth.get("refresh_token", ""))
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

def migrate_data(sync_context: dict, sync_options: dict, event_queue: queue.Queue, job_id: str):
    try:
        if sync_options.get("sync_lists_choice"):
            sync_lists(sync_context)
            event_queue.put({"type": "progress", "message": "Finished syncing lists", "step": 1, "progress": 17})
        if sync_options.get("sync_movie_resume_points_choice"):
            sync_movie_resume_points(sync_context)
            event_queue.put({"type": "progress", "message": "Finished syncing movie resume points", "step": 2, "progress": 33})
        if sync_options.get("sync_movie_watch_history_choice"):
            sync_movie_watch_history(sync_context)
            event_queue.put({"type": "progress", "message": "Finished syncing movie watch history", "step": 3, "progress": 50})
        if sync_options.get("sync_show_resume_points_choice"):
            sync_show_resume_points(sync_context)
            event_queue.put({"type": "progress", "message": "Finished syncing show resume points", "step": 4, "progress": 67})
        if sync_options.get("sync_show_watch_history_choice"):
            sync_show_watch_history(sync_context)
            event_queue.put({"type": "progress", "message": "Finished syncing show watch history", "step": 5, "progress": 83})
        if sync_options.get("sync_watchlist_choice"):
            sync_watchlist(sync_context)

        event_queue.put({"type": "complete", "message": "Migration complete", "step": 6, "progress": 100})

        remove_job(job_id)  # Remove the job from the running jobs list after completion
    except Exception as e:
        print(f"Error during migration: {e}")
        traceback.print_exc()
        event_queue.put({"type": "error", "message": f"Migration failed: {str(e)}"})

def create_sync_job(sync_context: dict, sync_options: dict, event_queue: queue.Queue) -> tuple[str, queue.Queue, threading.Thread]:
    job_id = f"job_{uuid4()}_{int(datetime.now().timestamp())}"  # Create a unique job ID based on the current timestamp and number of running jobs
    thread = threading.Thread(target=migrate_data, args=(sync_context, sync_options, event_queue, job_id))
    return job_id, event_queue, thread

# Used for testing the event streaming without running the actual migration logic
def create_sync_job_dummy():
    job_id = f"job_{uuid4()}_{int(datetime.now().timestamp())}"  # Create a unique job ID based on the current timestamp and number of running jobs
    print(f"Created dummy job with ID:\n{job_id}\nJob URL:\n/migrate/{job_id}/events")
    event_queue = queue.Queue()
    add_job(job_id, event_queue, os.getenv("PMDB_API_KEY"))  # Add the new job to the list of running jobs with a dummy PMDB API key

    def dummy_event_generator():
        for i in range(6):
            event_queue.put({"type": "progress", "message": f"Dummy progress update {i+1}/6", "step": i+1, "progress": round((i+1)/6 * 100, 0), "complete": True if i == 5 else False})
            sleep(15)  # Simulate time taken for each step of the migration
        event_queue.put({"type": "complete", "message": "Dummy migration complete"})
        remove_job(job_id)  # Remove the job from the running jobs list after completion

    # Create a thread that simulates sending events to the queue
    thread = threading.Thread(target=dummy_event_generator)
    thread.start()
    return job_id, event_queue, thread

@app.post("/migrate")
def request_data_migration(sync_options: sync_options, response: Response, pmdb_auth: str | None = Cookie(default=None), trakt_auth: str | None = Cookie(default=None), trakt_auth_refresh: str | None = Cookie(default=None)) -> dict:
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
        response, refreshed, trakt_auth = refresh_trakt_token(response, trakt_auth.get("refresh_token", ""))
        if not refreshed:
            raise HTTPException(status_code=401, detail="Trakt authentication expired and refresh failed")

    try:
        event_queue = queue.Queue()  # Create a new event queue for this job
        sync_context = build_sync_context(trakt_auth, pmdb_api_key, event_queue)

        sync_options_data = sync_options.model_dump()
        job_id, event_queue, thread = create_sync_job(sync_context, sync_options_data, event_queue)  # Create the sync job and get the event queue
        add_job(job_id, event_queue, pmdb_api_key)  # Add the new job to the list of running jobs

        thread.start()

        return {"success": True, "job_id": job_id, "events_url": f"/migrate/{job_id}/events", "message": "Migration job started successfully"}
    except Exception as e:
        print(f"Error starting migration job: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to start migration job")

def stream_sync_job(job_id: str, pmdb_api_key: str) -> StreamingResponse:
    job = get_running_job(job_id)
    if job:
        if job["pmdb_api_key"] != pmdb_api_key:
            raise HTTPException(status_code=403, detail="Forbidden: You do not have access to this job's events")
    else:
        raise HTTPException(status_code=404, detail="Job not found")

    event_queue = job["event_queue"]

    def event_generator():
        while True:
            try:
                event = event_queue.get(timeout=1)  # Wait for an event with a timeout to allow checking for thread completion
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "complete":
                    break  # Stop streaming after completion
            except queue.Empty:
                continue  # No event, check again

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/migrate/{job_id}/events")
def migrate_job_events(job_id: str, pmdb_auth: str | None = Cookie(default=None)) -> StreamingResponse:
    pmdb_api_key = None

    if pmdb_auth:
        try:
            decoded_pmdb_auth = base64.b64decode(pmdb_auth).decode()
            pmdb_auth = json.loads(decoded_pmdb_auth)
            pmdb_api_key = pmdb_auth.get("api_key", "")
        except Exception as e:
            print(f"Error decoding pmdb_auth cookie: {e}")
            raise HTTPException(status_code=400, detail="Invalid PMDB authentication cookie")
    else:
        raise HTTPException(status_code=401, detail="Not authenticated with PMDB")
    
    return stream_sync_job(job_id, pmdb_api_key)

@app.get(os.getenv("trakt_redirect_uri", "/trakt/callback_fallback") if os.getenv("trakt_redirect_uri", "/trakt/callback_fallback") != "/trakt/callback" else "/trakt/callback_fallback")
def trakt_callback_fallback(code: str | None = None):
    return RedirectResponse(url=f"/trakt/callback?code={code}", status_code=301)

# This mounts the "static" directory to serve static files (like the callback HTML page) at the root URL.
# `html=True` makes `/` resolve to `static/index.html` automatically.
app.mount("/", StaticFiles(directory="static", html=True), name="static")

#create_sync_job_dummy()  # Create a dummy job on startup to test the event streaming without running the actual migration logic