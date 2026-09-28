# RBO Ticket Watcher

A reusable, read-only Royal Ballet and Opera ticket availability notifier. One
configuration can monitor multiple active productions in the same sweep.

The project performs availability checks only. It never reserves, selects,
holds, baskets, checks out, or purchases a seat.

## Current configuration

[`config.json`](config.json) contains:

- the shared Stalls Circle seat rules;
- production names and official RBO URLs;
- an `active` flag for each production; and
- each monitored performance's ISO date/time and performance ID.

Manon (Kenneth MacMillan) is initially active with the 21 supplied public
performance IDs. The 25 November Young RBO performance is not monitored.

The shared seat selection is:

- Row A: 4–8 and 106–111
- Row B: 6–15 and 98–106
- Row C: 12–16 and 97–101

Only records with the configured `ScreenId` of `2` and `SeatStatusId == 0` are
available. One seat triggers an alert. Same-row adjacent groups are placed first
as the highest-priority result; single seats and multiple non-adjacent seats also
trigger alerts.

## Safety properties

- Availability uses only the documented HTTPS `GET .../Performances/{ID}/Seats`
  request and its supplied query parameters.
- Production discovery uses one `GET` of an official
  `https://www.rbo.org.uk/production/...` page and reads its public embedded
  performance metadata.
- The clients contain no reservation, seat-selection, basket, checkout, or
  purchase endpoint.
- `401`, `403`, and `429` stop safely without bypass attempts.
- CAPTCHA, queue, authentication, waiting-room, and access-interstitial responses
  stop safely.
- Ordinary transient failures get a small bounded retry; checks remain sequential
  and are spaced by 1.5 seconds by default.
- Unrecognizable seat JSON leaves previous state untouched.
- Discovery never changes configuration unless the separate interactive
  `add-production` confirmation is explicitly completed.

## Project layout

```text
roh-ticket-watcher/
├── config.json                    # seats, productions, status, performances
├── data/                          # persistent state, created on first real run
├── src/roh_ticket_watcher/
│   ├── client.py                  # exact read-only seat endpoint
│   ├── discovery.py               # public production-page discovery
│   ├── config.py                  # validated/atomic configuration changes
│   ├── watcher.py                 # transition detection and sweep
│   └── notification.py            # Telegram formatting/delivery
├── tests/                         # mocked and offline tests
├── .env.example
└── pyproject.toml
```

## Local setup

Install Python 3.9 or newer; Python 3.12+ is recommended. From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.

## Inspect configured productions

```bash
roh-ticket-watcher list-productions
```

Expected initial result:

```text
manon-kenneth-macmillan: Manon (Kenneth MacMillan) — active — 21 performance(s)
```

## Discover a future production safely

Use the official production page—not a seat-map or search-result URL:

```bash
roh-ticket-watcher discover-production \
  https://www.rbo.org.uk/production/example-production
```

This command only displays a review. It makes one public page `GET` and does not
change `config.json`.

The review has two sections:

```text
PUBLIC/BOOKABLE PERFORMANCES
  + 2 Jan 2027 19:30 — performance ID 9001

NOT CURRENTLY PUBLIC/BOOKABLE
  - 3 Jan 2027 13:00 — no public performance ID — no public performance ID
  - 4 Jan 2027 19:30 — performance ID 9002 — restricted activity type: young-roh
```

Dates without a public ID, restricted events, and dates whose public booking data
is not yet exposed are reported separately. They are never silently added.

If RBO changes the page schema, discovery stops with an error and leaves the
configuration unchanged.

## Add a future production

The convenient two-stage interactive command is:

```bash
roh-ticket-watcher add-production \
  https://www.rbo.org.uk/production/example-production
```

It performs the same discovery and prints the full review first. It then tells
you exactly how many public/bookable performances would be added and asks:

```text
Type 'yes' to update config.json, or press Enter to cancel:
```

Only typing `yes` writes the production. The new production is active by default,
and only the reviewed public/bookable IDs are stored. The write is validated and
atomic.

For an extra review-only step, run `discover-production` first, then run
`add-production` when satisfied.

## Disable or re-enable a production

No Python change is needed:

```bash
roh-ticket-watcher set-production-status manon-kenneth-macmillan inactive
roh-ticket-watcher set-production-status manon-kenneth-macmillan active
```

You may also carefully change a production's `active` value in `config.json` to
`false` or `true`. The watcher validates the complete file before use.

## Offline tests and dry run

All automated HTTP tests are mocked:

```bash
pytest
```

Preview one Manon notification entirely offline:

```bash
roh-ticket-watcher watch \
  --sample-file tests/fixtures/seats_available.json \
  --production manon-kenneth-macmillan \
  --performance-id 74500
```

`--sample-file` implies `--dry-run`: no Telegram message, state change, or RBO
request occurs.

A live but still read-only availability preview is:

```bash
roh-ticket-watcher watch \
  --dry-run \
  --production manon-kenneth-macmillan \
  --performance-id 74500
```

## Telegram and normal operation

For local operation, copy `.env.example` to `.env` and set:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Test only Telegram, without contacting RBO:

```bash
roh-ticket-watcher test-telegram
```

Run one normal sweep across every active production:

```bash
roh-ticket-watcher watch
```

Notifications identify the production, performance date/time and ID, new seats,
all currently available requested seats, whether an adjacent option exists, the
production URL, and the direct booking link.

State is written atomically to `data/state.json`, keyed by production and
performance. Unchanged availability does not repeat. A disappearance updates
state, so a later reappearance alerts again. If Telegram delivery fails, old
state is retained so delivery can be retried on the next sweep.

## Scheduling and deployment

The program performs one sweep and exits. An external scheduler should run:

```bash
roh-ticket-watcher watch
```

approximately every 10–15 minutes. There is deliberately no internal polling
loop.

The included GitHub Actions workflow (`.github/workflows/watch.yml`) runs one
read-only sweep at minutes 7, 22, 37, and 52 of every hour. It can also be run
manually from the repository's **Actions** tab. GitHub may start scheduled jobs
slightly late during busy periods.

Before enabling it, add these two repository secrets under **Settings → Secrets
and variables → Actions**:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

The workflow restores and saves `data/state.json` using the GitHub Actions cache.
This preserves transition history between temporary cloud runners, so unchanged
availability does not repeatedly alert. The local `.env` file and its secrets
are ignored by Git and are never uploaded.

## Important command summary

```text
roh-ticket-watcher list-productions
roh-ticket-watcher discover-production <official RBO production URL>
roh-ticket-watcher add-production <official RBO production URL>
roh-ticket-watcher set-production-status <slug> active|inactive
roh-ticket-watcher watch [--dry-run] [--production SLUG] [--performance-id ID]
roh-ticket-watcher test-telegram
```
