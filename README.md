# Decades Tracker 4

## 4.6.25: pregnancy rolls on simplified Today

**＋ Roll for pregnancies** is directly available on simplified Today. Choose a living Sim from the current household selection, prepare their annual pregnancy-count roll, then use the native die and consequence confirmation. Existing pending or completed allowances are opened without duplication or rerolling, in the correct date window and ahead of long task lists. Missing era rules show a helpful message and link to Roll Tables. The detailed Today controls remain available.

## 4.6.24: historical portraits and clearer Tray photos

- **History → Portrait Studio:** create a historical portrait from a Sim's Tray or imported photo; choose a life stage, suggested historical year, region and style. Suggested dates use recorded aging results or the save's scaled age schedule and can be overridden without changing the Sim's birth record.
- **Settings → AI Portrait Settings:** explicit enable switch, private encrypted provider key, model selection and a connection check that does not generate a paid image. OpenAI generation uses the player's own API credits; desktop Local AI requires a compatible ComfyUI bridge.
- Profiles link to generation from each source photo and show a historical portrait gallery. Originals are preserved. Generated images are labelled AI reconstructions, with full-size viewing, downloads and recoverable archiving.
- Tray imports now decode the embedded transparency mask and retain native resolution, eliminating the blurred edge background. Rescan existing Tray photos to replace previously imported blurred copies; manually chosen portraits remain protected. New decade snapshots use clean, flat backgrounds.
- Paid generation is deliberate, runs in the background and does not automatically retry interrupted requests. No game-mod change is required.

See [Portrait Studio guide](docs/portrait-studio.md) for setup and limitations.

## 4.6.23: household history and play support

- **People → Historical Address Book:** properties and dated resident, tenant, guest and owner periods; related events distinguish explicit venue links from residence-based associations. Does not move Sims or rewrite their current household.
- **People → Naming Customs:** source pools, namesakes, family surnames, editable patronymics, formal styles, era ranges and an explicit era override. Suggestions do not rename Sims.
- **People → Titles & Estates:** holders, preserved tenure history, associated properties and unlimited ranked/disputed succession claims. Title transfers remain player decisions.
- **Play → Seasonal Routines:** opt-in household instructions at four challenge-year quarters, scaling with year length. Due tasks appear on Today, respect the master automation switch, and generate once per year without pre-enablement backlogs.
- **Play → Story Board & Promises:** unresolved stories, priorities, next steps, deadlines, named parties/witnesses and exact outcomes recorded in Storyline. Broken promises can open a contextual promise-themed Drama Deck scene.
- **Settings → Historical Catch-up:** household-filtered, 50-item batches of pending past rolls/tasks; preview, historical completion or waiver, and protected undo. Does not fabricate dice results or execute death/follow-up logic. Administrative resolutions are separate from passing results in statistics.
- **History → Share a Family Chronicle:** preview/download self-contained HTML with selected people, linked family connections, optional embedded portraits, yearly selected facts and statistics. Private facts and narrative text require explicit opt-ins; no automatic publishing or remote assets. Future entries and unselected relatives are excluded, and year-only births remain year-only.

Those eight additions use already-supported synced note/task/story/scene types and require no game-mod change or new database table.

### 4.6.23: crash / older-game-save recovery

- **Play → Crash Recovery** holds game imports and the master automation scheduler when a newer report moves backward by a day or within the same day. Ordered duplicate and out-of-order reports are ignored before recovery detection; old unsequenced reporters show an uncertainty warning.
- Keep all history and wait for the game to catch up, or keep history and map the restored game day to the current tracker Global Day. Both choices retain completed rolls. After skipped delta reports, a fresh full report is required before automation resumes.
- Preview an all-record rollback to the nearest earlier observed checkpoint. It includes player edits and completed outcomes, restores available earlier record states, and soft-archives records created after the checkpoint. A full downloadable backup is required before any undo. Photos, dice audits, preferences and credentials are preserved. Missing earlier history, changed calendar/settings, frozen branch records, oversized plans, stale previews and failed backups prevent rollback instead of guessing or applying a partial recovery.
- Recovery checkpoints start with reports received after this update. They are report observations, not actual game-save confirmations: there can be a gap between the checkpoint and the time the player saved. The page states that limitation before confirmation. Up to 2,048 ten-game-minute intervals are retained per save in a lightweight local `clock_checkpoints` table created at startup. It stores journal boundaries and a configuration digest, not duplicate Sim payloads. Checkpoints/recovery decisions are database-local and are not transported to another device through save sync or backup imports.
- Review is save- and branch-scoped. Background held reports do not invalidate a rollback preview, but changed affected records, calendar settings, a further rewind, or an already-used/expired preview do. Replayed sessions start a new checkpoint epoch.

