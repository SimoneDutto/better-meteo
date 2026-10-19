# Better Meteo

A personal, monochrome weather dashboard for Torino: forecast, 15-day uncertainty,
regional pressure patterns, recent precipitation, wind and local climate comparisons. Astro renders plain HTML;
a separate opt-in Python collector writes shared local data files.

**All weather data and city lookup now come exclusively from Open-Meteo.** Direct
ECMWF and ARPA downloads have been removed and their hosts are no longer allowlisted.
The permanent service-use rules are in [AGENTS.md](AGENTS.md).

## How much does a sync use?

For the default Torino configuration, when every job needs updating:

| View | HTTP requests per collection | Conservative reserved quota units | Frequency |
| --- | ---: | ---: | --- |
| Forecast, including wind | 1 | 50 | Every 6 hours |
| ECMWF ensemble | 1 | 25 | Every 6 hours |
| GFS ensemble | 1 | 25 | Every 6 hours |
| Rainfall history | 1 | 50 | Every 24 hours |
| Regional pressure maps, shared across cities | 3 | 162 | Every 12 hours |
| Climate recent ERA5 window | 1 | 2 | Every 24 hours |
| Historical climate archive | Missing batches only | Up to 79 per batch | Explicit `make climate-history` only; never normal sync |
| **Fresh full sync** | **8** | **314** | Only when all jobs are due |
| **Normal scheduled day** | **20** | **776** | Successful updates with default settings |

**Repeating sync immediately sends zero additional requests.** Opening the page,
refreshing it, changing tabs and selecting a cached city also send zero provider
requests. Failures stop/defer collection; there are no immediate retries.

An HTTP request is not necessarily one quota unit. Open-Meteo weights locations,
variables and long time windows. Our maps sample **150 locations at 4° spacing**,
batched into three requests of 50 locations. Each asks for three fields over 15 days:
we reserve `ceil(50 × 15/14) = 54` units per request, or **162 per map collection**.
This is a coarse view of broad circulation, not a high-resolution local weather map.

The other reservations deliberately exceed the expected cost of these fixed queries.
These are **conservative estimates**, not measurements reported by Open-Meteo. Review
costs before adding variables/models or changing horizons; extra cities share the same
budget and may cause updates to defer. Geocoding costs one reserved unit per uncached
owner search, cached for 30 days. No autocomplete traffic is generated.

## Free allowance and our limits

The selected public endpoints need **no account or API key** for personal,
noncommercial use. Attribution is required under CC BY 4.0. The free service has **no
uptime guarantee**, and a numerical allowance does not guarantee every request succeeds.

