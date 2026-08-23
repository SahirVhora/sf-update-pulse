#!/usr/bin/env python3
"""
SAP SF Release Updates - Scraper
Fetches the latest SAP SuccessFactors What's New data and outputs structured JSON.
Run: python3 scraper.py
Output: data/updates.json
"""

import html
import json
import re
import sys
import urllib.parse
import urllib.robotparser
from contextlib import suppress
from datetime import datetime
from pathlib import Path

# --- Config ---
BASE_URL = "https://help.sap.com/whats-new/8fcf4960eea24f78b1d7613da406a885"
OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "updates.json"
INDEX_FILE = Path(__file__).parent / "index.html"
# How many pages to fetch (25 items per page). Set high, script stops when no more data.
MAX_PAGES = 50


def parse_release_version(version: str):
    """Return (year, half) for labels like '1H 2026' or '2H 2026 (Preview)'."""
    match = re.match(r"([12])H\s+(\d{4})", (version or "").strip())
    if not match:
        return None
    return int(match.group(2)), int(match.group(1))


def preview_start_for(year: int, half: int) -> datetime:
    """SAP SF preview windows generally start in April for 1H and October for 2H."""
    month = 4 if half == 1 else 10
    return datetime(year, month, 1)


def default_published_version(now: datetime) -> str:
    """Fallback to the latest release whose preview window has started."""
    if now.month >= 10:
        return f"2H {now.year} (Preview)" if datetime(now.year, 11, 15) > now else f"2H {now.year}"
    if now.month >= 4:
        return f"1H {now.year}"
    return f"2H {now.year - 1}"


def planning_window_versions(candidates: list[str], now: datetime) -> list[str]:
    """Keep current release plus near-future planning releases.

    SAP exposes placeholders many years ahead. For planning, keep the latest
    release whose preview has started plus the rest of that year and next year.
    On 2026-06-15 this means 1H 2026, 2H 2026, 1H 2027, and 2H 2027.
    """
    parsed = []
    for version in candidates:
        parsed_version = parse_release_version(version)
        if not parsed_version:
            continue
        year, half = parsed_version
        parsed.append((year, half, version.strip()))
    if not parsed:
        return [default_published_version(now)]

    published = [(year, half) for year, half, _ in parsed if preview_start_for(year, half) <= now]
    if published:
        start_year, start_half = max(published)
    else:
        fallback = default_published_version(now)
        parsed_fallback = parse_release_version(fallback)
        start_year, start_half = parsed_fallback if parsed_fallback else (now.year, 1)

    end_year = start_year + 1
    planned = [
        (year, half, version)
        for year, half, version in parsed
        if (year, half) >= (start_year, start_half) and year <= end_year
    ]
    planned.sort(key=lambda row: (row[0], row[1]))
    return [version for _, _, version in planned]


# --- Impact Classification ---
def classify_impact(
    change_type: str,
    lifecycle: str,
    action: str,
    enablement: str,
    major_or_minor: str = "",
) -> dict:
    """Classify impact from SAP's distinct release-note dimensions.

    SAP's current table exposes Type (New/Changed), Major or Minor,
    Lifecycle, Action (Required/Recommended/Info only), and Enablement as
    separate columns. Keeping those semantics separate prevents every row
    falling through to Low when SAP changes a column label or meaning.
    """
    change_type = (change_type or "").strip().lower()
    lifecycle = (lifecycle or "").strip().lower()
    action = (action or "").strip().lower()
    enablement = (enablement or "").strip().lower()
    major_or_minor = (major_or_minor or "").strip().lower()

    if lifecycle == "deleted":
        return {"level": "critical", "label": "Critical", "color": "#ef4444"}
    if lifecycle == "deprecated":
        level = "critical" if action == "required" else "high"
        return {
            "level": level,
            "label": level.title(),
            "color": "#ef4444" if level == "critical" else "#f97316",
        }

    # Required work and forced major changes need preview-cycle ownership.
    if action == "required" or (major_or_minor == "major" and enablement == "automatically on"):
        return {"level": "high", "label": "High", "color": "#f97316"}

    # Recommended, major, or customer-configured changes merit review/testing.
    if (
        action == "recommended"
        or major_or_minor == "major"
        or enablement == "customer configured"
        or (change_type == "changed" and enablement == "automatically on")
    ):
        return {"level": "medium", "label": "Medium", "color": "#eab308"}

    return {"level": "low", "label": "Low", "color": "#22c55e"}


