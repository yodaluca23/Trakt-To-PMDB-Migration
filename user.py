import os
import json
import requests
from datetime import datetime
from main import code_authorize_user, sync_lists, sync_movie_resume_points, sync_movie_watch_history, sync_show_resume_points, sync_show_watch_history, sync_watchlist, add_user_information, create_trakt_headers, build_sync_context, trakt_api_url

def check_for_existing_token() -> dict | None:
    global trakt_api_url

    if os.path.exists("token.json"):
        with open("token.json", "r") as token_file:
            token_data = json.load(token_file)
            expires_at = token_data.get("created_at") + token_data.get("expires_in")
            if datetime.now().timestamp() < expires_at - 60:  # Check if token is still valid (with a 60-second buffer)
                print("Existing token found and is still valid.")
                return token_data
            else:
                print("Existing token found but has expired. Refreshing token...")
                headers = create_trakt_headers()
                payload = {
                    "client_id": os.getenv("trakt_client"),
                    "client_secret": os.getenv("trakt_secret"),
                    "refresh_token": token_data.get("refresh_token"),
                    "grant_type": "refresh_token"
                }
                response = requests.post(trakt_api_url + "/oauth/token", json=payload, headers=headers)
                if response.status_code == 200:
                    new_token_data = response.json()

                    trakt_headers = create_trakt_headers(new_token_data)
                    user_data = add_user_information(new_token_data, trakt_headers)

                    with open("token.json", "w") as token_file:
                        json.dump(user_data, token_file, indent=4)
                    print("Token refreshed successfully.")
                    return user_data
                else:
                    print(f"Failed to refresh token: {response.status_code} - {response.text}")
                    return None
    else:
        print("No existing token found. Please authorize the user.")
        return None

def clean_bool_input(prompt: str) -> bool:
    return input(prompt).lower().replace(" ", "") == "y"

if __name__ == "__main__":

    user_data = check_for_existing_token()

    if not user_data:
        user_data = code_authorize_user()

    with open("token.json", "w") as token_file:
        json.dump(user_data, token_file, indent=4)

    ctx = build_sync_context(user_data, os.getenv("pmdb_api_key"))

    sync_all = clean_bool_input("Do you want to sync all data (watchlist, lists, watch history, and resume points)? (y/n): ")

    sync_lists_choice = sync_show_watch_history_choice = sync_movie_watch_history_choice = sync_watchlist_choice = sync_movie_resume_points_choice = sync_show_resume_points_choice = True

    if not sync_all:
        sync_watchlist_choice = clean_bool_input("Do you want to sync your watchlist? (y/n): ")
        sync_lists_choice = clean_bool_input("Do you want to sync all your lists? (y/n): ")
        sync_show_watch_history_choice = clean_bool_input("Do you want to sync your show watch history? (y/n): ")
        sync_movie_watch_history_choice = clean_bool_input("Do you want to sync your movie watch history? (y/n): ")
        sync_show_resume_points_choice = clean_bool_input("Do you want to sync your show progress/resume points? (y/n): ")
        sync_movie_resume_points_choice = clean_bool_input("Do you want to sync your movie progress/resume points? (y/n): ")

    if sync_watchlist_choice:
        sync_watchlist(ctx)
    if sync_lists_choice:
        sync_lists(ctx)
    if sync_show_watch_history_choice:
        sync_show_watch_history(ctx)
    if sync_movie_watch_history_choice:
        sync_movie_watch_history(ctx)
    if sync_show_resume_points_choice:
        sync_show_resume_points(ctx)
    if sync_movie_resume_points_choice:
        sync_movie_resume_points(ctx)