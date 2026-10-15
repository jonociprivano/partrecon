# Bimmerpost browser trial

The trial uses ordinary headless Chromium, visits one F80 wheels classifieds page
(sorted by original listing date) and one public thread, then exits. No database,
AI parser, login, saved cookies, proxies, CAPTCHA handling or automatic retries.
A 401/403/429 or unrecognized/challenge page fails rather than being bypassed.

Use a separate Railway service, with no environment variables, domains, volumes,
cron schedule or database connection. Pin the reviewed commit and select
`deploy/bimmerpost/railway.json`. The default command is
`python -m scraper.bimmerpost_probe`. Restart policy must remain NEVER.

The service is staged for review only. Approval to deploy the trial does not
approve changing the production scraper. After an approved trial, inspect
BIMMERPOST_PROBE logs for listings, price/date/status coverage and photo_found.
If blocked, stop; request an authorized feed/access arrangement from Bimmerpost.

Only after a successful trial and separate production approval: use the same
Dockerfile on cron-bimmerpost and start with
`BIMMERPOST_TRANSPORT=browser python -m scraper.runner bimmerpost`.
Keep the existing cron schedule and database configuration. Other forum hosts
still require validation: the trial proves only the F80 wheels route.
The production inventory still includes older hosts/routes; do not activate
broad ingestion solely on the strength of this one-page trial.