No Clock Sync mod replacement is needed. Recovery points start after updating the tracker; they cannot reconstruct reports from before the update.

`4.6.22` restores the named weekday on simplified Today and the shared game-connection panel. Live reports update the weekday and time without a page reload, including at midnight and across week boundaries. Today uses the last reported in-game weekday, not the challenge's Global Day or year length; without a report, the tracker-derived weekday is explicitly labelled. Saved-game and disconnected clocks retain their last reported weekday. No game-mod or save-data changes are needed.

`4.6.21` fixes roll confirmation being blocked by unrelated background reports. Confirmation rechecks the real consequences using the same stored die result and reviewed random choices, while preserving newer clock metadata, unrelated Sim details, and the current save revision. Changes to the roll, source rules, eligibility, or consequences still require review. A **Review updated preview** button retains the original die result and replaces repeated errors with a single message. Rejected confirmations preserve the previous Undo action. No game-mod update is required.

`4.6.20` adds delivery-centred birth panels (including per-baby maternal checks for multiples), household-sized Today batches, situation-specific roll names, scaled birth-to-aging calculations, and compact profile life-stage schedules. General historical context is collected into a daily summary rather than repeated on unrelated rolls. Calendar and rulepack changes run the real scheduling functions inside a rollback-only preview, show moved/added/retired obligations, and preserve completed results. Roll outcomes similarly preview the exact generated death dates and conditional follow-ups; confirmation applies that reviewed plan only if the save and affected records are still current. Native throws are retained across previews, while deliberate reopening permits a new throw. All previews are private, user-owned, expire after 15 minutes, and are never synced as game records. Maternal duplicate repair includes pregnancy and baby identity. Clickable date-source labels distinguish game-confirmed, player-entered, estimated, randomized and unverified evidence. A branch/checkpoint banner stays visible; clock receipt metadata separately identifies duplicate relay deliveries, unchanged game time, detected changes and the last successful new report without creating history on every poll. No game-mod update is needed. Desktop install and public publication are separate from preparing this source.

`4.6.19` is the play-first usability update. Today defaults to **Needs your decision**, **Happening today**, and **Completed**, with separate overdue/future windows and 24-record batches. **All Today tools** retains the detailed calendar, age check, manual-roll, pregnancy and occult controls; old deep links still reach them. The sidebar uses **Play**, **People**, **History**, and **Settings**, hiding disabled fantasy add-on destinations without hiding rule setup. A shared clock strip shows the current connected save, tracker day, game time and last successful report. Missing reports mean waiting, not a guessed pause; a player pause marker is explicitly labelled, and connection failures retain the last successful timestamp. Rolls share due/die/bad-results/modifiers/outcome/source/follow-up cards. Roll completions and inbox decisions use save/version-guarded targeted updates with ordinary form fallbacks. Automation shows current-to-proposed comparisons, conservative evidence labels and remembered dismissals. Profiles keep essentials at the top and use keyboard-accessible overview, family, health, education, occult, game, history, portrait and editing tabs. Date badges distinguish observed, estimated, randomized, manually entered and unverified sources on profiles, Sim lists, relationships and timeline. Active lists default to living Sims while history keeps deceased records and legitimate post-death occult obligations. Sim lists fetch full records in batches, using lightweight identity fields for menus. Per-person filters (including multiple selections), sorting, collapsed sections, profile tab, thumbnail size and density persist in a dedicated preference table; private editing drafts are never stored. No game-mod update is required.

`4.6.18` adds five connected play-support pages covering ten requested tools. **Play Session Planner** ranks households by births, birthdays, weddings, due rolls, pending detections and unfinished stories; dated handover notes show meaningful changes on return. **Family Projects** tracks multi-generation ambitions, named secret witnesses and knowledge history, optional event recovery, and configurable achievements. Goals can measure known funds, descendants, occult descendants, generations, marriages, heirloom years and event survivors, or use player-confirmed progress. Recovery plans never silently deduct funds or change Sims. **Compare Branches** reads Infinite Decades checkpoints side by side without restoring them. **Historical Checks** applies optional, source-backed, rulepack-scoped restrictions with per-warning overrides. **Letters & Journals** generates clearly fictional spouse, parent, heir, rival or chronicler perspectives around selected recorded events, without an API key. Secrets can start a discovery-specific Drama Deck scene. New **Why this roll?** and detection/profile evidence links retain allowlisted source rules, calendar calculations and original report identities when available; older records are never given invented creation history. All new records use existing sync-compatible types. The tutorial and related-task navigation explain the workflow; original records, the drama randomizer and game clock behavior are preserved.