def generate_plain_english(
    change_type: str,
    lifecycle: str,
    action: str,
    enablement: str,
    major_or_minor: str,
    title: str,
    description: str,
) -> str:
    """Generate a contextual plain-English summary from the current schema."""
    change_type = (change_type or "").strip().lower()
    lifecycle = (lifecycle or "").strip().lower()
    action = (action or "").strip().lower()
    enablement = (enablement or "").strip().lower()
    major_or_minor = (major_or_minor or "").strip().lower()

    # Extract first sentence for context
    first_sentence = description.split(".")[0].strip() if description else ""
    # Shorten very long sentences
    if len(first_sentence) > 200:
        first_sentence = first_sentence[:197] + "..."
    context_sentence = first_sentence
    if context_sentence and not context_sentence.endswith((".", "!", "?", "...")):
        context_sentence += "."

    # Extract dates if present
    import re

    dates = re.findall(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
        description,
    )
    date_str = ""
    if dates:
        # Find full date match (not just captured month group)
        full_match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
            description,
        )
        if full_match:
            date_str = f" Timeline: {full_match.group(0)}."

    # Detect urgency words
    urgent_words = ["deleted", "removed", "no longer available", "end of", "must"]
    is_urgent = any(w in description.lower() for w in urgent_words)

    if lifecycle == "deprecated":
        if is_urgent:
            return f"Will stop working - you need a replacement.{date_str} Check the linked SAP Note for migration steps."
        return f"Being phased out. Start planning your move to the replacement now.{date_str}"

    if lifecycle == "deleted":
        return f"Already removed. If you relied on this, switch to the alternative immediately.{date_str}"

    if change_type == "new":
        if action == "required":
            return f"New capability requiring action.{date_str} {context_sentence} Review the SAP steps and test before production."
        if major_or_minor == "major":
            return f"Significant new capability - could change how you work. {context_sentence} Review config steps and test in sandbox first."
        if enablement == "customer configured":
            return f"Available when you're ready. {context_sentence} Test in non-production before enabling broadly."
        return f"Active automatically - no setup needed. {context_sentence}"

    if change_type == "changed":
        if action == "required":
            return f"Action required.{date_str} {context_sentence} Assign an owner and validate the change before production."
        if is_urgent:
            return f"Important change to review.{date_str} {context_sentence}"
        if major_or_minor == "major":
            return f"Significant update. {context_sentence} Plan configuration changes and communicate to users."
        if enablement == "customer configured" or action == "recommended":
            return f"Minor update - configure if useful. {context_sentence}"
        return f"Updated automatically. {context_sentence}"

    # Fallback with context
    return f"Review this change. {context_sentence}"


# --- Scraping ---
def _check_robots(url: str, user_agent: str = "*") -> None:
    """Fetch robots.txt and warn (not abort) if the path is disallowed.

    A warning rather than a hard exit because SAP's help portal is public
    and meant to be indexed. This is a polite check, not a gate.
    """
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        if not rp.can_fetch(user_agent, url):
            print(
                f"WARNING: robots.txt at {robots_url} disallows crawling {url}. Proceeding anyway (public page)."
            )
    except Exception as exc:
        print(f"WARNING: Could not fetch robots.txt from {robots_url}: {exc}")


