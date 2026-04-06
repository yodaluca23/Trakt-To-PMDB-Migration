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

    @property
    def username(self):
        return self.token_data.get("user_info", {}).get("user", {}).get("username")

def create_trakt_headers(token_data: dict = None):
    headers = {
        "trakt-api-version": "2",
        "trakt-api-key": os.getenv("trakt_client")
    }

    if token_data and token_data.get("access_token") and token_data.get("token_type"):
        headers["Authorization"] = f"{token_data.get('token_type')} {token_data.get('access_token')}"

    return headers

def build_sync_context(token_data: dict, pmdb_api_key: str):
    return SyncContext(
        token_data=token_data,
        trakt_headers=create_trakt_headers(token_data),
        pmdb_headers={"Authorization": "Bearer " + pmdb_api_key}
    )

def add_user_information(token_data: dict, trakt_headers: dict):

    response = session.get(trakt_api_url + "/users/settings", headers=trakt_headers)
    if response.status_code == 200:
        user_info = response.json()
        print(f"User information retrieved: {user_info.get('user').get('username')}")
        token_data["user_info"] = user_info
        return token_data
    else:
        print(f"Failed to retrieve user information: {response.status_code} - {response.text}")
        return None

def code_authorize_user():
    global trakt_api_url, userAgent
    print("Authorizing user...")

    client_id = os.getenv("trakt_client")
    client_secret = os.getenv("trakt_secret")
    
    payload = {"client_id": client_id}

    response = session.request("POST", trakt_api_url + "/oauth/device/code", json=payload)

    verification_url = response.json().get("verification_url")
    user_code = response.json().get("user_code")
    device_code = response.json().get("device_code")
    interval = response.json().get("interval")

    open_new_tab(verification_url + "?code=" + user_code)

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
            print("User authorized successfully!")

            trakt_auth_headers = create_trakt_headers(json_response)
            user_data = add_user_information(json_response, trakt_auth_headers)

            return user_data
        elif response.status_code == 400:
            print("Waiting for user authorization...")
        else:
            print(f"Error: {response.status_code} - {response.text}")
            break

