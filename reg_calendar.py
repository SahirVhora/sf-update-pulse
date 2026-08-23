"""
SAP release-update tracker - regulatory calendar extension.

Imports `research_data.json` from `the in-house toolkit` to track regulatory
deadlines that drive compliance work for SAP SuccessFactors customers:

    * EU Pay Transparency Directive (2023/970) - 7 Jun 2026 transposition
    * EU AI Act - high-risk obligations phasing (SF Recruiting, PMGM)
    * CSRD Wave 2 / Wave 3 reporting cycles
    * SAP ECC mainstream support end - 31 Dec 2027

Each milestone is verified against the primary source URL via HEAD request
(with a short timeout) so we surface stale / unreachable references early.

Outputs:
    data/reg_calendar.json   -- structured deadlines + URL reachability
    data/reg_calendar.md     -- human-readable digest for status page

Run standalone:
    python3 reg_calendar.py

Or as part of the existing scrape pipeline:
    from reg_calendar import build_reg_calendar
    build_reg_calendar(merge_into_output=output_dict)
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
RESEARCH_DATA = HERE.parent / "research" / "research_data.json"
OUTPUT_JSON = HERE / "data" / "reg_calendar.json"
OUTPUT_MD = HERE / "data" / "reg_calendar.md"


HEAD_TIMEOUT = 6
HEAD_USER_AGENT = "SAP release-update tracker-reg-calendar/1.0"


def _iso_to_date(raw):
    """Parse loose date/month strings into ISO date or first-of-month fallback."""
    s = (raw or "").strip()
    if not s:
        return None
    # Year-only
    if re.fullmatch(r"\d{4}", s):
        return datetime(int(s), 1, 1).date()
    # Quarter
    m = re.fullmatch(r"(\d{4})-Q([1-4])", s)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return datetime(year, (q - 1) * 3 + 1, 1).date()
    # Year-Month only
    if re.fullmatch(r"\d{4}-\d{2}", s):
        y, mo = s.split("-")
        return datetime(int(y), int(mo), 1).date()
    # ISO or 6 Oct 2024
    for fmt in ("%Y-%m-%d", "%Y-%m", "%d %b %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _http_head(url):
    """Best-effort HEAD; returns (http_status or None, error string or None)."""
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": HEAD_USER_AGENT},
            timeout=HEAD_TIMEOUT,
            allow_redirects=True,
        )
        return resp.status_code, None
    except requests.exceptions.HTTPError as e:
        # Some hosts reject HEAD - the status code carries the meaning.
        return (e.response.status_code if e.response is not None else None), None
    except (requests.exceptions.RequestException, TimeoutError, ConnectionError) as e:
        return None, str(e)


def _normalize_text(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def load_research_data():
    if not RESEARCH_DATA.exists():
        raise FileNotFoundError(
            f"research_data.json not found at {RESEARCH_DATA}. "
            "Run the in-house toolkit or supply research_data.json manually."
        )
    return json.loads(RESEARCH_DATA.read_text(encoding="utf-8"))


def build_reg_calendar(merge_into_output=None):
    """Build the regulatory calendar digest. Optionally merge into scraper output dict."""
    payload = load_research_data()
    today = datetime.utcnow().date()
    rows = []
    for entry in payload.get("regulatory_calendar", []):
        deadline = _iso_to_date(entry.get("date", ""))
        days_to = (deadline - today).days if deadline else None
        url = entry.get("source_url") or ""
        status, error = (None, None) if not url else _http_head(url)
        rows.append(
            {
                "label": entry.get("label", ""),
                "date_raw": entry.get("date", ""),
                "deadline_iso": deadline.isoformat() if deadline else None,
                "regions": entry.get("regions", []),
                "severity": entry.get("severity", "P2"),
                "source_url": url,
                "source_publication": entry.get("source_publication", "-"),
                "days_to_deadline": days_to,
                "url_status": status,
                "url_error": error,
                "trigger_pain_ids": _detect_trigger_pain_ids(payload, entry),
            }
        )

    rows.sort(key=lambda r: (r["deadline_iso"] or "9999-99-99", r["severity"]))
    digest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "schema_version": 1,
        "source": "the in-house toolkit",
        "now": today.isoformat(),
        "deadlines": rows,
        "summary": {
            "total": len(rows),
            "p0": sum(1 for r in rows if r["severity"] == "P0"),
            "p1": sum(1 for r in rows if r["severity"] == "P1"),
            "p2": sum(1 for r in rows if r["severity"] == "P2"),
            "reachable_urls": sum(1 for r in rows if (r["url_status"] or 0) < 400),
            "unreachable_urls": sum(1 for r in rows if r["url_error"] is not None),
            "next_30d": sum(
                1
                for r in rows
                if r["days_to_deadline"] is not None and 0 <= r["days_to_deadline"] <= 30
            ),
            "next_90d": sum(
                1
                for r in rows
                if r["days_to_deadline"] is not None and 0 <= r["days_to_deadline"] <= 90
            ),
        },
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTPUT_MD.write_text(_render_markdown(digest), encoding="utf-8")

    if isinstance(merge_into_output, dict):
        merge_into_output["metadata"]["regulatoryCalendar"] = digest["summary"]
        merge_into_output["regulatoryCalendar"] = digest

    return digest


def _detect_trigger_pain_ids(payload, entry):
    """Suggest pain findings whose headline is semantically close to this milestone."""
    label_norm = _normalize_text(entry.get("label", ""))
    scored = []
    for f in payload.get("findings", []):
        score = 0
        for s in (f.get("title", ""), f.get("summary", "")):
            s_norm = _normalize_text(s)
            for token in re.findall(r"[A-Za-z]{4,}", label_norm):
                if token in s_norm:
                    score += 2
        if score:
            scored.append((score, f["id"]))
    scored.sort(reverse=True)
    return [pid for _, pid in scored[:5]]


def _render_markdown(digest):
    lines = ["# Regulatory calendar (≤ 2027)", ""]
    lines.append(f"_Generated {digest['generated_at']} · source: `{digest['source']}`_")
    lines.append("")
    summary = digest["summary"]
    lines.append(
        f"**{summary['total']} tracked deadlines** · "
        f"{summary['p0']} P0 · {summary['p1']} P1 · {summary['p2']} P2 · "
        f"{summary['reachable_urls']} URLs verified reachable · "
        f"{summary['unreachable_urls']} unreachable"
    )
    lines.append("")
    lines.append("| Date | Label | Sev | Days left | URL reached? | Pain IDs |")
    lines.append("|---|---|---|---|---|---|")
    for r in digest["deadlines"]:
        status = (
            "✓ " + str(r["url_status"])
            if r["url_status"] and r["url_status"] < 400
            else (
                "FAIL: " + (r["url_error"] or str(r["url_status"]))
                if r["url_error"] or r["url_status"]
                else "(no URL)"
            )
        )
        days = f"{r['days_to_deadline']}" if r["days_to_deadline"] is not None else "-"
        labels = ", ".join(r["trigger_pain_ids"]) or "-"
        link = f"[link]({r['source_url']})" if r["source_url"] else "(no URL)"
        lines.append(
            f"| {r['date_raw']} | {r['label']} | {r['severity']} | {days} | {status} ({link}) | {labels} |"
        )
    return "\n".join(lines) + "\n"


def detect_regulatory_shifts(prior_path=OUTPUT_JSON):
    """Compare current digest vs a previous digest; return list of new/changed entries.

    Useful for the weekly cron: blow the whistle when a new regulatory milestone
    becomes imminent (≤ 30 days) or when a previously reachable URL becomes dead.
    """
    prior = {}
    if Path(prior_path).exists():
        try:
            prior = {
                d["label"]: d for d in json.loads(Path(prior_path).read_text()).get("deadlines", [])
            }
        except Exception:
            prior = {}
    cur = build_reg_calendar()
    alerts = []
    for d in cur["deadlines"]:
        prev = prior.get(d["label"])
        if not prev:
            alerts.append(f"NEW: {d['label']} ({d['date_raw']}, sev {d['severity']})")
            continue
        # URL regression
        if (prev.get("url_status") or 0) < 400 and (d.get("url_status") or 0) >= 400:
            alerts.append(
                f"URL DOWN: {d['label']} was {prev.get('url_status')} now {d.get('url_status') or d.get('url_error')}"
            )
    if cur["summary"]["next_30d"] > 0:
        alerts.append(f"WATCH: {cur['summary']['next_30d']} deadline(s) within 30 days.")
    return alerts


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "shifts":
        for line in detect_regulatory_shifts():
            print(line)
        return
    digest = build_reg_calendar()
    s = digest["summary"]
    print(
        f"Saved {OUTPUT_JSON.name} + {OUTPUT_MD.name}: "
        f"{s['total']} entries ({s['p0']} P0, {s['p1']} P1, {s['p2']} P2), "
        f"{s['reachable_urls']} URLs reachable, {s['unreachable_urls']} unreachable, "
        f"{s['next_30d']} within 30 days, {s['next_90d']} within 90 days."
    )


if __name__ == "__main__":
    main()
