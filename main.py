import datetime
import queue

import requests
from dotenv import load_dotenv
import os
from dataclasses import dataclass
from webbrowser import open_new_tab
from time import sleep

# Load environment variables from .env file
load_dotenv()

trakt_api_url = "https://api.trakt.tv"
pmdb_api_url = "https://publicmetadb.com/api"
version = "1.0.0"
userAgent = f"TraktMigration/{version}"

session = requests.Session()
session.headers.update({
    "Content-Type": "application/json",
    "User-Agent": userAgent
})

@dataclass
class SyncContext:
    token_data: dict
    trakt_headers: dict
    pmdb_headers: dict
    trakt_data: dict
    event_queue: queue.Queue | None = None

    @property
    def username(self) -> str:
        return self.token_data.get("username", "")
    
def log(message: str, ctx: SyncContext = None, level: str = "info") -> None:
    event_queue = ctx.event_queue if ctx else None

    if event_queue and level.lower() != "verbose":
        event_queue.put({"type": "log", "message": message, "level": level})
    elif (os.getenv("domain", "").replace(" ", "") == "" or os.getenv("domain", "").lower() == "required_if_using_as_a_webserver_with_webserver.py") or os.getenv("log_to_console", "true").lower() == "true":
        print(f"[{level.upper()}] {message}")

def create_trakt_headers(token_data: dict = None) -> dict:
    headers = {
        "trakt-api-version": "2",
        "trakt-api-key": os.getenv("trakt_client")
    }

    if token_data and token_data.get("access_token") and token_data.get("token_type"):
        headers["Authorization"] = f"{token_data.get('token_type')} {token_data.get('access_token')}"

    return headers

def build_sync_context(token_data: dict, pmdb_api_key: str, event_queue: queue.Queue = None, trakt_data: dict = None) -> SyncContext:
    return SyncContext(
        token_data=token_data,
        trakt_headers=create_trakt_headers(token_data),
        pmdb_headers={"Authorization": "Bearer " + pmdb_api_key},
        event_queue=event_queue,
        trakt_data=trakt_data
    )

def add_user_information(token_data: dict, trakt_headers: dict) -> dict | None:

    response = session.get(trakt_api_url + "/users/settings", headers=trakt_headers)
    if response.status_code == 200:
        user_info = response.json()
        #log(f"User information retrieved: {user_info.get('user').get('username')}")
        token_data["username"] = user_info.get("user").get("username")
        return token_data
    else:
        log(f"Failed to retrieve user information: {response.status_code} - {response.text}", level="error")
        return None
    
def check_pmdb_token(token: str) -> bool:
    global pmdb_api_url

    url = pmdb_api_url + "/external/ratings"
    querystring = {"tmdb_id":"550","media_type":"movie"}
    headers = {"Authorization": "Bearer " + token}

    response = requests.request("GET", url, headers=headers, params=querystring)

    if not (response.status_code >= 200 and response.status_code < 300):
        log(f"PMDB token validation failed: {response.status_code} - {response.text}", level="error")

    return response.status_code >= 200 and response.status_code < 300

def code_authorize_user() -> dict | None:
    global trakt_api_url, userAgent
    log("Authorizing user...")

    client_id = os.getenv("trakt_client")
    client_secret = os.getenv("trakt_secret")
    
    payload = {"client_id": client_id}

    response = session.request("POST", trakt_api_url + "/oauth/device/code", json=payload)

    verification_url = response.json().get("verification_url")
    user_code = response.json().get("user_code")
    device_code = response.json().get("device_code")
    interval = response.json().get("interval")

    open_new_tab(verification_url + "?code=" + user_code)
    log(f"If the page doesn't open automatically, please visit {verification_url} and enter the code: {user_code}")

    while True:
        sleep(interval)
        payload = {
            "code": device_code,
            "client_id": client_id,
            "client_secret": client_secret
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": userAgent
        }

        response = session.request("POST", trakt_api_url + "/oauth/device/token", json=payload, headers=headers)

        if response.status_code == 200:
            json_response = response.json()
            log("User authorized successfully!")

            trakt_auth_headers = create_trakt_headers(json_response)
            user_data = add_user_information(json_response, trakt_auth_headers)

            return user_data
        elif response.status_code == 400:
            log("Waiting for user authorization...")
        else:
            log(f"Error: {response.status_code} - {response.text}", level="error")
            break

def parse_listed_at(value: str) -> datetime.datetime:
    if not value:
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