def build_column_index(header_cells) -> dict[str, int]:
    """Map each header to its first table index, ignoring duplicate table copies."""
    column_index = {}
    for index, header in enumerate(header_cells):
        label = (header.inner_text() or "").strip()
        if label:
            column_index.setdefault(label, index)
    return column_index


def read_table_cell(cells, column_index: dict[str, int], field: str, fallback: int) -> str:
    """Read a table cell by header, using position only when no headers exist."""
    field_lower = field.lower()
    for name, index in column_index.items():
        if name.lower() == field_lower and index < len(cells):
            return (cells[index].inner_text() or "").strip()
    if not column_index and fallback < len(cells):
        return (cells[fallback].inner_text() or "").strip()
    return ""


def read_table_alias(cells, column_index: dict[str, int], aliases) -> str:
    """Return the first non-empty header alias or layout fallback."""
    for field, fallback in aliases:
        value = read_table_cell(cells, column_index, field, fallback)
        if value:
            return value
    return ""


def validate_items(items: list[dict]) -> None:
    """Fail before publishing when the scraped semantic columns look broken."""
    if not items:
        raise ValueError("No release items were extracted")

    change_types = {(item.get("changeType") or "").strip().lower() for item in items}
    actions = {(item.get("action") or "").strip().lower() for item in items}
    lifecycles = {(item.get("lifecycle") or "").strip().lower() for item in items}

    if not change_types.intersection({"new", "changed"}):
        raise ValueError("SAP Type column is missing expected New/Changed values")
    if not actions.intersection({"required", "recommended", "info only"}):
        raise ValueError("SAP Action column is missing expected action values")
    if not lifecycles.intersection(
        {"general availability", "restricted availability", "deprecated", "deleted"}
    ):
        raise ValueError("SAP Lifecycle column is missing expected lifecycle values")

    recognized_signal = any(
        (item.get("action") or "").strip().lower() in {"required", "recommended"}
        or (item.get("lifecycle") or "").strip().lower() in {"deprecated", "deleted"}
        for item in items
    )
    if recognized_signal and all(
        (item.get("impact") or {}).get("level") == "low" for item in items
    ):
        raise ValueError("Impact classification collapsed to Low for every item")


