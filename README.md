# Decades Tracker v2.0 — Neon Edition

This is the recovered Decades Tracker application with Neon PostgreSQL as its live storage backend.

## Current cloud save

The latest intact populated SQLite database was migrated to an isolated Neon schema. The migration preserved and verified 78,264 rows across 13 tables, including 1,023 Sims.

Local SQLite databases are not modified or deleted during migration and remain useful as offline safety copies.

## Windows quick start

1. Download or clone this repository.
2. Double-click `Launch Decades Tracker.bat`.
3. The first launch installs a private Python runtime and required packages.
4. Paste a Neon pooled connection string when prompted, or create a local `.env` file from `.env.example`.

## Configuration

Copy `.env.example` to `.env` and replace the placeholder with your Neon pooled connection string:

```env
NEON_DATABASE_URL=postgresql://USER:PASSWORD@YOUR-POOLER-HOST/neondb?sslmode=require
```

The `.env` file, local Neon state, databases, saves, exports, and private runtime are ignored by Git. Never commit a real connection string.

## Storage behavior

- Each tracker save uses a separate PostgreSQL schema.
- Save creation, duplication, renaming, deletion, import, and export operate against Neon.
- Shareable `.decades-save` exports remain SQLite-compatible for portability.
- The app uses pooled connections for responsive cloud-backed pages.
- Automatic roll scheduling runs when relevant tracker data changes or when requested, rather than on every page rerun.

## Version

Current source version: **3.6.0**
