# Project rules

- Use Open-Meteo exclusively for all weather data and city lookup. Do not add direct ECMWF, ARPA, cloud-mirror or other provider requests. No account/key is required for the selected noncommercial endpoints.

- Never hammer upstream APIs. All provider access belongs in the collector, never in the web request path or frontend.
- Keep persistent shared caches, a process lock, daily request/byte budgets, minimum intervals, and provider-wide failure cooldowns. Respect Retry-After. No immediate retries or force-refresh bypass.
- Keep ordinary development and automated tests offline. Use fixtures; explicitly opt into live sync.
- Before adding a provider, document its account requirements, terms, published limits (or absence of a verified quota), proposed request frequency, and download size. Tell the user if an account is needed or a limit prevents an update.
- Preserve old data on failure and label source, age, missing data and model estimates honestly. Never silently substitute demonstration data.
- Optimize for e-ink: readable monochrome, static by default, no required browser JavaScript, no external fonts/assets. Maintain a PNG dashboard option. The owner-requested pressure animation is optional playback of local cached maps, with no provider requests.

- Every dashboard view must fit the available browser viewport on both desktop and e-ink without scrolling. Keep headers compact, paginate growing records, and put extended details on separate link-based screens. Do not hide overflowing content to claim a layout fits.

- Download fixed historical climate archives only through the explicit `climate-history` command. Normal sync updates recent weather and reads saved history; it must never fetch historical batches.

- Owner-approved exception for the Turin archive bootstrap on 2026-10-19 UTC only: the dated local permit may allow `climate-history` up to 40 total HTTP attempts/day and 2,500 quota units/day/hour for ERA5 history through 2025. Normal sync caps, minute/month/byte limits, schedules and cooldowns remain unchanged. Never reset counters; expire the permit by date or completion.

- GitHub Pages exports must remain static, with real paths for tabs/cities/date selections and no required browser JavaScript. Publish only generated HTML/CSS/current media to `gh-pages`; keep raw caches, historical archives and quota records local. Normal Pages builds/tests must be offline; live sync/push belongs only in explicit publication. Use fast-forward pushes without touching the source worktree.