def scrape_with_playwright():
    """Use Playwright to extract all rows from the What's New Viewer, across all available versions."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright not installed. Run: pip install playwright && playwright install chromium"
        )
        sys.exit(1)

    _check_robots(BASE_URL)
    all_items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        """)
        page = context.new_page()

        print(f"Loading {BASE_URL} ...")
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)
        except Exception as e:
            print(f"Initial load warning: {e}. Trying load event...")
            page.goto(BASE_URL, wait_until="load", timeout=120000)

        # Dismiss cookie consent
        print("Checking for cookie consent banner...")
        try:
            consent_btn = page.wait_for_selector(
                'button:has-text("Accept All"), button:has-text("Accept Cookies"), '
                'button:has-text("OK"), button#truste-consent-button, '
                'a:has-text("Accept All"), .trustarc-agree-btn',
                timeout=15000,
            )
            if consent_btn:
                print("Dismissing cookie consent banner...")
                consent_btn.click()
                page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"No cookie consent banner found (or already accepted): {exc}")

        with suppress(Exception):
            page.evaluate("""
                const banners = document.querySelectorAll('#truste-consent-track, .trustarc-banner, [id*="consent_blackbar"]');
                banners.forEach(b => b.style.display = 'none');
            """)

        print("Waiting for page to render...")
        try:
            page.wait_for_selector("table tbody tr, button:has-text('Product')", timeout=45000)
        except Exception as exc:
            print(f"WARNING: Selectors not found. Page might not have loaded fully: {exc}")

        page.wait_for_timeout(5000)

        # --- Discover available versions ---
        available_versions = []

        # Strategy 1: Look for the version filter button's current display text
        try:
            ver_btn = page.query_selector('button:has-text("Software Version")')
            if ver_btn:
                btn_text = ver_btn.inner_text().strip()
                print(f"  Version button text: '{btn_text}'")
                ver_btn.click()
                page.wait_for_timeout(2500)

                # The SAP UI5 dialog with version checkboxes should now be open.
                # Strategy 2a: Look for SAP UI5 list items with version patterns
                raw_candidates = page.evaluate("""
                    () => {
                        const results = [];
                        // Target SAP UI5 dialog/popover specifically
                        const popups = document.querySelectorAll('.sapMDialog, .sapMPopover, [role="dialog"], .sapUiRespGrid');
                        const containers = popups.length > 0 ? popups : [document];
                        containers.forEach(container => {
                            // SAP UI5 checkboxes with labels
                            container.querySelectorAll('.sapMCb').forEach(cb => {
                                const label = cb.querySelector('.sapMCbLabel');
                                if (label) {
                                    const t = label.textContent.trim();
                                    if (/^[12]H\\s+20\\d\\d/.test(t)) results.push(t);
                                }
                            });
                            // Also check regular labels near checkboxes
                            container.querySelectorAll('label').forEach(lbl => {
                                const t = lbl.textContent.trim();
                                // Only match clean version strings: "1H 2026", "2H 2026", "2H 2026 (Preview)"
                                if (/^[12]H\\s+20\\d\\d(\\s*\\(Preview\\))?$/.test(t) && !results.includes(t)) {
                                    results.push(t);
                                }
                            });
                            // Check list items
                            container.querySelectorAll('li, [role="option"]').forEach(li => {
                                const t = li.textContent.trim();
                                if (/^[12]H\\s+20\\d\\d/.test(t) && t.length < 30 && !results.includes(t)) {
                                    results.push(t);
                                }
                            });
                        });
                        return results;
                    }
                """)
                print(f"  Raw version candidates: {raw_candidates}")

                # Keep versions in the planning window. SAP exposes placeholders
                # many years ahead, but current + next year is useful for planning.
                for v in raw_candidates:
                    v = v.strip()
                    if parse_release_version(v):
                        available_versions.append(v)

                # Close dropdown by pressing Escape (more reliable than clicking body)
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  Version discovery error: {e}")

        # Strategy 3: Fallback to latest release whose preview has started.
        if not available_versions:
            print("  No versions found via dropdown. Using latest published release fallback.")
            available_versions = [default_published_version(datetime.now())]

        # Deduplicate and sort (1H before 2H). Prefer "(Preview)" variants when duplicates exist.
        seen_ver = {}
        for v in available_versions:
            v = v.strip()
            key = re.sub(r"\s*\(Preview\)\s*", "", v).strip()
            # Keep the variant with "(Preview)" if both exist
            if key not in seen_ver or ("(Preview)" in v and "(Preview)" not in seen_ver[key]):
                seen_ver[key] = v
        deduped = list(seen_ver.values())
        available_versions = sorted(
            deduped,
            key=lambda x: (
                (re.search(r"\d{4}", x).group() if re.search(r"\d{4}", x) else "0")
                + ("0" if "1H" in x else "1")
            ),
        )
        available_versions = planning_window_versions(available_versions, datetime.now())

        # Append "(Preview)" to 2H versions whose production date is still in the future
        final_versions = []
        for v in available_versions:
            match = re.match(r"2H\s+(\d{4})", v)
            if match:
                year = int(match.group(1))
                prod_date = datetime(year, 11, 15)
                if prod_date > datetime.now() and "(Preview)" not in v:
                    v = v + " (Preview)"
            final_versions.append(v)
        available_versions = final_versions

        print(f"Selected versions: {available_versions}")

        # --- Scrape each version ---
        prev_first_title = None  # Track first item of previous version to detect failed switches
        for ver_idx, version_name in enumerate(available_versions):
            print(f"\n--- Scraping version: {version_name} ---")

            if ver_idx > 0:
                # Switch to this version
                try:
                    # Strip "(Preview)" for lookup since SAP's dropdown uses plain version names
                    lookup_name = re.sub(r"\s*\(Preview\)\s*", "", version_name).strip()
                    ver_btn = page.query_selector('button:has-text("Software Version")')
                    if ver_btn:
                        ver_btn.click()
                        page.wait_for_timeout(1500)
                        # Find and click the option - try exact text match first
                        option = page.query_selector(f'text="{lookup_name}"')
                        if not option:
                            option = page.query_selector(f'[title="{lookup_name}"]')
                        if not option:
                            # Try SAP UI5 checkbox label
                            labels = page.query_selector_all(".sapMCbLabel, label")
                            for lbl in labels:
                                if lbl.inner_text().strip() == lookup_name:
                                    option = lbl
                                    break
                        if option:
                            option.click()
                            page.wait_for_timeout(3000)  # Wait for data reload
                        else:
                            print(f"  Could not find option for {lookup_name}, skipping.")
                            # Close dropdown and continue
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(500)
                            continue
                        # Close dropdown after selection
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(500)
                except Exception as e:
                    print(f"  Failed to switch version: {e}")
                    continue

            # Detect column order from <th> headers in the table thead (auto-adapts
            # if SAP reorders or renames columns). Falls back to legacy positional
            # indices when the header row is missing.
            column_index: dict[str, int] = {}
            try:
                header_cells = page.query_selector_all("table thead th")
                column_index = build_column_index(header_cells)
                if column_index:
                    print(f"  Detected table columns: {list(column_index.keys())}")
            except Exception as e:
                print(f"  Column header detection failed: {e}")

            def _cell(
                cells,
                field: str,
                fallback: int,
                current_column_index=column_index,
            ) -> str:
                return read_table_cell(cells, current_column_index, field, fallback)

            # Extract all pages for this version
            page_num = 1
            version_items = 0
            while page_num <= MAX_PAGES:
                rows = page.query_selector_all("table tbody tr")
                if not rows:
                    print(f"  No rows on page {page_num}. Done with {version_name}.")
                    break

                # --- Guards against failed version switches ---
                # Guard 1: if the first page yields 0 valid rows, the version has no data
                valid_row_count = sum(1 for row in rows if len(row.query_selector_all("td")) >= 8)
                if page_num == 1 and valid_row_count == 0:
                    print(
                        f"  ⚠ Version {version_name} returned 0 items - skipping (no data published yet)."
                    )
                    break

                # Guard 2: if first item matches previous version's first item, the switch likely failed
                if ver_idx > 0 and page_num == 1 and prev_first_title:
                    first_cell = rows[0].query_selector("td")
                    if first_cell:
                        current_first_title = (
                            (first_cell.inner_text() or "").strip().removeprefix("Preview ")
                        )
                        if current_first_title == prev_first_title:
                            print(
                                f"  ⚠ Version switch to {version_name} appears to have failed - first item matches {prev_first_title[:60]}..."
                            )
                            print(
                                f"  Skipping {version_name} (data unchanged from previous version)."
                            )
                            break
                # --- End guards ---

                for row in rows:
                    cells = row.query_selector_all("td")
                    if len(cells) < 8:
                        continue

                    title = (cells[0].inner_text() or "").strip().removeprefix("Preview ")
                    description = (
                        (cells[1].inner_text() or "").strip().removesuffix("See More").strip()
                    )
                    product = (cells[2].inner_text() or "").strip()
                    module_raw = (cells[3].inner_text() or "").strip()
                    module = module_raw.split("\n")[0].strip()
                    feature = _cell(cells, "Feature", 5)
                    change_type = _cell(cells, "Type", 6)
                    major_or_minor = _cell(cells, "Major or Minor", 7)
                    lifecycle = _cell(cells, "Lifecycle", 8)
                    action = _cell(cells, "Action", 9)
                    enablement = _cell(cells, "Enablement", 10)
                    # Header-aware extraction. New SAP column layout (as of June 2026):
                    # Title, Description, Product, Module, Business Process Variant,
                    # Feature, Type, Major or Minor, Lifecycle, Action, Enablement,
                    # Reference Number, Demo, Software Version, Valid as Of,
                    # Latest Revision, Document ID.
                    # Positional fallbacks prefer the current 17-column layout,
                    # then the legacy layout only when the current index is absent.
                    ref_number = read_table_alias(
                        cells,
                        column_index,
                        (
                            ("Reference Number", 11),
                            ("Component / Reference Number", 11),
                            ("Ref. Number", 11),
                            ("Reference", 11),
                            ("Legacy Reference Number", 8),
                        ),
                    )
                    demo = read_table_alias(
                        cells,
                        column_index,
                        (
                            ("Demo", 12),
                            ("Demo Available", 12),
                            ("Legacy Demo", 9),
                        ),
                    )
                    version_field = (
                        _cell(cells, "Software Version", 13)
                        or _cell(cells, "Version", 13)
                        or _cell(cells, "Software Version", 10)
                    )
                    valid_as_of = (
                        _cell(cells, "Valid as Of", 14)
                        or _cell(cells, "Valid As Of", 14)
                        or _cell(cells, "Valid as of", 14)
                        or _cell(cells, "Valid as Of", 14)
                    )
                    latest_revision = _cell(cells, "Latest Revision", 15) or _cell(
                        cells, "Latest revision", 15
                    )

                    see_more_link = ""
                    see_more_el = cells[1].query_selector("a")
                    if see_more_el:
                        href = see_more_el.get_attribute("href") or ""
                        if href.startswith("/"):
                            href = "https://help.sap.com" + href
                        see_more_link = href

                    impact = classify_impact(
                        change_type,
                        lifecycle,
                        action,
                        enablement,
                        major_or_minor,
                    )
                    plain_english = generate_plain_english(
                        change_type,
                        lifecycle,
                        action,
                        enablement,
                        major_or_minor,
                        title,
                        description,
                    )

                    all_items.append(
                        {
                            "title": title,
                            "description": description,
                            "product": product,
                            "module": module,
                            "feature": feature,
                            "changeType": change_type,
                            "majorOrMinor": major_or_minor,
                            "lifecycle": lifecycle,
                            "action": action,
                            "enablement": enablement,
                            "refNumber": ref_number,
                            "demo": demo,
                            "version": version_field,
                            "validAsOf": valid_as_of,
                            "latestRevision": latest_revision,
                            "sapLink": see_more_link,
                            "impact": impact,
                            "plainEnglish": plain_english,
                            "releaseVersion": version_name,
                        }
                    )
                    version_items += 1

                # After first page of a successful scrape, snapshot the first item for next version's guard
                if page_num == 1 and version_items > 0:
                    first_cell = rows[0].query_selector("td")
                    if first_cell:
                        prev_first_title = (
                            (first_cell.inner_text() or "").strip().removeprefix("Preview ")
                        )

                print(
                    f"  Page {page_num}: {len(rows)} rows (total for {version_name}: {version_items})"
                )

                # Next page — click the page-number button matching page_num + 1.
                # SAP redesigned their UI to use numbered pagination buttons instead
                # of a "Next page" button. Each button has class "pagination".
                next_page_num = page_num + 1
                next_btn = page.query_selector(
                    f'button.pagination:not([disabled])[title="{next_page_num}"]'
                )
                if not next_btn:
                    # Try matching by exact text content instead of title
                    all_page_btns = page.query_selector_all("button.pagination:not([disabled])")
                    for btn in all_page_btns:
                        txt = (btn.inner_text() or "").strip()
                        if txt == str(next_page_num):
                            next_btn = btn
                            break

                if not next_btn:
                    print(f"  No 'Next' button. Done with {version_name}.")
                    break

                if next_btn.get_attribute("disabled") is not None:
                    print(f"  Next disabled. Reached last page of {version_name}.")
                    break

                next_btn.click()
                page.wait_for_timeout(2000)
                page_num += 1

        browser.close()

    return all_items


