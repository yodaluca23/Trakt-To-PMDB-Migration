# Trakt To Pmdb Migration Utility

Migrate your Trakt data into The PublicMetaDb.

This project connects to Trakt using the Device Code flow, stores your token locally, then pushes selected data to The PublicMetaDb.

## What it Migrates

- Watchlist
- Custom Trakt lists
- Movie and Show watch history
- Resume points (progress) for movies and shows

The interactive entrypoint is `user.py`.

## Planned

Planned features and improvements:

- Only migrate select lists.
- A desktop GUI so migration can be run without using the terminal.
- ~~A website interface for account connection and migration management.~~
- Packaged desktop builds:
	- Windows `.exe`
	- macOS `.dmg`

## Requirements

- Python 3.9+
- A Trakt API app (client ID + client secret)
- A PMDB API key

## Local User Setup

1. Clone this repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Create a `.env` file from `.env.example`.

For the CLI migration flow (`user.py`), install from `requirements_user.txt`.
For the web UI (`webserver.py`), install from `requirements.txt`.

Example:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_user.txt
# or, for webserver usage:
# pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env`:

```env
pmdb_api_key=Required
trakt_client=Required
trakt_secret=Required
```

## Local User First run and authentication

Run:

```bash
python user.py
```

On first run:

1. The script requests a Trakt device code.
2. Your browser opens the Trakt verification page.
3. After approval, the token is written to `token.json`.

On later runs:

- If `token.json` is still valid, it is reused.
- If expired, it is refreshed automatically.

### How migration works

When you run `user.py`, you can either:

- Migrate everything in one go, or
- Choose each area individually:
	- Watchlist
	- Lists
	- Show watch history
	- Movie watch history
    - Show resume points
    - Movie resume points

## Webserver Usage

The web UI is served by `webserver.py` and uses browser cookies for auth state.

### 1. Configure `.env` for web mode

Required values:

```env
trakt_client=Required
trakt_secret=Required
domain=http://127.0.0.1:8000
trakt_redirect_uri=/trakt/callback
cookie_encryption_key=Required
```
You can generate a random `cookie_encryption_key` using Fernet:

```python
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Notes:

- Keep `domain` aligned with where you actually run the app.
- If you host behind HTTPS in production, set `domain` to your HTTPS URL.

### 2. Start the webserver

From the project root:

```bash
source .venv/bin/activate
pip install -r requirements.txt
fastapi run webserver.py
```

Then open `http://127.0.0.1:8000` in your browser.

### 3. Authenticate accounts in the UI

1. Click the Trakt status text and complete OAuth.
2. Click the PublicMetaDb status text and paste your PMDB API key.
3. Wait until both indicators turn green.

### 4. Start a migration job

1. Select the migration options you want.
2. Click **Submit**.
3. Watch live progress indicators and the progress bar.
4. Use **View Migration Logs** to inspect job output.

### 5. Reconnect behavior

- If you refresh, the UI resumes from stored job state when possible.
- Trakt tokens are refreshed automatically when needed.
- If auth expires or cookies are invalid, re-authenticate from the status links.

## Other scripts

- `main.py`: core API/auth/migration implementation.
- `debug.py`: utility that clears PublicMetaDb watch history entries.

Use `debug.py` carefully because it deletes watch history records from The PublicMetaDb.

## Notes and current behavior

- PublicMetaDb list creation for custom Trakt lists currently creates new PublicMetaDb lists as part of migration.
- Mapping from Trakt IDs to TMDB IDs is attempted when TMDB IDs are missing.
- Console output is the primary logging mechanism.
- Authorization tokens are stored in `token.json` for reuse and refresh. Treat this file as sensitive since it contains OAuth tokens.

## Troubleshooting

- `401` or auth errors:
	- Check `.env` values.
	- Delete `token.json` and run again to re-authorize.
- Items not migrating:
	- Confirm those items exist in Trakt and are visible for the authenticated account.
	- Check terminal output for PMDB API errors.
- Dependency import errors:
	- Re-activate your virtual environment.
	- Reinstall packages with the correct file:
		- `pip install -r requirements_user.txt` for CLI (`user.py`)
		- `pip install -r requirements.txt` for webserver (`webserver.py`)

## Security

- Never commit real secrets in `.env`.
- `token.json` contains OAuth tokens; treat it as sensitive.
- Their is no CSRF protection on the webserver endpoints.
- Their is no content security policy on the webserver endpoints.
- The webserver should only be run locally behind a trusted firewall or in production with proper security measures in place.