`4.6.17` adds **Drama Randomizer** under Story & Progress, separate from the existing Drama Deck decision-tree game. Its d64 contains all 64 player-supplied in-game prompts in their original order, with equal odds, optional Sim/household focus, reroll/skip, and a collapsible full list. After playing a prompt in The Sims, confirm its actual global day and write the exact outcome to Storyline and Timeline. Draws do not change people, relationships, money or scheduled obligations. Repeated confirmations keep one outcome; stale draws, changed saves, and paused or changed dynasty branches are protected. Confirmed outcomes reuse the existing synced drama-scene record type. The Infinite Decades sidebar button also wraps as one full-width control and follows the mobile menu.

`4.6.16` organizes the tracker around a play session. Session Home offers prepare, play, review and wrap-up actions; seven sidebar groups separate everyday play, Sims and families, planning, story and progress, rule setup, references, and maintenance. Relationships now contains the automatic marriage-roll dashboard alongside courtships and generated wedding dates. Succession & Campaigns focuses on heirs and wartime planning. Related-task links connect existing tools, and section shortcuts reveal collapsed forms on long pages. The tutorial explains the new layout and distinguishes Game Connection from Desktop / Online Sync. Existing URLs, saved results, rule-pack controls and records are preserved. Historical Life's education and marriage views also regain their missing age-helper import.

`4.6.15` fills missing Sim birth times with a stable randomized time inside the recorded birth Global Day, deriving a matching historical date for four-day and custom-length years. Profiles and timelines label generated dates as randomized. Known times/dates, year-only migrations, uncertain multi-day estimates, paused automation and frozen dynasty relatives remain untouched. New births use the same fallback; manual time edits replace the estimate.

`4.6.14` introduces the family explorer: compact portraits and details beside the diagram, parent-specific child groups, remarriages and co-parent connections, explicit adoption, branch folding, search, previous focus, zoom/fit, a mobile family list, kinship tracing, and connected SVG/print/PDF output. Large views are capped at 180 people without changing saved records. Infinite Decades uses the same canonical Sim IDs across active and frozen branches; preserved relatives remain read-only. This release includes the completed Infinite Decades branch/checkpoint controls and keeps the withdrawn Sims 3 clock mod disabled.

`4.6.13` withdraws the unreliable Sims 3 Clock Sync experiment. Its downloads, setup/token actions, relay auto-start, and incoming reports are disabled, and its kit is excluded from desktop/hosted distributions. The Clock page explains the creator's decision and the desktop save-data supplement, including its capabilities and limits. The local saved-file reader from 4.6.9–4.6.12 remains available; existing tracker records and Sims 4 Clock Sync are unchanged.

`4.6.12` reads the active Sims 3 world's saved clock directly from its serialized SimClockUtils singleton. The local Clock page can automatically advance Global Day when completed saves cross game midnights. Initial alignment preserves the current challenge day; repeated snapshots cannot double-count days, older clocks are rejected, and town changes or paused/manual day adjustments re-align without backfilling. Today labels this as a saved-game clock, not live telemetry. No in-game mod is required, and observation time is never mistaken for exact birth or death time.

`4.6.11` reads Sims 3 traits from completed saves, using a bundled dictionary of 337 built-in trait identifiers. Traits update linked profiles and new-Sim review payloads; full 64-bit custom IDs stay explicit. Missing or unreadable managers cannot clear traits, while verified empty lists can. Decoder revisions enrich previously read files once without duplicate imports or changing Global Day. The housed-households scope now covers every readable saved world while excluding homeless/service NPCs (the active household remains included).

`4.6.10` adds **Entire save** to the local Sims 3 save reader: all readable full human Sim records across saved .nhd worlds, including homeless and service Sims without requiring household rotation. It deduplicates game Sim IDs, scopes household IDs by world, preserves existing household records, and rejects older per-Sim snapshots. Other worlds represent their last saved state, not live updates. Pets and partial travel-only records remain unsupported; clock confirmation is still manual.

