# Decades Tracker 4

A clean, fast rebuild of the Ultimate Decades tracker. Version 4 is a FastAPI application with server-rendered HTML and small targeted interactions. It does not use Streamlit and does not load every feature after each button press.

## Current milestone

`4.4.9` repairs stale Clock Sync anchors automatically, adds a save-wide master automation switch, and adds editable manual rolls. A manual Global Day change can no longer strand the game clock behind the tracker, while a persistent game-day high watermark still prevents an older Sims save from advancing the chronicle. Pausing automation preserves existing data and keeps the private link healthy; resuming starts from the current alignment without a catch-up burst.

`4.4.8` restores repeating historical-event obligations. Source instructions such as “each year,” “annually,” and “every ten years” now create one durable roll per reached historical interval, anchored to the event's start day. Existing first-year and completed rolls are recognized so refreshes cannot duplicate history, and custom events can set an editable repeat interval from the Events page.

`4.4.7` keeps maternal rolls pending when a delivery is accepted through Clock Sync, Today, a newborn record, or the pregnancy editor. The roll moves to the confirmed delivery day instead of disappearing, while miscarriages and cancellations still retire it. Today’s pending-roll refresh can safely restore delivery-hidden maternal rolls from older builds without reviving duplicates or completed history.

`4.4.6` keeps unfinished obligations synchronized with their editable event, aging, occult, marriage, pregnancy and campaign tables. Today now has a one-click pending-roll refresh that repairs stale dice and outcomes, adds missing obligations, and never rewrites completed history. It also restores the original SeveralUDO lifecycle mortality tables and the correctly anchored 60–120 elder-age draw; gives fairy discovery an automatic community-response and persecution chain; moves courtship creation to Relationships with stable generated marriage dates; and creates annual family plans automatically from completed pregnancy-count rolls.

`4.4.5` restores every approved historical-event roll table and its conditional follow-up chain. All 500 roll-bearing events now carry source-defined dice and adverse results, including separate regional enlistment paths, multi-stage casualty checks, and branched disaster outcomes. Existing saves receive the repair automatically while player-edited rule fields remain intact.

`4.3.0` adds optional Kemzima Responsible Pregnancy compatibility. Clock Sync 2.2.7 detects selected active pregnancy exposures, risks, maternal conditions, and newborn complications without treating them as ordinary illnesses. Current states appear on Sim and pregnancy profiles, state changes enter the chronicle once, and repeated reports do not create duplicate inbox work. Players without the mod continue to use the tracker normally.

`4.2.6` makes the Windows desktop tracker automatically start and supervise the installed Clock Sync relay. The relay follows the app lifecycle, recognizes an already-running instance by heartbeat, and restarts a failed or hung process without losing the ordered offline queue.

`4.2.5` separates family and friendship records from romantic partners throughout Sim profiles and the family tree. Relationships are directly editable, Clock Sync genealogy overrides broad Love Interest labels, and an idempotent repair corrects older parent/child/sibling records without changing marriages or engagements.

`4.2.4` restores lightweight historical-event roll reconciliation on Today. Reached global events now backfill one roll per eligible Sim after imports and same-day clock reconnects without running every scheduler, and editable event start dates are honored even when an older import left the indexed record day stale.

`4.2.3` adds complete compatibility with existing Neon saves. A legacy workspace code can now discover and safely copy every owned 3.x save into the 4.x model, including all 31 legacy table types, portraits, event rules, illnesses, resolved automation history, and prior clock alignment. The bridge is read-only against legacy schemas, preserves IDs and relationships, normalizes historical events without duplication, and can be rerun to fill only missing records. It builds on the reversible event hiding and historical event filters in 4.2.2:

- Google OpenID Connect is the normal hosted sign-in; a recovery key is created only as an emergency fallback.
- SQLite desktop mode and Neon/PostgreSQL hosted mode use the same models and application.
- Versioned change journals provide automatic desktop/cloud push and pull every ten seconds.
- Concurrent edits produce conflict records instead of silently overwriting data.
- The Sims 4 clock-report endpoint runs inside both the desktop and hosted application.
- Marriage portraits support uploads, a no-credit local ComfyUI provider, or an optional OpenAI provider.
- Every native die roll uses operating-system cryptographic randomness and writes a commitment/reveal audit record.
- The Dice Audit page reports per-face counts and a chi-square review signal.
- All major tracker areas have isolated routes so opening one feature does not calculate every other feature.

Version 4 installs beside—not over—the 3.6 application. The verified 3.6 backups remain a recovery source, and `.decades-save` exports provide portable backups.

## Desktop start

Double-click `Start Decades Tracker 4.bat`. The first run downloads a private Python runtime and dependencies. Later launches use the installed private runtime. The browser opens at `http://127.0.0.1:8000`.

The local application is also the Game Clock receiver; no separate hosted receiver is required.

## Hosted configuration

Set these Railway variables:

- `DATABASE_URL` — Neon PostgreSQL connection string.
- `SESSION_SECRET` — at least 32 random characters.
- `PUBLIC_URL` — public Railway URL.
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — Google OAuth web client.
- `PORTRAIT_PROVIDER` — `manual`, `comfyui`, or `openai`.
- `COMFYUI_URL` — local/private image service when using `comfyui`.
- `OPENAI_API_KEY` — only when choosing the optional OpenAI provider.

Google redirect URI is `${PUBLIC_URL}/auth/google/callback`.

For a gradual 3.x cutover, the app also recognizes the existing Railway
`NEON_DATABASE_URL`, `OWNER_ACCESS_KEY`, and `RAILWAY_PUBLIC_DOMAIN` variables.
Existing users can enter their email and old workspace code once; the original
legacy schemas remain read-only and the same email can later be upgraded to
Google sign-in without creating a second identity.

## Sync model

On the hosted Sync page, create a device link. Copy its one-time token, hosted save ID and hosted URL to the desktop Sync page. The desktop agent then pushes and pulls changes automatically every ten seconds.

Device tokens can be revoked independently. A version mismatch creates a conflict for review; it never chooses a winner silently.

## Portrait costs

Uploaded images and local ComfyUI generation do not use paid API credits. OpenAI generation remains available as an optional provider for people who prefer it.

## Security

- OAuth state and session cookies are signed.
- Hosted cookies are HTTPS-only.
- Google passwords are never seen or stored by the tracker.
- Recovery keys are stored only as hashes.
- Device and clock tokens are stored only as hashes.
- `.env`, databases, portraits, runtime files and exports are excluded from Git.