def fetch_watchlist(ctx: SyncContext) -> list | None:
    log("Fetching watchlist...")

    if ctx.trakt_data and ctx.trakt_data.get("lists-watchlist") is not None:
        log("Using watchlist from provided trakt_data.")
        watchlist = ctx.trakt_data.get("lists-watchlist")

        # Sort by converting 'listed_at' date to a datetime object in ascending order to maintain consistency with how items are added to PMDB
        watchlist.sort(key=lambda x: parse_listed_at(x.get("listed_at")))
        return watchlist

    url = trakt_api_url + f"/users/{ctx.username}/watchlist/all/added/asc"

    response = session.get(url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        watchlist = response.json()
        log(f"Watchlist fetched successfully. Total items: {len(watchlist)}", ctx=ctx)
        return watchlist
    else:
        log(f"Failed to fetch watchlist: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return []

def get_pmdb_watchlist_id(ctx: SyncContext) -> str | None:
    log("Retrieving PMDB watchlist ID...", ctx=ctx)
    url = pmdb_api_url + "/external/lists"

    response = session.get(url, headers=ctx.pmdb_headers)

    if response.status_code == 200:
        lists = response.json()
        for watchlist in lists.get("items", [{}]):
            if watchlist.get("type") == "watchlist":
                log(f"Found existing PMDB watchlist with ID: {watchlist.get('id')}", ctx=ctx)
                return watchlist.get("id")
        log("No existing PMDB watchlist found. A new one will be created.", ctx=ctx)

        url = pmdb_api_url + "/external/lists"
        body = {
            "name": "Watchlist",
            "is_public": True,
            "type": "watchlist"
        }
        response = session.post(url, headers=ctx.pmdb_headers, json=body)
        if response.status_code >= 200 and response.status_code < 300 and response.json().get("success") and response.json().get("item").get("id"):
            new_watchlist = response.json()
            log(f"New PMDB watchlist created with ID: {new_watchlist.get('item').get('id')}", ctx=ctx)
            return new_watchlist.get("item").get("id")
        else:
            log(f"Failed to create PMDB watchlist: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return None

def add_to_pmdb_list(ctx: SyncContext, pmdb_list_id: str, item: dict) -> bool:

    tmdb_id = item.get("movie", item.get("show", {})).get("ids", {}).get("tmdb")

    if not tmdb_id:
        body = {
            "id_type": "trakt",
            "id_value": item.get("ids", {}).get("trakt")
        }

        id_response = session.get(pmdb_api_url + "/external/mappings/lookup", headers=ctx.pmdb_headers, json=body)
        if id_response.status_code == 200:
            tmdb_id = id_response.json().get("results", [{}])[0].get("tmdb_id")

    url = pmdb_api_url + f"/external/lists/{pmdb_list_id}/items"
    body = {
        "listId": pmdb_list_id,
        "media_type": "movie" if item.get("type") == "movie" else "tv",
        "tmdb_id": tmdb_id
    }

    response = session.post(url, headers=ctx.pmdb_headers, json=body)
    if response.status_code >= 200 and response.status_code < 300 and response.json().get("success"):
        log(f"Added '{item.get('movie', item.get('show', {})).get('title')}' to PMDB watchlist.", level="verbose", ctx=ctx)
        return True
    else:
        log(f"Failed to add '{item.get('movie', item.get('show', {})).get('title')}' to PMDB watchlist: {response.status_code} - {response.text}", level="error", ctx=ctx)
    return False

def sync_watchlist(ctx: SyncContext) -> bool:
    log("Syncing watchlist...", ctx=ctx)
    watchlist = fetch_watchlist(ctx)
    pmdb_watchlist_id = get_pmdb_watchlist_id(ctx)

    all_success = True

    for item in watchlist:
        success = add_to_pmdb_list(ctx, pmdb_watchlist_id, item)

        if not success:
            all_success = False

    if all_success:
        log("Watchlist synced successfully!", ctx=ctx)
    else:
        log("Watchlist synced with some errors. Please check the logs for details.", level="error", ctx=ctx)

def fetch_trakt_lists(ctx: SyncContext) -> list | None:
    log("Fetching Trakt lists...", ctx=ctx)

    if ctx.trakt_data and ctx.trakt_data.get("lists-lists") is not None:
        log("Using lists from provided trakt_data.", ctx=ctx)

        sorted_lists = []
        for trakt_list in ctx.trakt_data.get("lists-lists") or []:
            items = trakt_list.get("items") or []
            sorted_items = sorted(items, key=lambda x: parse_listed_at(x.get("listed_at")))
            sorted_lists.append({**trakt_list, "items": sorted_items})  # no in-place mutation

        return sorted_lists

    url = trakt_api_url + f"/users/{ctx.username}/lists"

    response = session.get(url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        lists = response.json()
        log(f"Trakt lists fetched successfully. Total lists: {len(lists)}", ctx=ctx)
        return lists
    else:
        log(f"Failed to fetch Trakt lists: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return []

def fetch_trakt_list(ctx: SyncContext, trakt_list: dict) -> list | None:
    url = trakt_api_url + f"/users/{ctx.username}/lists/{trakt_list.get('ids').get('trakt')}/items/all/added/asc"

    response = session.get(url, headers=ctx.trakt_headers)
    if response.status_code == 200:
        return response.json()
    else:
        log(f"Failed to fetch Trakt list: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return []

def add_list_to_pmdb(ctx: SyncContext, trakt_list: dict, trakt_list_items: list) -> bool:
    log(f"Adding list '{trakt_list.get('name')}' to PMDB...", level="verbose", ctx=ctx)

    url = pmdb_api_url + "/external/lists"
    body = {
        "name": trakt_list.get("name"),
        "description": trakt_list.get("description"),
        "is_public": True if trakt_list.get("privacy") == "public" else False,
        "type": "custom"
    }

    response = session.post(url, headers=ctx.pmdb_headers, json=body)
    if response.status_code >= 200 and response.status_code < 300 and response.json().get("success") and response.json().get("item").get("id"):
        pmdb_list = response.json()
        pmdb_list_id = pmdb_list.get("item").get("id")

        all_success = True

        for item in trakt_list_items:
            success = add_to_pmdb_list(ctx, pmdb_list_id, item)
            if not success:
                all_success = False
                log(f"Failed to add item '{item.get('movie', item.get('show', {})).get('title')}' to PMDB list '{trakt_list.get('name')}'.", level="error", ctx=ctx)

        if not all_success:
            log(f"List '{trakt_list.get('name')}' added to PMDB with some errors. Please check the logs for details.", level="error", ctx=ctx)

        return all_success
    else:
        log(f"Failed to create PMDB list '{trakt_list.get('name')}': {response.status_code} - {response.text}", level="error", ctx=ctx)
        return False

def sync_lists(ctx: SyncContext, sync_all: bool = True) -> bool:
    trakt_lists = fetch_trakt_lists(ctx)

    #sync_all = input("Do you want to sync all lists? (y/n): ").lower().replace(" ", "") == "y"
    if sync_all:
        log("Syncing all lists...", ctx=ctx)

    all_success = True

    for trakt_list in trakt_lists:
        if not sync_all:
            sync_list = input(f"Do you want to sync the list '{trakt_list.get('name')}' (https://trakt.tv/users/{ctx.username}/lists/{trakt_list.get('ids').get('trakt')})? (y/n): ").lower().replace(" ", "") == "y"
            if not sync_list:
                log(f"Skipping list '{trakt_list.get('name')}'...", ctx=ctx)
                continue

        log(f"Syncing list '{trakt_list.get('name')}'...", ctx=ctx)

        trakt_list_items = fetch_trakt_list(ctx, trakt_list)

        success = add_list_to_pmdb(ctx, trakt_list, trakt_list_items)

        if success:
            log(f"List '{trakt_list.get('name')}' synced successfully!", ctx=ctx)
        else:
            all_success = False
            log(f"Failed to sync list '{trakt_list.get('name')}'. Please check the logs for details.", level="error", ctx=ctx)

    return all_success

def submit_watched_timestamp_to_pmdb(ctx: SyncContext, tmdb_id: int, type: str, watched_at: str, season: int = None, episode: int = None) -> bool:
        url = pmdb_api_url + "/external/watched"

        if watched_at == "1970-01-01T00:00:00.000Z":
            watched_at = None # For when Trakt marks a movie with 'Unknown' watched date.

        body = {
            "media_type": type,
            "tmdb_id": tmdb_id,
            "watched_at": watched_at
        }

        if season and episode:
            body["season"] = season
            body["episode"] = episode

        response = session.post(url, headers=ctx.pmdb_headers, json=body)
        if response.status_code >= 200 and response.status_code < 300 and response.json().get("success"):
            return True
        else:
            log(f"Failed to submit watch history for TMDB ID {tmdb_id} to PMDB: {response.status_code} - {response.text}", level="error", ctx=ctx)
            return False
    
def submit_history_movie_to_pmdb(ctx: SyncContext, movie: dict) -> bool:

    tmdb_id = movie.get("movie", {}).get("ids", {}).get("tmdb")

    if not tmdb_id:
        body = {
            "id_type": "trakt",
            "id_value": movie.get("movie", {}).get("ids", {}).get("trakt")
        }

        id_response = session.get(pmdb_api_url + "/external/mappings/lookup", headers=ctx.pmdb_headers, json=body)
        if id_response.status_code == 200:
            tmdb_id = id_response.json().get("results", [{}])[0].get("tmdb_id")
    
    if movie.get("history", []):
        all_success = True

        for watch in movie.get("history", []):
            watched_at = watch.get("watched_at")

            if watched_at:
                success = submit_watched_timestamp_to_pmdb(ctx, tmdb_id, "movie", watched_at)
                if not success:
                    all_success = False
                    log(f"Failed to submit watch history for movie '{movie.get('movie', {}).get('title')}' (TMDB ID: {tmdb_id}) to PMDB.", level="error", ctx=ctx)
            else:
                all_success = False
                log(f"No 'watched_at' timestamp found for movie '{movie.get('movie', {}).get('title')}' (TMDB ID: {tmdb_id}). Skipping.", level="error", ctx=ctx)

        return all_success
    else:
        return submit_watched_timestamp_to_pmdb(ctx, tmdb_id, "movie", movie.get("last_watched_at", "1970-01-01T00:00:00.000Z"))

def sync_movie_watch_history(ctx: SyncContext) -> bool:
    log("Syncing movie watch history...", ctx=ctx)

    if ctx.trakt_data and ctx.trakt_data.get("watched-history") is not None:
        log("Using watched movies from provided trakt_data.", ctx=ctx)
        watch_history = ctx.trakt_data.get("watched-history")
        success = submit_exported_history_to_pmdb(ctx, "movie", watch_history)
        return success
    
    url = trakt_api_url + f"/users/{ctx.username}/watched/movies"
    
    response = session.request("GET", url, headers=ctx.trakt_headers)

    all_success = True

    if response.status_code == 200:
        watched_movies = response.json()
        for movie in watched_movies:
            if movie.get("plays", 5) > 1:
                url = trakt_api_url + f"/users/{ctx.username}/history/movies/{movie.get('movie', {}).get('ids', {}).get('trakt')}"
                response = session.request("GET", url, headers=ctx.trakt_headers)
                if response.status_code == 200:
                    movie_watch_times = response.json()
                    movie["history"] = movie_watch_times
                else:
                    log(f"Failed to fetch watch history for movie '{movie.get('movie', {}).get('title')}' (Trakt ID: {movie.get('movie', {}).get('ids', {}).get('trakt')}): {response.status_code} - {response.text}", level="error", ctx=ctx)
                    all_success = False

            success = submit_history_movie_to_pmdb(ctx, movie)
            if not success:
                all_success = False
        return all_success

    else:
        log(f"Failed to fetch watched movies: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return False
    
def add_show_watch_history(ctx: SyncContext, show: dict) -> dict:
    for season in show.get("seasons", []):

        season_details = None
        for episode in season.get("episodes", []):
            if episode.get("plays", 5) > 1:

                if not season_details:
                    url = trakt_api_url + f"/shows/{show.get('show', {}).get('ids', {}).get('trakt')}/seasons/{season.get('number')}"
                    response = session.request("GET", url, headers=ctx.trakt_headers)
                    if response.status_code == 200:
                        season_details = response.json()
                    else:
                        log(f"Failed to fetch season details for show '{show.get('show', {}).get('title')}' season {season.get('number')}: {response.status_code} - {response.text}", level="error", ctx=ctx)
                        continue

                for detailed_episode in season_details:
                    if detailed_episode.get("number", 0) == episode.get("number", 0):
                        url = trakt_api_url + f"/users/{ctx.username}/history/episodes/{detailed_episode.get('ids', {}).get('trakt')}"
                        response = session.request("GET", url, headers=ctx.trakt_headers)
                        if response.status_code == 200:
                            episode_watch_times = response.json()
                            episode["history"] = episode_watch_times
                        else:
                            log(f"Failed to fetch watch history for episode '{episode.get('title')}' (Trakt ID: {episode.get('ids', {}).get('trakt')}): {response.status_code} - {response.text}", level="error", ctx=ctx)

    return show

def submit_history_show_to_pmdb(ctx: SyncContext, show: dict) -> bool:
    tmdb_id = show.get("show", {}).get("ids", {}).get("tmdb")

    if not tmdb_id:
        body = {
            "id_type": "trakt",
            "id_value": show.get("show", {}).get("ids", {}).get("trakt"),
            "media_type": "tv"
        }

        id_response = session.get(pmdb_api_url + "/external/mappings/lookup", headers=ctx.pmdb_headers, json=body)
        if id_response.status_code == 200:
            tmdb_id = id_response.json().get("results", [{}])[0].get("tmdb_id")

    all_success = True

    for season in show.get("seasons", []):
        for episode in season.get("episodes", []):
            if episode.get("history", []):
                for watch in episode.get("history", []):
                    watched_at = watch.get("watched_at")

                    if watched_at:
                        success = submit_watched_timestamp_to_pmdb(ctx, tmdb_id, "tv", watched_at, season=season.get("number"), episode=episode.get("number"))
                        if not success:
                            all_success = False
                            log(f"Failed to submit watch history for episode '{episode.get('title')}' (TMDB ID: {tmdb_id}) to PMDB.", level="error", ctx=ctx)
                    else:
                        all_success = False
                        log(f"No 'watched_at' timestamp found for episode '{episode.get('title')}' (TMDB ID: {tmdb_id}). Skipping.", level="error", ctx=ctx)
            else:
                success = submit_watched_timestamp_to_pmdb(ctx, tmdb_id, "tv", episode.get("last_watched_at", "1970-01-01T00:00:00.000Z"), season=season.get("number"), episode=episode.get("number"))
                if not success:
                    all_success = False
                    log(f"Failed to submit watch history for episode '{episode.get('title')}' (TMDB ID: {tmdb_id}) to PMDB.", level="error", ctx=ctx)

    return all_success

def submit_exported_history_to_pmdb(ctx: SyncContext, media_type: str, history: list) -> bool:
    all_success = True

    if media_type not in ["movie", "episode"]:
        log(f"Invalid media type '{media_type}' for history export. Skipping.", level="error", ctx=ctx)
        return False

    for item in history:
        if item.get("type") == media_type:
            watched_at = item.get("watched_at", "1970-01-01T00:00:00.000Z")
            tmdb_id = item.get("movie", item.get("show", {})).get("ids", {}).get("tmdb")

            if not tmdb_id:
                body = {
                    "id_type": "trakt",
                    "id_value": item.get("movie", item.get("show", {})).get("ids", {}).get("trakt"),
                    "media_type": "movie" if item.get("type") == "movie" else "tv"
                }

                id_response = session.get(pmdb_api_url + "/external/mappings/lookup", headers=ctx.pmdb_headers, json=body)
                if id_response.status_code == 200:
                    tmdb_id = id_response.json().get("results", [{}])[0].get("tmdb_id")

            success = False
            if item.get("type") == "movie":
                success = submit_watched_timestamp_to_pmdb(ctx=ctx, tmdb_id=tmdb_id, type="movie", watched_at=watched_at)
            else:
                success = submit_watched_timestamp_to_pmdb(ctx=ctx, tmdb_id=tmdb_id, type="tv", watched_at=watched_at, season=item.get("episode", {}).get("season"), episode=item.get("episode", {}).get("number"))

            if not success:
                all_success = False
                log(f"Failed to submit watch history for '{item.get('movie', item.get('show', {})).get('title')}' (TMDB ID: {tmdb_id}) to PMDB.", level="error", ctx=ctx)

    return all_success

def sync_show_watch_history(ctx: SyncContext) -> bool:
    log("Syncing show watch history...", ctx=ctx)

    if ctx.trakt_data and ctx.trakt_data.get("watched-history") is not None:
        log("Using watched shows from provided trakt_data.", ctx=ctx)
        watch_history = ctx.trakt_data.get("watched-history")
        success = submit_exported_history_to_pmdb(ctx, "episode", watch_history)
        return success

    url = trakt_api_url + f"/users/{ctx.username}/watched/shows"

    response = session.request("GET", url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        watched_shows = response.json()

        all_success = True
        for show in watched_shows:
            show = add_show_watch_history(ctx, show)
            success = submit_history_show_to_pmdb(ctx, show)
            if not success:
                all_success = False
        
        if all_success:
            log("Show watch history synced successfully!", ctx=ctx)
        else:
            log("Show watch history synced with some errors. Please check the logs for details.", level="error", ctx=ctx)

        return all_success
    else:
        log(f"Failed to fetch watched shows: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return False
    
def submit_resume_point_to_pmdb(ctx: SyncContext, item: dict) -> bool:

    item_type = "movie" if item.get("type") == "movie" else "tv"
    item_spesific = item.get("movie", item.get("show", {}))
    ids = item_spesific.get("ids", {})

    # Normalize progress percentage to runtime and position in milliseconds for PMDB, since it doesn't support percentage-based resume points.
    percentage = item.get("progress", 0)
    runtime_ms = 100 * 10000 # Full 100% complete
    position_ms = percentage * 10000 # Normalized to account for decimal points
    position_ms = round(position_ms) # Round to nearest millisecond

    url = pmdb_api_url + "/external/resume"
    body = {
        "media_type": item_type,
        "tmdb_id": ids.get("tmdb"),
        "id_type": "trakt",
        "id_value": ids.get("trakt"),
        "position_ms": position_ms,
        "runtime_ms": runtime_ms
    }

    if item_type == "tv":
        body["season"] = item.get("episode", {}).get("season")
        body["episode"] = item.get("episode", {}).get("number")

    response = session.post(url, headers=ctx.pmdb_headers, json=body)
    if response.status_code >= 200 and response.status_code < 300 and response.json().get("action") == "saved":
        return True
    else:
        media_label = "Movie" if item_type == "movie" else "Show"
        log(f"Failed to submit resume point for {media_label} '{item_spesific.get('title')}' (Trakt ID: {ids.get('trakt')}) to PMDB: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return False

def sync_show_resume_points(ctx: SyncContext) -> bool:
    log("Syncing show resume points...", ctx=ctx)

    progress_data = []
    if ctx.trakt_data and ctx.trakt_data.get("watched-playback") is not None:
        log("Using show resume points from provided trakt_data.", ctx=ctx)
        resume_points = ctx.trakt_data.get("watched-playback")
        sorted_resume_points = sorted(resume_points, key=lambda x: parse_listed_at(x.get("paused_at")), reverse=False)
        progress_data = [item for item in sorted_resume_points if item.get("type") == "episode"]

    url = trakt_api_url + "/sync/playback/episodes"
    response = session.get(url, headers=ctx.trakt_headers) if not progress_data else None

    if response is None or response.status_code == 200:
        progress_data = response.json() if progress_data == [] else progress_data

        all_success = True

        for show in progress_data:
            success = submit_resume_point_to_pmdb(ctx, show)
            if not success:
                all_success = False

        if all_success:
            log("Show resume points synced successfully!", ctx=ctx)
        else:
            log("Show resume points synced with some errors. Please check the logs for details.", level="error", ctx=ctx)

        return all_success
    else:
        log(f"Failed to fetch show resume points: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return False

def sync_movie_resume_points(ctx: SyncContext) -> bool:
    log("Syncing movie resume points...", ctx=ctx)

    progress_data = []
    if ctx.trakt_data and ctx.trakt_data.get("watched-playback") is not None:
        log("Using movie resume points from provided trakt_data.", ctx=ctx)
        resume_points = ctx.trakt_data.get("watched-playback")
        sorted_resume_points = sorted(resume_points, key=lambda x: parse_listed_at(x.get("paused_at")), reverse=False)
        progress_data = [item for item in sorted_resume_points if item.get("type") == "movie"]

    url = trakt_api_url + "/sync/playback/movies"
    response = session.get(url, headers=ctx.trakt_headers) if not progress_data else None

    if response is None or response.status_code == 200:
        progress_data = response.json() if progress_data == [] else progress_data

        all_success = True

        for movie in progress_data:
            success = submit_resume_point_to_pmdb(ctx, movie)
            if not success:
                all_success = False

        if all_success:
            log("Movie resume points synced successfully!", ctx=ctx)
        else:
            log("Movie resume points synced with some errors. Please check the logs for details.", level="error", ctx=ctx)

        return all_success
    else:
        log(f"Failed to fetch movie resume points: {response.status_code} - {response.text}", level="error", ctx=ctx)
        return False