def fetch_watchlist(ctx: SyncContext):
    print("Fetching watchlist...")

    url = trakt_api_url + f"/users/{ctx.username}/watchlist/all/added/asc"

    response = session.get(url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        watchlist = response.json()
        print(f"Watchlist fetched successfully. Total items: {len(watchlist)}")
        return watchlist

def get_pmdb_watchlist_id(ctx: SyncContext):
    print("Retrieving PMDB watchlist ID...")
    url = pmdb_api_url + "/external/lists"

    response = session.get(url, headers=ctx.pmdb_headers)

    if response.status_code == 200:
        lists = response.json()
        for watchlist in lists.get("items", [{}]):
            if watchlist.get("type") == "watchlist":
                print(f"Found existing PMDB watchlist with ID: {watchlist.get('id')}")
                return watchlist.get("id")
        print("No existing PMDB watchlist found. A new one will be created.")

        url = pmdb_api_url + "/external/lists"
        body = {
            "name": "Watchlist",
            "is_public": True,
            "type": "watchlist"
        }
        response = session.post(url, headers=ctx.pmdb_headers, json=body)
        if response.status_code >= 200 and response.status_code < 300 and response.json().get("success") and response.json().get("item").get("id"):
            new_watchlist = response.json()
            print(f"New PMDB watchlist created with ID: {new_watchlist.get('item').get('id')}")
            return new_watchlist.get("item").get("id")
        else:
            print(f"Failed to create PMDB watchlist: {response.status_code} - {response.text}")
        return None

def add_to_pmdb_list(ctx: SyncContext, pmdb_list_id: str, item: dict):

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
        print(f"Added '{item.get('movie', item.get('show', {})).get('title')}' to PMDB watchlist.")
        return True
    else:
        print(f"Failed to add '{item.get('movie', item.get('show', {})).get('title')}' to PMDB watchlist: {response.status_code} - {response.text}")
    return False

def sync_watchlist(ctx: SyncContext):
    print("Syncing watchlist...")
    watchlist = fetch_watchlist(ctx)
    pmdb_watchlist_id = get_pmdb_watchlist_id(ctx)

    all_success = True

    for item in watchlist:
        success = add_to_pmdb_list(ctx, pmdb_watchlist_id, item)

        if not success:
            all_success = False

    if all_success:
        print("Watchlist synced successfully!")
    else:
        print("Watchlist synced with some errors. Please check the logs for details.")

def fetch_trakt_lists(ctx: SyncContext):
    print("Fetching Trakt lists...")
    url = trakt_api_url + f"/users/{ctx.username}/lists"

    response = session.get(url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        lists = response.json()
        print(f"Trakt lists fetched successfully. Total lists: {len(lists)}")
        return lists
    else:
        print(f"Failed to fetch Trakt lists: {response.status_code} - {response.text}")
        return []

def fetch_trakt_list(ctx: SyncContext, trakt_list: dict):
    url = trakt_api_url + f"/users/{ctx.username}/lists/{trakt_list.get('ids').get('trakt')}/items/all/added/asc"

    response = session.get(url, headers=ctx.trakt_headers)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to fetch Trakt list: {response.status_code} - {response.text}")
        return []

def add_list_to_pmdb(ctx: SyncContext, trakt_list: dict, trakt_list_items: list):
    print(f"Adding list '{trakt_list.get('name')}' to PMDB...")

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
                print(f"Failed to add item '{item.get('movie', item.get('show', {})).get('title')}' to PMDB list '{trakt_list.get('name')}'.")

        if not all_success:
            print(f"List '{trakt_list.get('name')}' added to PMDB with some errors. Please check the logs for details.")

        return all_success
    else:
        print(f"Failed to create PMDB list '{trakt_list.get('name')}': {response.status_code} - {response.text}")
        return False

def sync_lists(ctx: SyncContext, sync_all: bool = True):
    trakt_lists = fetch_trakt_lists(ctx)

    #sync_all = input("Do you want to sync all lists? (y/n): ").lower().replace(" ", "") == "y"
    if sync_all:
        print("Syncing all lists...")

    for trakt_list in trakt_lists:
        if not sync_all:
            sync_list = input(f"Do you want to sync the list '{trakt_list.get('name')}' (https://trakt.tv/users/{ctx.username}/lists/{trakt_list.get('ids').get('trakt')})? (y/n): ").lower().replace(" ", "") == "y"
            if not sync_list:
                print(f"Skipping list '{trakt_list.get('name')}'...")
                continue

        print(f"Syncing list '{trakt_list.get('name')}'...")

        trakt_list_items = fetch_trakt_list(ctx, trakt_list)

        success = add_list_to_pmdb(ctx, trakt_list, trakt_list_items)

        if success:
            print(f"List '{trakt_list.get('name')}' synced successfully!")
        else:
            print(f"Failed to sync list '{trakt_list.get('name')}'. Please check the logs for details.")

def submit_watched_timestamp_to_pmdb(ctx: SyncContext, tmdb_id: int, type: str, watched_at: str, season: int = None, episode: int = None):
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
            print(f"Failed to submit watch history for TMDB ID {tmdb_id} to PMDB: {response.status_code} - {response.text}")
            return False
    
def submit_history_movie_to_pmdb(ctx: SyncContext, movie: dict):

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
                    print(f"Failed to submit watch history for movie '{movie.get('movie', {}).get('title')}' (TMDB ID: {tmdb_id}) to PMDB.")
            else:
                all_success = False
                print(f"No 'watched_at' timestamp found for movie '{movie.get('movie', {}).get('title')}' (TMDB ID: {tmdb_id}). Skipping.")

        return all_success
    else:
        return submit_watched_timestamp_to_pmdb(ctx, tmdb_id, "movie", movie.get("last_watched_at", "1970-01-01T00:00:00.000Z"))

def sync_movie_watch_history(ctx: SyncContext):

    print("Syncing movie watch history...")

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
                    print(f"Failed to fetch watch history for movie '{movie.get('movie', {}).get('title')}' (Trakt ID: {movie.get('movie', {}).get('ids', {}).get('trakt')}): {response.status_code} - {response.text}")
                    all_success = False

            success = submit_history_movie_to_pmdb(ctx, movie)
            if not success:
                all_success = False
        return all_success

    else:
        print(f"Failed to fetch watched movies: {response.status_code} - {response.text}")
        return False
    
def add_show_watch_history(ctx: SyncContext, show: dict):

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
                        print(f"Failed to fetch season details for show '{show.get('show', {}).get('title')}' season {season.get('number')}: {response.status_code} - {response.text}")
                        continue

                for detailed_episode in season_details:
                    if detailed_episode.get("number", 0) == episode.get("number", 0):
                        url = trakt_api_url + f"/users/{ctx.username}/history/episodes/{detailed_episode.get('ids', {}).get('trakt')}"
                        response = session.request("GET", url, headers=ctx.trakt_headers)
                        if response.status_code == 200:
                            episode_watch_times = response.json()
                            episode["history"] = episode_watch_times
                        else:
                            print(f"Failed to fetch watch history for episode '{episode.get('title')}' (Trakt ID: {episode.get('ids', {}).get('trakt')}): {response.status_code} - {response.text}")

    return show

def submit_history_show_to_pmdb(ctx: SyncContext, show: dict):

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
                            print(f"Failed to submit watch history for episode '{episode.get('title')}' (TMDB ID: {tmdb_id}) to PMDB.")
                    else:
                        all_success = False
                        print(f"No 'watched_at' timestamp found for episode '{episode.get('title')}' (TMDB ID: {tmdb_id}). Skipping.")
            else:
                success = submit_watched_timestamp_to_pmdb(ctx, tmdb_id, "tv", episode.get("last_watched_at", "1970-01-01T00:00:00.000Z"), season=season.get("number"), episode=episode.get("number"))
                if not success:
                    all_success = False
                    print(f"Failed to submit watch history for episode '{episode.get('title')}' (TMDB ID: {tmdb_id}) to PMDB.")

    return all_success

def sync_show_watch_history(ctx: SyncContext):
    print("Syncing show watch history...")

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
            print("Show watch history synced successfully!")
        else:
            print("Show watch history synced with some errors. Please check the logs for details.")
    else:
        print(f"Failed to fetch watched shows: {response.status_code} - {response.text}")

def submit_resume_point_to_pmdb(ctx: SyncContext, item: dict):

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
        print(f"Failed to submit resume point for {media_label} '{item_spesific.get('title')}' (Trakt ID: {ids.get('trakt')}) to PMDB: {response.status_code} - {response.text}")
        return False

def sync_show_resume_points(ctx: SyncContext):
    print("Syncing show resume points...")

    url = trakt_api_url + "/sync/playback/episodes"
    response = session.get(url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        progress_data = response.json()

        all_success = True

        for show in progress_data:
            success = submit_resume_point_to_pmdb(ctx, show)
            if not success:
                all_success = False

        if all_success:
            print("Show resume points synced successfully!")
        else:
            print("Show resume points synced with some errors. Please check the logs for details.")
    else:
        print(f"Failed to fetch show resume points: {response.status_code} - {response.text}")
        all_success = False

    return all_success

def sync_movie_resume_points(ctx: SyncContext):
    print("Syncing movie resume points...")

    url = trakt_api_url + "/sync/playback/movies"
    response = session.get(url, headers=ctx.trakt_headers)

    if response.status_code == 200:
        progress_data = response.json()

        all_success = True

        for movie in progress_data:
            success = submit_resume_point_to_pmdb(ctx, movie)
            if not success:
                all_success = False

        if all_success:
            print("Movie resume points synced successfully!")
        else:
            print("Movie resume points synced with some errors. Please check the logs for details.")
    else:
        print(f"Failed to fetch movie resume points: {response.status_code} - {response.text}")
        all_success = False

    return all_success