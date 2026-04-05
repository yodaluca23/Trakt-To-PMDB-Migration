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
- A website interface for account connection and migration management.
- Packaged desktop builds:
	- Windows `.exe`
	- macOS `.dmg`

## Requirements

- Python 3.9+
- A Trakt API app (client ID + client secret)
- A PMDB API key

## Setup

1. Clone this repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Create a `.env` file from `.env.example`.

Example:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env`:

```env
pmdb_api_key=YOUR_PMDB_API_KEY
trakt_client=YOUR_TRAKT_CLIENT_ID
trakt_secret=YOUR_TRAKT_CLIENT_SECRET
```

## First run and authentication

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

## How migration works

When you run `user.py`, you can either:

- Migrate everything in one go, or
- Choose each area individually:
	- Watchlist
	- Lists
	- Show watch history
	- Movie watch history
    - Show resume points
    - Movie resume points

## Other scripts

- `main.py`: core API/auth/migration implementation.
- `debug.py`: utility that clears PublicMetaDb watch history entries.

Use `debug.py` carefully because it deletes watch history records from The PublicMetaDb.

## Notes and current behavior

- PublicMetaDb list creation for custom Trakt lists currently creates new PublicMetaDb lists as part of migration.
- Mapping from Trakt IDs to TMDB IDs is attempted when TMDB IDs are missing.
- Console output is the primary logging mechanism.

## Troubleshooting

- `401` or auth errors:
	- Check `.env` values.
	- Delete `token.json` and run again to re-authorize.
- Items not migrating:
	- Confirm those items exist in Trakt and are visible for the authenticated account.
	- Check terminal output for PMDB API errors.
- Dependency import errors:
	- Re-activate your virtual environment.
	- Reinstall packages with `pip install -r requirements.txt`.

## Security

- Never commit real secrets in `.env`.
- `token.json` contains OAuth tokens; treat it as sensitive.