def calculate_release_dates(available_versions, scraped_at=None):
    """
    Calculate estimated release dates based on SAP's typical biannual schedule.

    SAP SuccessFactors releases follow this pattern:
    - 1H YYYY: Preview in April, Production in mid-May
    - 2H YYYY: Preview in October, Production in mid-November

    Dates are marked as estimates if the production date is in the future
    relative to scraped_at. Called dynamically so new versions are handled
    automatically - no hardcoded year/month values needed.
    """
    if scraped_at is None:
        scraped_at = datetime.now()

    release_dates = {}

    for version in available_versions:
        # Normalize: strip " (Preview)" suffix for key lookup
        clean_version = re.sub(r"\s*\(Preview\)\s*", "", version).strip()

        # Parse version like "1H 2026" or "2H 2026"
        match = re.match(r"(\d)H\s+(\d{4})", clean_version)
        if not match:
            print(f"  [WARN] Could not parse version: '{version}', skipping release dates.")
            continue

        half = int(match.group(1))
        year = int(match.group(2))

        if half == 1:
            preview_str = f"April {year}"
            production_str = f"May 15, {year}"
        else:  # 2H
            preview_str = f"October {year}"
            production_str = f"November 15, {year}"

        # Check if production is in the future → mark as estimate
        try:
            prod_dt = datetime.strptime(production_str, "%B %d, %Y")
            if prod_dt > scraped_at:
                preview_str += " (est.)"
                production_str += " (est.)"
        except ValueError:
            pass

        release_dates[clean_version] = {"preview": preview_str, "production": production_str}

    return release_dates


