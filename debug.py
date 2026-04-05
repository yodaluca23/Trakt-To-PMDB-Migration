import json
from main import authorize_user, pmdb_api_url, pmdb_headers
import requests

token_data = authorize_user()

def clear_watch_history():
    url = f"{pmdb_api_url}/external/watched"
    response = requests.get(url, headers=pmdb_headers)
    if response.status_code == 200:
        watched_items = response.json()
        for item in watched_items.get("items", []):
            delete_url = f"{pmdb_api_url}/external/watched/{item['id']}"
            delete_response = requests.delete(delete_url, headers=pmdb_headers)
            if delete_response.status_code == 200:
                print(f"Deleted watch history item with ID: {item['id']}")
            else:
                print(f"Failed to delete watch history item with ID: {item['id']}. Status code: {delete_response.status_code}")

clear_watch_history()