A clean, fast rebuild of the Ultimate Decades tracker. Version 4 is a FastAPI application with server-rendered HTML and small targeted interactions. It does not use Streamlit and does not load every feature after each button press.

## Current milestone
`4.6.9` adds an external, read-only Sims 3 save reader to the local desktop Clock page. Choose a regular .sims3 folder, preview its data, and enable automatic reading after each completed game save. It imports verified human Sim identities, stages, household data, available family links, learned skills and positive pregnancy signals; new Sims go to Automation Inbox. It uses temporary copies, waits for quiet files, avoids duplicate imports, respects the master automation switch and backs up tracker data before reconciliation. Exact game time, pregnancy completion, illnesses, traits and portraits are not decoded; the manual clock confirmation remains necessary. No in-game Sims 3 package is required or installed by this feature.

`4.6.8` corrected calendar-aware age displays and eligibility throughout 12-day saves. Its experimental in-game Sims 3 reporter has since been replaced by the external save-safe workflow because of loading issues.

`4.6.5` adds a hosted-only, privacy-respecting support placement. After the creator configures an approved Google AdSense unit, signed-in visitors receive a one-time choice: show one small footer ad or continue without ads. Google’s script is never loaded unless they opt in; desktop/local tracking remains permanently ad-free, and the choice can be changed from the sidebar.

`4.6.4` adds a game edition choice for every new chronicle. Sims 3 saves get their own Clock Sync screen, a separate private kit, save-pairing protection, and a safe manual day-and-time reporter; Sims 4 saves keep the existing automatic `.ts4script` kit unchanged.

`4.6.3` makes the challenge calendar genuinely length-aware. Changing from four to twelve days per year triples future lifecycle ages and pending age-based rolls, marriage and elder limits, age-gated rules, and Hogwarts/Harry Potter milestones. Completed history is never moved.

`4.6.2` makes every supporting Drama Deck scene specific from its first decision: opening choices now name the actual card situation—such as repairing a missed birthday or investigating a missing key—instead of falling back to generic response buttons.

`4.6.0` turns the Drama Deck into a complete five-act scene minigame. Set an objective, choose an opening move, draw a themed complication, commit a tactic, track Connection/Leverage/Security/Tension, roll a d6 resolution, and choose the exact ending. The retained Storyline record includes the cast, complication, tactic, roll, and final outcome; the game board itself never changes Sims, relationships, rules, or rolls automatically.

`4.5.22` turns the Drama Deck into a genuinely informed decision tree. Each draw has a named counterpart Sim (auto-selected or chosen by the player), a concrete cast and time pressure, and a forecast for each first path: immediate effect, benefit, and risk. The selected counterpart is retained with the exact card outcome in Storyline.

`4.5.21` gives every Drama Deck draw a practical scene briefing: who is affected, what is at stake, and the missing context you should decide from your own save. Response cards now state their immediate trade-off, and consequence cards show their exact recorded text before you choose.

`4.5.20` carries every saved Drama Deck scene into Storyline as the actual situation, selected response, and chosen consequence. Earlier saved scenes retain their complete saved card body instead of being reduced to a generic player-choice note.

`4.5.19` expands every Drama Deck to at least twenty fully playable, non-mechanical decision cards. This includes every historical era, the selected core challenge ruleset, and enabled Harry Potter, Avatar, and Game of Thrones add-ons, while a test prevents future releases from shrinking any deck below that minimum.

`4.5.18` adds a date-scaled, interactive Visual Timeline to the Chronicle. It groups busy days into readable moments while preserving the complete ledger underneath, marks current and future days, and links every marker to the underlying record. Clock Sync 2.2.10 avoids calling the legacy poll restarter before the Sims 4 time service exists, preventing the startup exception seen during loading.

`4.5.17` adds the Drama Deck: an optional, card-based decision tree for player-chosen household scenes. Every draw is themed by the current historical era, selected core ruleset, and enabled Harry Potter, Avatar, or Game of Thrones add-on. Two decisions reveal a conclusion, and only an explicitly saved conclusion becomes a non-mechanical, editable chronicle record. No card silently changes a Sim, relationship, rule, roll, or game state.

`4.5.16` makes hosted sync forward-compatible with the desktop tracker’s Clock Sync journal. Game-history, Clock Sync diagnostic and protocol records, and illness-detection dismissals now reach online saves instead of blocking the entire sync queue. Maternal delivery checks are per baby delivered, and occult Sims use the original SeveralUDO aging chart except where a supplied source explicitly replaces ordinary aging (Servo). Clock Sync 2.2.9 also records each Sim’s directional relationship sentiments and native satisfaction where the game exposes them, with a clearly labeled friendship/romance estimate only when it does not.

