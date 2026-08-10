# SeveralUDO / Decades Tracker v2.0

A Windows-first local Streamlit tracker for Sims historical / Decades-style challenges, with Neon PostgreSQL cloud save storage.

## Features

- Multiple completely independent cloud saves
- Different historical starting years per save
- Sims, portraits, households, pregnancies, relationships, rolls, and events
- Family Tree 2.0 with ancestor / descendant views and portrait nodes
- Historical event library and event rolling workflow
- Automatic roll scheduling with configurable era/species roll tables
- Timeline and detailed statistics
- Offline name randomizer sourced from the bundled Decades Names library
- Portable `.decades-save` export/import
- Migration from existing v1.x SQLite saves into Neon

## Windows quick start

1. Download or clone this repository.
2. Double-click `Launch Decades Tracker.bat`.
3. The first launch installs a private Python runtime and dependencies into `.runtime/`.
4. Paste your Neon PostgreSQL connection string into the setup screen.
5. Create a new cloud save or migrate an existing local save.

## Neon configuration

Copy `.env.example` to `.env` and fill in your own credentials, or enter the connection string in the app.

```env
NEON_DATABASE_URL=postgresql://USER:PASSWORD@YOUR-POOLER-HOST/neondb?sslmode=require
NEON_DIRECT_URL=postgresql://USER:PASSWORD@YOUR-DIRECT-HOST/neondb?sslmode=require
```

Never commit `.env` or `.neon_storage.json`. Both are ignored by `.gitignore`.

## Existing v1.x users

Keep your old `decades.db`, `saves/`, and `saves.json` files while upgrading. v2 can discover those SQLite saves and copy them into Neon without deleting the originals.

## Save sharing

Use **Saves → Manage → Export portable `.decades-save`**. The exported world includes its Sims, portraits, relationships, rules, events, and calendar state, but never includes your Neon credentials.

## Data and secrets excluded from Git

The repository intentionally ignores local databases, local saves, `.decades-save` exports, `.env`, `.neon_storage.json`, and the private Python runtime. Fresh installs use `starter_seed.json` for the bundled rules/event library instead of committing a template database.

## Version

Current source version: **2.0.0**

See `README_FIRST.txt` and `USER_GUIDE.txt` for more detailed usage instructions.