def build_meta_summary(metadata: dict, items: list[dict]) -> tuple[str, str]:
    """Build title/description strings for HTML, Open Graph, and Twitter tags."""
    total = metadata.get("totalItems") or len(items)
    version_counts = metadata.get("versionCounts") or {}
    versions = list(version_counts.keys()) or metadata.get("availableVersions") or []
    version_text = ", ".join(versions) if versions else "the latest releases"
    impacts = {}
    for item in items:
        level = (item.get("impact") or {}).get("level", "low")
        impacts[level] = impacts.get(level, 0) + 1
    impact_text = ""
    if impacts.get("critical") or impacts.get("high"):
        impact_text = f" {impacts.get('critical', 0)} critical and {impacts.get('high', 0)} high impact items."
    refreshed = f" Last refreshed {metadata['lastScraped']}." if metadata.get("lastScraped") else ""
    title = f"SAP SF Release Updates - {total} SuccessFactors updates"
    description = (
        f"{total} SAP SuccessFactors release updates across {version_text}, "
        f"classified by impact and summarised in plain English."
        f"{impact_text}{refreshed}"
    )
    return title, description


def replace_meta_content(markup: str, selector_type: str, selector_value: str, content: str) -> str:
    escaped = html.escape(content, quote=True)
    pattern = rf'(<meta\s+{selector_type}="{re.escape(selector_value)}"\s+content=")[^"]*(">)'
    replacement = rf"\g<1>{escaped}\2"
    updated, count = re.subn(pattern, replacement, markup, count=1)
    if count:
        return updated
    insert_after = (
        '<meta name="twitter:card" content="summary_large_image">'
        if selector_type == "name" and selector_value.startswith("twitter:")
        else '<meta property="og:url" content="https://sahirvhora.github.io/sf-release-update/">'
    )
    tag = f'<meta {selector_type}="{selector_value}" content="{escaped}">'
    return updated.replace(insert_after, insert_after + "\n" + tag, 1)