`4.5.15` corrects household-wide witch trials: every eligible Spellcaster receives an individual accusation roll, and older completed trial rolls are repaired once without duplicating existing accusations.

`4.5.14` adds Save-a-Sims: a configurable credit ledger that earns one credit after every ten deaths, once when every recorded Sim has a scheduled death date, and from optional player-defined rule conditions. Credits can be spent to withdraw a scheduled death and restore its future obligations. Family plans created from a pregnancy-count roll now track pregnancies independently from children, so twins and triplets use one allowance.

`4.5.13` broadens save-level visual themes across the remaining legacy pages, adds seven more polished light and dark palettes, sorts the Sims ledger by birth date with living/deceased filters, and gives a sorted Sim in an active Harry Potter save a House-specific profile palette and Hogwarts badge.

`4.5.12` adds an everyday-use pass: the dashboard now acts as a true starting point with a recent-change digest, household focus, upcoming calendar, data-health checks, and a one-click resume link. Today can focus on a household without losing global events; profiles have a compact chronological life history; Search finds records anywhere in the tracker; dead Sims are excluded from Age Check; and newly accepted Sims automatically receive recorded passes for the life-stage checks they have already outgrown.

`4.5.11` automatically schedules every Harry Potter rule with a tracker-visible trigger when its module is enabled: magical/Squib birth determination, Squib discovery, higher-order magical multiples, accidental magic, American Muggle-Born Obscurial risk, Statute of Secrecy, annual wizarding-household and Hogwarts events, and Wizarding-War household events. Results update the relevant Sim, pregnancy, or household record automatically.

`4.5.10` adds automatic Hogwarts Sorting: when the optional Hogwarts House Assignment rule is enabled, eligible Spellcaster Sims receive one D4 roll at age 11 from 990 onward. Completing the roll records their Hogwarts House on their profile.

`4.5.9` adds a true light reading option. Daylight Chronicle provides a polished parchment-and-ink palette, and Custom palette can now be saved in either light or dark mode without dimming the selected canvas. The Appearance preview, cards, forms, navigation, Today, family tree, and other older feature surfaces follow the chosen mode.

`4.5.8` refreshes the approved pre-1300 source documents, preserving existing 1200s event IDs while adding the missing historical entries and refreshed roll tables. It also expands the hosted Neon connection pool so simultaneous page loads and Clock Sync reports do not exhaust the web service and cause internal-server errors. Clock Sync remains 2.2.8.

`4.5.7` ensures a Clock Sync relationship upgrading from a generic game summary to Marriage always creates an Automation Inbox review. An engagement no longer suppresses a later marriage confirmation, while an already-recorded marriage remains protected from duplicate reviews. It also retains the maternal delivery safeguards introduced in 4.5.6. Clock Sync remains 2.2.8.

`4.5.4` adds automatic occult-alignment rolls. Vampires, Spellcasters and Mermaids use Good/Bad; Fairies use Benevolent/Unseelie. Founders establish alignment with a D2, children normally inherit an aligned occult parent's result with the supplied D10 rule, and opposing parents use a D2. Completed results update the Sim profile and unlock alignment-dependent obligations without creating duplicates. Clock Sync remains 2.2.8.

`4.5.1` adds compact collapsible family plans and the connected Life Records workspace: planned-marriage dowry estimates, guardianship, birth-order privileges, coming-of-age and household-dispersal planning, social mobility, absences, disability, mourning, wellbeing, treatment and recovery restrictions, Law & Disorder-compatible legal reviews, grief automation, roll explanations, safe automation undo, contradiction checks, saved views, bulk profile correction, and annual newspapers. A new save-level Appearance editor adds six compatible palettes, safe custom colors, spacing, text size, heading style, corner, motion, live-preview, and reset controls across desktop, hosted, and mobile layouts. Clock Sync remains 2.2.8.

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

## Optional hosted ads

Advertising is disabled by default and never runs in the local desktop tracker. To enable the single opt-in footer placement on the hosted deployment only after AdSense has approved the site, set `DECADES_ADVERTISING_ENABLED=true`, `GOOGLE_ADSENSE_CLIENT_ID` (for example, `ca-pub-…`), and `GOOGLE_ADSENSE_FOOTER_SLOT` (the display unit slot). Without all three, no prompt, ad space, or Google script is included.
