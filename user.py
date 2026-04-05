from main import sync_lists, sync_movie_resume_points, sync_movie_watch_history, sync_show_resume_points, sync_show_watch_history, sync_watchlist

def clean_bool_input(prompt):
    return input(prompt).lower().replace(" ", "") == "y"

if __name__ == "__main__":

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
        sync_watchlist()
    if sync_lists_choice:
        sync_lists()
    if sync_show_watch_history_choice:
        sync_show_watch_history()
    if sync_movie_watch_history_choice:
        sync_movie_watch_history()
    if sync_show_resume_points_choice:
        sync_show_resume_points()
    if sync_movie_resume_points_choice:
        sync_movie_resume_points()