Checked on 19 October 2026 against [pricing](https://open-meteo.com/en/pricing) and
[terms](https://open-meteo.com/en/terms):

| Limit | Open-Meteo published free allowance | Our enforced application ceiling |
| --- | ---: | ---: |
| Quota units per minute | 600 | 400, rolling window |
| Quota units per hour | 5,000 | 1,500, rolling window |
| Quota units per day | 10,000 | 1,000, UTC day |
| Quota units per month | 300,000 | 30,000, UTC month |
| HTTP attempts per day | Separate from weighted quota | 24 total |
| Download allowance per day | No limit inferred here | 40 MiB |

The normal default day reserves 776 units, about 7.8% of the published daily allowance. An explicit historical download shares this same budget; it can defer other updates.
The old application's 600-unit daily ceiling was raised to 1,000 to accommodate the
explicitly costed regional grid. It remains 10% of the published daily allowance.

All endpoints (forecast, ensembles, history, geocoding) share one persistent budget,
provider cooldown and process lock. Attempts count on failure too. `Retry-After` can
lengthen the minimum six-hour provider-wide cooldown. No forced refresh, retry burst,
automatic paid fallback, endpoint switching or redirect following is implemented.
Requests are sequential and at least five seconds apart. Conditional HTTP validators
are used when supplied. Bytes are reserved before downloads; failed downloads retain
their reservation. Old raw responses are pruned after 45 days; completed old images
after seven days. Current dashboard files and policy records are preserved.

JSON responses are validated before entering the shared HTTP cache. A streamed HTTP
200 response containing timeout text or truncated JSON is treated as a failed attempt:
its explanation is retained, existing valid data stays intact, and the normal provider
cooldown applies. No automatic retry is made. Invalid responses cached by an older
collector are not reusable; replacement still honours existing job schedules, budgets
and cooldowns.

HTTP errors include Open-Meteo's JSON explanation when available, read from at most
4 KiB of the original response without another request. A `400` means the request
was rejected as invalid; it is distinct from a `429` rate-limit response. Both retain
their attempt/reservation and trigger the existing failure cooldown. Multi-location
pressure queries supply one `nan` elevation per coordinate, as required by the API.

**Keep `data/` persistent. Do not delete its policy database to reset a limit.** Old
ECMWF attempts and cooldowns remain as history but the retired provider cannot be
requested. Existing Open-Meteo counters, successful forecasts and cooldowns are reused.
New map/history jobs have distinct names so retired jobs do not schedule new requests.
If another application shares your public IP, its usage may also affect upstream limits.

## Run locally

### Make shortcuts

After installing dependencies, use:

```sh
make sync               # collect due data using the existing API safeguards
make start              # production build, then start the dashboard in the background
make restart            # production build, then stop/start the managed dashboard
make stop
make logs
```

`make` lists all targets. `make plan` and `make status` are offline. `make sync`
defaults to Torino; use `make sync CITY=cuneo` for another configured city. New data
appears on the next page refresh and does not require a frontend build or restart.
Building, starting and restarting never trigger a weather sync.

The Makefile prefers `.venv/bin/python`, so activation is optional. Override
`PYTHON`, `NODE`, `HOST` or `PORT` if needed, for example `make restart PORT=4322`.
Background process management uses Linux `/proc`, a PID/start-time record and a
lock in `data/`. Logs are appended to `data/dashboard.log`. Only a server started
by these Make targets is stopped; stop any manually started server yourself before
the first `make start` if it occupies the same port. A failed build leaves the
managed process running. Docker users can keep using the Compose commands below.

Requires supported Node 22 (22.12+ recommended) and Python 3.12+. NumPy, Matplotlib
and Pillow handle maps/PNG/GIF rendering; GRIB and NetCDF packages are no longer used.

```sh
npm install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm run build
HOST=127.0.0.1 PORT=4321 node dist/server/entry.mjs
```

Both of these commands are offline, and show the request plan and persisted use:

```sh
python -m collector plan
python -m collector status
# Original machine-readable status is still available:
python -m collector status --json
```

Start a bounded sync explicitly:

```sh
python -m collector sync
```

Its completion message reports how many HTTP attempts and estimated quota units that
particular run used, followed by today's totals and eligible job times. A fresh full
sync needs roughly 35 seconds of spacing plus network/processing time. If existing
forecasts are still fresh, fewer requests are made. Rainfall no longer waits until 19:00.

Use `--only forecast`, `--only ensembles`, `--only rainfall`, `--only pressure` or `--only climate` to
collect a due source. These options do not bypass schedules, budgets or cooldowns.
The next `sync` can obtain the new Open-Meteo map/history jobs without contacting the
retired ECMWF portal. This migration does not reset either provider's saved history.

Schedule `sync` hourly from cron/systemd; the jobs still enforce 6/12/24-hour intervals.
Starting the web server alone never starts collection.

### Publish to GitHub Pages from this machine

The hosted dashboard needs **no Node/Python backend**. A local export renders all
configured cities, tabs, pressure dates/loops, climate views, paginated records and
rotation links into plain HTML. CSS and only the current referenced map/GIF/PNG files
are copied alongside it. There is no required browser JavaScript, server routing or
provider request from the published site. Absolute source timestamps replace relative
“hours ago” labels, which would become misleading on a static snapshot.

With this repository's `origin` configured on GitHub:

```sh
make pages       # offline build/export only; output is dist-pages/
make publish     # sync permitted recent weather, render PNG, build, commit and push
```

`make publish` updates **only `gh-pages`**, using an isolated temporary checkout.
It does not stage source changes, switch your branch, commit local caches, publish
historical archive files, upload the policy database, or reset any collector budget.
Ordinary fast-forward pushes preserve deployment history; concurrent remote changes
cause a failure instead of a forced overwrite. The command checks Git identity and
remote access before syncing. If GitHub is unreachable or authentication is missing,
it stops before weather requests. A sync that retains older data can still publish
it with its actual source timestamps and collection status. Building/exporting alone
always stays offline. The generated export validates every local page/asset/rotation
link before replacing the previous complete export.

On the **first successful push**, open the repository's **Settings → Pages**:

1. Source: **Deploy from a branch**.
2. Branch: **gh-pages**.
3. Folder: **/ (root)**, then Save.

The site will be available at `https://<user>.github.io/<repository>/` after GitHub's
deployment finishes. No custom workflow or GitHub-hosted weather collector is needed.
A `.nojekyll` file lets GitHub serve Astro's `_astro` CSS directory unchanged.
Subsequent mornings require only `make publish`; the normal collector still honours
all caches, schedules, budgets and cooldowns. Historical downloads stay separate.
`CITY=...` selects the city to sync; all configured cities' cached views are exported.

The repository name determines the base URL automatically. User sites named
`<user>.github.io` use `/`; project sites use `/<repository>/`. Override for a custom
domain with `PAGES_BASE=/ make publish` (DNS/Pages domain configuration is separate).
For an offline root-path preview:

```sh
PAGES_BASE=/ make pages
python3 -m http.server 8080 --directory dist-pages
# Open http://localhost:8080/
```

The existing `make start` / `make restart` commands still run the local server and
read fresh caches directly. The exported site changes only when you publish.
The PNG reader option is published at `/media/<city>-dashboard.png` beneath the same
base path. Builds include only the current maps, not superseded generated images.

GitHub credentials must already work with Git: normally SSH for `git@github.com:...`,
or your existing HTTPS credential helper. The publisher does not store a token or
create an account. GitHub Pages is free from a **public** repository; directly
publishing from a private repository needs an eligible paid plan. To keep source
private on GitHub Free, create a separate public deployment repository and run:

```sh
git remote add pages git@github.com:YOUR_USER/weather-site.git
PAGES_REMOTE=pages make publish
```

Only generated frontend files go to that repository. The base path follows the
chosen deployment repository. Published HTML, CSS and images are visible to visitors,
even when the source repository is private.

Checked against GitHub's [publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
and [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits):
1 GB published site, 100 GB/month soft bandwidth limit, 10 builds/hour soft limit for
branch publishing. The current export is approximately 8.4 MiB; one morning push is
well below the build-frequency limit. Generated images also accumulate in Git commit
history; the script never rewrites history automatically. Raw HTTP caches and the
historical archive remain local and should be backed up separately.

### Docker

```sh
docker compose up --build -d dashboard
# Explicit opt-in to scheduled weather requests:
docker compose --profile live up --build -d collector
```

Visit `http://127.0.0.1:4321`. The dashboard mounts data read-only. The optional collector
service checks due Torino jobs hourly. To serve a Kindle on your LAN, change the port
binding to your chosen LAN IP. Keep the personal dashboard on your own network.

### Other cities

```sh
python -m collector add-city --city cuneo --name Cuneo
# Inspect numbered matches; repeat the same search with a selected cached result:
python -m collector add-city --city cuneo --name Cuneo --match 0
python -m collector sync --city cuneo
```

Known coordinates/timezones can instead be added directly to `config.json` with no
lookup request. Adding a city does not multiply the scheduled jobs automatically;
configure that explicitly within the shared budget. City selection reads cached files.
The pressure map's configured Europe/Atlantic extent is shared, and is not re-centred
when a city on another continent is selected.

## What the views mean

- **Forecast/wind:** temperatures °C, precipitation water equivalent mm, snowfall cm,
  wind km/h at 10 m. Current conditions are modelled, not station observations.
  Wind labels say where air comes *from*; arrows point toward its destination.
- **15 days:** ECMWF IFS and GFS member forecasts show daily maximum median and
  10th–90th percentiles. Temperature agreement is a spread heuristic (≤3°C high,
  ≤6°C medium, otherwise low), not calibrated accuracy. Rain probability is the
  complete-member fraction predicting ≥1 mm/day. Incomplete daily members are excluded.
- **Pressure:** [ECMWF IFS 0.25° via Open-Meteo](https://open-meteo.com/en/docs/ecmwf-api),
  sampled at a deliberately coarse **4° grid**. 500 hPa height (metres converted to dam),
  sea-level pressure (hPa) and 850 hPa temperature (°C). All fifteen daily snapshots
  use 12:00 UTC within each forecast date. Retrieval/reference time is not presented
  as model initialisation time. Missing late snapshots are labelled and never extrapolated.
  Coastlines are locally authored schematic lines; no map tiles are downloaded.
- **Rainfall:** [historical API](https://open-meteo.com/en/docs/historical-weather-api)
  with `ecmwf_ifs`, the previous 365 completed local dates, refreshed daily. These are
  **model estimates, not ARPA/rain-gauge measurements**. The page shows 15-day daily
  bars/total and 30/90/180/365-day totals. Missing days make totals unavailable. The
  rainfall tab does not calculate a baseline or formal SPI/SPEI drought index.
  Such totals alone do not diagnose drought; the Climate change tab has a separate ERA5 baseline.

### Climate change tab

The tab compares a **recent completed 15-day window** with matching calendar windows
in **1991–2020**, using ERA5 consistently for both. It shows average daily high/low,
total precipitation (mm water equivalent), rain (mm), snowfall (cm), differences,
reference-year counts and historical 10th–90th percentiles. These percentiles describe
variability, not a confidence interval. A short weather anomaly does not establish a
climate trend or attribute an event to human influence.

Longer-term context includes annual charts and the difference between annual values
averaged over **1961–1990 and 1991–2020**. Temperature values are annual averages of
daily highs/lows; precipitation values are annual totals. Individual years/windows
require complete daily data per measure. Period means require at least **24/30**
complete years/windows and show the counts. Missing values remain unavailable;
incomplete calendar years are excluded. These are descriptive comparisons without a
significance test or causal attribution. Natural variability, urban effects and
reanalysis limitations matter, especially near the Alps. This is a city-location
estimate at approximately 25 km resolution, not a regional spatial average or local
station record. Neither reference period represents preindustrial conditions.

Climate uses **UTC calendar days** consistently across all downloads, avoiding DST
and fixed-offset changes across multi-year requests. The recent request ends
**six days before collection**, allowing for ERA5's documented
five-day publication delay. A 28-day buffer allows the collector to select the latest
complete 15-day window when publication is later than expected. If there is no
complete window, available values are explicitly labelled partial with a day count;
no full-period anomaly is calculated. Partial differences instead compare the available
dates with exactly the same date offsets in every reference year, per measure. Both
temperature means and precipitation totals therefore use matching coverage; the day
count remains visible and annual statistics still require complete years. It deliberately differs from the Rainfall tab's more
recent ECMWF IFS history. Matching historical windows end on the same month/day and
run backwards 15 days; 29 February maps to 28 February in non-leap years. The start
can therefore shift one day around leap years. Recent reanalysis may be preliminary.

**Collection policy, reviewed 19 October 2026:** this uses the existing Open-Meteo
Historical Weather endpoint, with `models=era5`, one location and five daily fields.
No new provider, account or key is needed for personal noncommercial use; the same
[terms](https://open-meteo.com/en/terms), [pricing limits](https://open-meteo.com/en/pricing)
and CC BY 4.0 attribution apply. [Open-Meteo recommends ERA5/ERA5-Land for multi-decade
comparisons](https://open-meteo.com/en/docs/historical-weather-api). The standard
1991–2020 reference is described by the [WMO](https://wmo.int/activities/climate-services/climate-products-and-initiatives/wmo-climatological-normals).

Normal `make sync` requests only a recent 28-day buffer (2 reserved units per day)
and reads historical archives from disk. **It never downloads historical batches.**

Download the fixed historical archive explicitly:

```sh
make climate-history
# Equivalent, using the existing city configuration:
python -m collector climate-history --city turin
```

This fills missing three-year blocks, prioritising 1991–2020, then 1961–1990 and newer
completed years. In 2026 the full archive is **22 requests / 1,712 reserved units**
from empty; the modern baseline alone is **10 requests / 790 units**. Completed blocks
are skipped permanently. Running the command after completion makes zero weather
requests. Each query reserves `ceil(days / 14)` units, up to 79, for one location,
one model and five variables. JSON is expected to be under 1 MiB per block (an
estimate); the enforced per-request ceiling/reservation remains 8 MiB. All existing
request/byte/quota budgets, locks, schedules and provider cooldowns remain active.
The command spaces requests and waits when necessary before a rolling-minute limit;
it stops at the first failure, cooldown, scheduled deferral or exhausted budget.
There are no error retries or forced bypasses. A failed/deferred block retains a
24-hour job interval. Another explicit invocation can resume after it is eligible,
without downloading completed blocks again. With a 1,000-unit daily cap, the complete
archive necessarily spans multiple UTC days and competes with ordinary weather jobs.

For the owner-approved bootstrap on **19 October 2026 only**, the local
`data/climate-download-permit.json` permits the `climate-history` command for Turin
to use **40 total HTTP attempts/day, 2,500 quota units/day and 2,500/hour**. It applies
only to ERA5 archive requests ending by 31 December 2025; it cannot affect normal
sync or other providers. It expires at the end of that UTC date and is marked
completed when the archive is full. No command-line force/override flag exists.
The 400-unit/minute, 40 MiB/day and 30,000-unit/month limits remain unchanged,
as do cooldowns, Retry-After and per-batch failed-attempt schedules. Counters are
never reset. Ordinary sync retains its 1,000-unit/24-attempt daily caps, so it may
pause for the remainder of the UTC day once the bootstrap uses more than those
amounts. This is a dated bootstrap allowance, not a permanent policy increase.

The archive is stored in `data/cities/<city>/climate-archive-v1/`, outside the 45-day
raw-response pruning. Keep it persistent and backed up. Multi-year blocks containing
a newly completed year are extended only on another explicit archive invocation;
normal sync will never extend or refresh them. Changing the location/timezone under
an existing city id is rejected to prevent mixing histories. Historical downloads
rebuild the local comparison immediately while preserving the timestamp of recent
weather. Partial progress survives failures; missing historical values are rejected
before a block is saved as complete. Recent weather continues updating independently.
No ceilings or cooldowns have been raised or bypassed.

Open-Meteo's local ISO time output uses a fixed offset over a request, including across
DST changes. Around transitions, city hourly labels/daily boundaries may differ by an
hour from civil time. Pressure maps use UTC timestamps and are unaffected.

## E-ink

The dashboard uses the available browser viewport on desktop and e-readers. Each tab
is laid out as a single screen: a compact header, navigation, content and footer.
There are no expandable sections that grow the page. Sources/status has its own
screen; climate comparison, 30-year change, charts, method and annual records use
separate plain-link views. Annual records paginate ten years at a time, so growing
archives cannot lengthen the screen. Short viewports prioritise the complete 15-day
table over its duplicate temperature chart; narrow wind views sample less frequently.
Weather data is never fetched by changing views.

Plain links and a form; no browser JavaScript, automatic animation, external fonts, hover-only
controls, live tiles or CDN calls. A 30-minute refresh and optional two-minute tab
rotation read local files only. Actual Kindle browser/layout compatibility needs testing
on your specific model.

```sh
python -m collector render
```

This produces a forecast-only 600×800 `/media/turin-dashboard.png` for devices that
display images directly. The HTML interface contains all six views. A Kindle script
can periodically download that local image without involving weather providers.

The Pressure tab includes **Play forecast loop / Pause** controls. Playback loads one
locally generated animated GIF and requires no JavaScript or provider calls. It loops
the actual available forecast snapshots, with a prominent full-date / day-number / UTC-time banner in every frame and a
pause at the end. Frames advance daily at 12:00 UTC across all 15 forecast dates; no intermediate weather is
invented. The static PNG/date selector remains the default for e-ink. Every successful
map collection rebuilds the GIF from the same PNGs, without increasing API usage.

## Verification

```sh
python3 -m unittest discover -s tests -v
node tests/test_pages.mjs
npm run build
# Optional visual layout check with locally installed Google Chrome (no downloads):
make test-layout
# Override browser path if needed:
CHROME=/path/to/chrome make test-layout
```

The layout check blocks HTTP/HTTPS requests and renders cached data into temporary
local HTML. It checks 600×800 portrait, 800×600 landscape, 1366×768 and 1920×1080
viewports, plus synthetic fully populated climate archives. It fails on page overflow
or content extending beneath the footer, rather than hiding overflow. Restricted
sandboxes may prevent Chrome from starting; run it on the host in that case.

Tests use fake transports, multi-location JSON, synthetic ensembles/rainfall and map
fields. They cover shared persistent limits, cooldowns, no retry, cache reuse, rejected
retired providers, bounded grid cost, missing snapshots, unit/coordinate validation,
PNG rendering and migration history. Tests do not call weather providers.

The Astro production build passes. The new map/history integration is tested offline;
no live Open-Meteo map/history collection was run during migration. Previously downloaded
forecasts remain usable. Confirm actual provider payloads with one normal opt-in sync;
do not loop or clear the database if it defers or fails.
