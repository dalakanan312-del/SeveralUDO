DECADES TRACKER v2.0 — NEON CLOUD STORAGE
==========================================

Decades Tracker v2.0 moves live save storage from local SQLite databases to
Neon PostgreSQL.

WHAT CHANGED
------------
- Neon is the live database.
- Each challenge save is stored in its own PostgreSQL schema.
- Multiple saves remain completely isolated.
- The same Neon project can be opened from another computer by using the same
  connection string.
- Sim portraits are stored in PostgreSQL BYTEA data inside the selected save.
- .decades-save export/import is still supported for portable backups/sharing.
- Existing v1.x SQLite saves can be copied into Neon from the app.
- Original SQLite files are not deleted or edited by migration.

FIRST LAUNCH
------------
1. Extract the entire app folder.
2. Double-click Launch Decades Tracker.bat.
3. The first v2 launch installs the Psycopg PostgreSQL driver into the app's
   private Python runtime.
4. The app opens a Neon connection setup page.
5. In the Neon dashboard, copy your connection string and paste it into the app.
6. A pooled Neon connection string is recommended for normal app traffic.
7. A direct/non-pooler connection string can optionally be provided for schema
   setup and migrations.
8. Click Test connection & use Neon.

The connection string contains your Neon database password. It is stored only
in this local app folder in:
  .neon_storage.json

Do NOT publish or share that file.

MIGRATING AN EXISTING TRACKER
-----------------------------
After Neon connects, v2 looks for populated v1.x databases in the local saves/
folder.

If found:
1. Select the saves you want.
2. Click Migrate selected saves to Neon.
3. Wait for the copy to finish.
4. The original .db files remain untouched as local safety backups.

For a v1.x multi-save installation, v2 prefers the databases inside saves/ and
does not also offer the old root decades.db safety copy.

CLOUD SAVE DESIGN
-----------------
One Neon database can contain many Decades Tracker saves.

Neon:
  public.decades_saves
  save_abcd1234... schema
  save_efgh5678... schema
  ...

Every save schema contains its own:
- settings/calendar
- Sims
- portraits
- households
- pregnancies
- rolls
- relationships
- historical events/results
- challenge rules
- era/species roll tables
- imported supporting data

This allows SIM-0001, HH-0001, etc. to exist independently in different saves.

PORTABLE SAVE SHARING
---------------------
Saves -> Manage -> Export portable .decades-save

A cloud save is converted into one portable .decades-save file. It can be:
- archived as a backup
- sent to another player
- imported into another Neon-backed Decades Tracker

Import:
  Saves -> Import .decades-save

The recipient uses their own Neon credentials. Your Neon password is never
included in the save package.

INTERNET
--------
Unlike the old local-database version, v2 needs an internet connection while
you are actively using the tracker because Neon is the live database.

The name library and application code remain bundled locally.

SECURITY
--------
Never publish:
- .neon_storage.json
- a Neon connection string
- a database password

The clean public release does not contain any Neon credentials.
