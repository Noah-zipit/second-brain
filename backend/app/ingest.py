"""Ingest the real job-hunt pipeline into the store.

Sources:
  - ~/workspace/goals/job-application-campaign/hidden_files/applied.json (list of dicts)
  - ~/workspace/client-leads/daily-leads-YYYY-MM-DD.md (markdown lead files)

Run:  python3 backend/app/ingest.py
Writes: data/store.json
"""

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.store import JsonStore  # noqa: E402

HOME = Path.home()
APPLIED = HOME / "workspace/goals/job-application-campaign/hidden_files/applied.json"
LEADS_GLOB = str(HOME / "workspace/client-leads/daily-leads-2*.md")
OUT = HOME / "workspace/second-brain/data/store.json"

PLATFORMS = ["threads", "x", "freeindex", "hn", "linkedin", "reddit",
             "telegram", "discord", "indiehackers", "producthunt", "facebook"]


def parse_date(text: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text or "")
    return m.group(1) if m else ""


def parse_lead_file(path: Path):
    """Parse one daily-leads markdown file. Returns list of lead dicts."""
    text = path.read_text(encoding="utf-8", errors="replace")
    sweep = parse_date(path.name)
    leads = []
    blocks = re.split(r"(?m)^### ", text)
    for block in blocks[1:]:
        header, _, body = block.partition("\n")
        name = header.split("—")[0].strip()
        name = re.sub(r"^\d+\.\s*", "", name)  # strip "1. "
        sm = re.search(r"(\d)/5", header)
        score = int(sm.group(1)) if sm else 0

        fields = {}
        for line in body.splitlines():
            m = re.match(r"\s*-\s*\*\*([^*]+):\*\*\s*(.*)", line)
            if m:
                fields[m.group(1).strip().lower()] = m.group(2).strip()
            elif line.strip().startswith("— "):
                # draft signature continuation line — ignore
                continue

        posted = parse_date(fields.get("posted", "")) or sweep
        platform = ""
        hay = (header + " " + fields.get("contact", "") + " " + fields.get("source", "")).lower()
        for p in PLATFORMS:
            if p in hay:
                platform = p
                break
        status_note = ""
        for k, v in fields.items():
            if k.startswith("status"):
                status_note = v
                break
        leads.append({
            "name": name, "source_platform": platform or "unknown",
            "source_url": fields.get("source", ""), "contact": fields.get("contact", ""),
            "posted_date": posted, "sweep_date": sweep,
            "fit": fields.get("fit", ""), "score": score,
            "draft": fields.get("draft", ""), "status_note": status_note,
        })
    return leads


def main():
    store = JsonStore(OUT)
    # start clean every run — sources are the truth
    store.db = {"companies": [], "applications": [], "leads": [],
                "events": [], "edges": []}
    store._companies = {}

    # 1. applications
    apps = json.loads(APPLIED.read_text())
    for a in apps:
        store.add_application(
            company=a.get("company", "unknown"),
            role=a.get("role", ""),
            platform=a.get("platform", ""),
            url=a.get("url", ""),
            date=a.get("date", ""),
            status=a.get("status", ""),
            reason=a.get("reason", ""),
            evidence=a.get("evidence", ""),
        )
        if a.get("status") == "rejected":
            store.add_event("rejected",
                            f"Rejected: {a.get('company')} — {a.get('role')}",
                            (a.get("evidence") or "")[:300],
                            a.get("date", ""))

    # 2. leads
    n_leads = 0
    for f in sorted(glob.glob(LEADS_GLOB)):
        for lead in parse_lead_file(Path(f)):
            store.add_lead(**lead)
            n_leads += 1

    store.save()
    s = store.stats()
    print(f"ingested {s['applications_total']} applications, {n_leads} leads, "
          f"{s['companies_total']} companies, {s['events_total']} events -> {OUT}")


if __name__ == "__main__":
    main()
