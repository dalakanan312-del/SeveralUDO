# Decades Tracker 3.2.2

Clock Sync now uses a secure local Windows relay because The Sims 4 embedded
Python runtime does not include the SSL module required for direct HTTPS.

- The game mod queues reports locally without exposing the token in logs.
- The relay forwards queued reports to Railway over HTTPS.
- Railway continues to enforce HTTPS; no insecure public endpoint was added.
- Manual reports distinguish a queued report from a receiver-accepted report.
- The tracker provides downloads for the relay and its launcher.
- Existing private clock links and tokens remain valid.
