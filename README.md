[![Weekly Scrape](https://github.com/SahirVhora/sf-release-update/actions/workflows/scrape.yml/badge.svg)](https://github.com/SahirVhora/sf-release-update/actions/workflows/scrape.yml)
[![GitHub Pages](https://github.com/SahirVhora/sf-release-update/actions/workflows/pages/pages-build-deployment/badge.svg)](https://sahirvhora.github.io/sf-release-update)
[![Last Updated](https://img.shields.io/badge/dynamic/json?url=https://raw.githubusercontent.com/SahirVhora/sf-release-update/main/data/updates.json&label=last%20updated&query=%24.metadata.lastScraped&color=teal)](https://sahirvhora.github.io/sf-release-update)

# SAP SF Release Updates

Live tracker for SAP SuccessFactors release updates. Pulls the latest changes from the SAP What's New Viewer and presents them in a clean, filterable dashboard - categorized by module with plain-English impact summaries.

**Live at: https://sahirvhora.github.io/sf-release-update**

## Features

- **Multi-version support** - toggle between 1H 2026, 2H 2026 (preview), or all releases. The scraper auto-discovers available versions from SAP.
- **Release timeline** - preview and production dates for each release so teams know when changes hit their stack.
- **Weekly auto-refresh** - GitHub Actions scrapes the SAP What's New Viewer every Monday, discovers new versions automatically.
- **SF Compass-style accordion sidebar** - modules expand to show New/Changed/Deprecated/Deleted counts. Click a module header to filter the right panel; click a sub-item to narrow further.
- **Dark/Light theme toggle** - navy + gold dark theme (matches SF Compass), warm parchment light theme. Persisted to localStorage.
- **Impact classification** - Critical / High / Medium / Low derived from SAP's Type, Major or Minor, Lifecycle, Action, and Enablement columns.
- **Contextual plain-English summaries** - each update includes a unique "What this means for you" summary using the actual description text and timeline dates.
- **Full search & filter** - by module, impact level, action type, version, and free text search.
- **Release Readiness Checklist** - select your managed modules, generate a personalised checklist with Action Required / Review & Test / Informational categories, track progress with checkboxes (persisted in localStorage), export to JSON, and print.
- **Direct SAP links** - every update links to the official SAP Help Portal documentation.
- **Single-file HTML** - no dependencies, works offline with bundled data, deployed via GitHub Pages.
- **41 SF modules** tracked across Employee Central, Compensation, Recruiting, Platform, and more.

## Architecture

```
weekly cron -> scraper.py -> data/updates.json -> index.html -> GitHub Pages
```

- `scraper.py` - Playwright-based scraper. Discovers available versions from SAP's filter, iterates through each, extracts all pages, classifies impact, generates plain-English summaries, and outputs structured JSON.
- `data/updates.json` - structured current-release JSON across 1H 2026 and 2H 2026 preview. Each item preserves SAP's change type, size, lifecycle, action, and enablement alongside the impact classification, plain-English summary, release version tag, and absolute SAP links.
- `index.html` - single-file viewer with dark/light theme, version switcher, accordion sidebar, search, and filters. Reads `data/updates.json`.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run scraper (auto-discovers available versions)
python3 scraper.py

# Serve locally
python3 -m http.server 8765
# Open http://localhost:8765
```

## Automation

The scraper runs automatically every **Monday at 06:00 UTC** via a GitHub Actions workflow (`.github/workflows/scrape.yml`). The workflow:

1. Checks out the repo
2. Sets up Python 3.11 with Playwright
3. Runs `run_scraper.sh`, which executes `scraper.py` and commits/pushes any new data

You can also trigger a scrape manually from the **Actions** tab -> "Weekly SAP SF Release Scrape" -> "Run workflow".

The scraper auto-discovers available versions from SAP, so when new release data is published, it will be picked up automatically.

## Deployment

GitHub Pages deploys from the `main` branch using `.github/workflows/deploy.yml`. The `data/updates.json` is committed alongside `index.html`.

```bash
git add data/updates.json index.html scraper.py
git commit -m "Weekly SF update refresh"
git push
```

## SAP Data Quirks

- **Release-note semantics**: SAP exposes change Type, Major or Minor, Lifecycle, Action, and Enablement separately. The scraper preserves each field and treats Deprecated/Deleted as lifecycle states.
- **Multi-line modules**: Some SAP items span multiple modules. The scraper takes the first (primary) module.
- **Preview items**: Future-release items appear in the current release view with "Preview" prefix. These are tagged as the upcoming release version.
- **Relative links**: SAP provides relative URLs; the scraper prepends `https://help.sap.com` and the viewer has a client-side fallback.

## Related Tools

This project is part of a wider SAP SuccessFactors tool suite. Start with [SF Compass](https://sahirvhora.github.io/sf-compass/) for the full hub.

| Tool | Purpose |
|------|---------|
| [SF Compass](https://sahirvhora.github.io/sf-compass/) | Feasibility answers, implementation guidance, and links to the full tool suite |
| **SF Release Update** | Release impact tracking and testing focus |
| [SF Impact Brief](https://sahirvhora.github.io/sf-impact-brief/) | Personalised release briefs from this data - select modules, get a tiered action plan |
| [SF Pay Transparency](https://sahirvhora.github.io/sf-pay-transparency/) | EU Pay Transparency readiness and evidence workflow framing |
| [SF Value Navigator](https://sahirvhora.github.io/sf-value-navigator/) | Value realisation and sponsor-facing consulting framework |
| [SF Position Integrity Checker](https://sahirvhora.github.io/sf-position-integrity/) | Position hierarchy, incumbency, and EC data-quality validation |
| [SAPSF ObjectSync](https://github.com/SahirVhora/sapsf-objectsync) | Controlled foundation-object synchronisation between SF environments |

## License

MIT

---

## Part of the SF Compass Suite

One of 10 free, open tools for SAP SuccessFactors consultants. Explore the full suite at [SF Compass](https://sahirvhora.github.io/sf-compass/).

Related tools:

- [Impact Brief](https://github.com/SahirVhora/sf-impact-brief) - Personalised release impact briefs
- [Agent Skills](https://github.com/SahirVhora/sf-agent-skills) - AI skills for SF consultants
- [Config Debt Radar](https://github.com/SahirVhora/sf-config-debt-radar) - Scan EC configuration debt - CLI, dashboard, MCP server