def update_index_metadata(output: dict) -> None:
    if not INDEX_FILE.exists():
        print("[WARN] index.html missing; skipping static meta update.")
        return
    title, description = build_meta_summary(output["metadata"], output["items"])
    markup = INDEX_FILE.read_text(encoding="utf-8")
    markup = replace_meta_content(markup, "property", "og:title", title)
    markup = replace_meta_content(markup, "property", "og:description", description)
    markup = replace_meta_content(markup, "name", "twitter:title", title)
    markup = replace_meta_content(markup, "name", "twitter:description", description)
    markup = replace_meta_content(markup, "name", "description", description)
    title_escaped = html.escape(title, quote=False)
    markup = re.sub(
        r"<title>.*?</title>", f"<title>{title_escaped}</title>", markup, count=1, flags=re.DOTALL
    )
    INDEX_FILE.write_text(markup, encoding="utf-8")
    print(f"Updated static index.html metadata: {title}")


def main():
    print("=" * 60)
    print("SAP SF Release Updates - Scraper")
    print(f"Target: {BASE_URL}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Try Playwright extraction
    items = scrape_with_playwright()

    if not items:
        print("ERROR: No items extracted. Check the SAP page structure.")
        sys.exit(1)

    # Deduplicate within each release only. The same title can legitimately appear
    # in current and future planning releases, and the version switcher needs to
    # show the count/details for each release independently.
    seen = set()
    unique = []
    for item in items:
        key = (item.get("releaseVersion", ""), item["title"], item.get("refNumber", ""))
        if key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"\\nTotal extracted: {len(items)}")
    print(f"After dedup: {len(unique)}")

    validate_items(unique)

    # Annotate cross-version duplicates: items with identical (title, refNumber)
    # that appear in multiple releases get an `alsoInVersion` list so the
    # frontend can display a note (e.g. "Also appears in 1H 2026").
    title_ref_map: dict[tuple[str, str], list[dict]] = {}
    for item in unique:
        key = (item["title"], item.get("refNumber", ""))
        title_ref_map.setdefault(key, []).append(item)
    for matches in title_ref_map.values():
        versions = list({m["releaseVersion"] for m in matches})
        if len(versions) > 1:
            for item in matches:
                item["alsoInVersion"] = [v for v in versions if v != item["releaseVersion"]]
    cross_count = sum(1 for item in unique if "alsoInVersion" in item)
    if cross_count:
        print(f"Cross-version duplicates annotated: {cross_count} items")

    # Count by impact
    impact_counts = {}
    for item in unique:
        level = item["impact"]["level"]
        impact_counts[level] = impact_counts.get(level, 0) + 1
    print(f"Impact breakdown: {impact_counts}")

    # Count by module
    module_counts = {}
    for item in unique:
        mod = item["module"] or "Unknown"
        module_counts[mod] = module_counts.get(mod, 0) + 1
    print(f"Top modules: {dict(sorted(module_counts.items(), key=lambda x: -x[1])[:10])}")

    # Count by version
    version_counts = {}
    for item in unique:
        ver = item.get("releaseVersion", "Unknown")
        version_counts[ver] = version_counts.get(ver, 0) + 1
    print(f"Version breakdown: {version_counts}")

    # Build output
    available_versions = sorted({item.get("releaseVersion", "Unknown") for item in unique})
    scraped_at = datetime.now()
    release_dates = calculate_release_dates(available_versions, scraped_at)
    output = {
        "metadata": {
            "source": BASE_URL,
            "scrapedAt": scraped_at.isoformat(),
            "totalItems": len(unique),
            "lastScraped": scraped_at.strftime("%Y-%m-%d %H:%M UTC"),
            "availableVersions": available_versions,
            "versionCounts": version_counts,
            "releaseDates": release_dates,
        },
        "items": unique,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")
    update_index_metadata(output)

    print(f"\nSaved {len(unique)} items to {OUTPUT_FILE}")
    print("Done!")


if __name__ == "__main__":
    main()
