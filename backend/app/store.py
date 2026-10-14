"""Second Brain storage layer — repository pattern.

Day 1 (Oct 14): JsonStore — real, working, file-backed store.
Day 2 (Oct 15–18, Graph Hacks window): FalkorDBStore implements the same
AbstractStore interface against FalkorDB via Cypher. The event rule requires
the graph implementation to be new work done during the window, so the stub
below only sketches the Cypher per method — nothing executes yet.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "unknown"


class AbstractStore(ABC):
    """Contract every store must honor. Add everything through the interface."""

    @abstractmethod
    def add_application(self, company, role, platform, url, date, status,
                        reason="", evidence=""):
        """Record a job application."""

    @abstractmethod
    def add_lead(self, name, source_platform, source_url, contact,
                 posted_date, sweep_date, fit, score, draft="", status_note=""):
        """Record a client/outreach lead."""

    @abstractmethod
    def add_event(self, kind, subject, note, date):
        """Record something that happened (rejected, replied, bounced, dm_sent, note)."""

    @abstractmethod
    def get_followups(self, limit=20):
        """Items needing action right now, most urgent first."""

    @abstractmethod
    def search(self, query, limit=20):
        """Substring search across companies, roles, lead names, notes."""

    @abstractmethod
    def stats(self):
        """Pipeline counts."""

    @abstractmethod
    def timeline(self, limit=50):
        """Chronological applications + leads + events, newest first."""


class JsonStore(AbstractStore):
    """File-backed store (data/store.json). The real Day-1 implementation."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists():
            self.db = json.loads(self.path.read_text())
        else:
            self.db = {"companies": [], "applications": [], "leads": [],
                       "events": [], "edges": []}
        self._companies = {c["id"]: c for c in self.db["companies"]}

    # -- internal helpers -------------------------------------------------
    def _company_id(self, name: str) -> str:
        cid = "company:" + slugify(name)
        if cid not in self._companies:
            self._companies[cid] = {"id": cid, "name": name, "location": ""}
            self.db["companies"].append(self._companies[cid])
        return cid

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.db, indent=1, ensure_ascii=False))

    # -- interface ---------------------------------------------------------
    def add_application(self, company, role, platform, url, date, status,
                        reason="", evidence=""):
        cid = self._company_id(company)
        aid = "app:" + slugify(f"{company}-{role}-{date}")
        self.db["applications"].append({
            "id": aid, "company_id": cid, "company": company, "role": role,
            "platform": platform, "url": url, "date": date, "status": status,
            "reason": reason, "evidence": evidence,
        })
        self.db["edges"].append({"type": "APPLIED_TO", "from": aid, "to": cid,
                                 "props": {"date": date}})
        if status == "rejected":
            self.db["edges"].append({"type": "REJECTED_BY", "from": aid, "to": cid,
                                     "props": {"date": date}})
        return aid

    def add_lead(self, name, source_platform, source_url, contact,
                 posted_date, sweep_date, fit, score, draft="", status_note=""):
        lid = "lead:" + slugify(f"{name}-{sweep_date}")
        self.db["leads"].append({
            "id": lid, "name": name, "source_platform": source_platform,
            "source_url": source_url, "contact": contact,
            "posted_date": posted_date, "sweep_date": sweep_date,
            "fit": fit, "score": score, "draft": draft, "status_note": status_note,
        })
        return lid

    def add_event(self, kind, subject, note, date):
        eid = f"event:{len(self.db['events'])}"
        self.db["events"].append({"id": eid, "kind": kind, "subject": subject,
                                  "note": note, "date": date})
        return eid

    def get_followups(self, limit=20):
        items = []
        for a in self.db["applications"]:
            if a["status"] in ("needs_user", "needs_browser"):
                items.append({
                    "type": "application", "title": f"{a['company']} — {a['role']}",
                    "detail": a.get("reason") or "Blocked on your input",
                    "date": a["date"], "action": "needs your input",
                })
            elif a["status"] in ("sent", "submitted"):
                items.append({
                    "type": "application", "title": f"{a['company']} — {a['role']}",
                    "detail": f"Applied {a['date']} via {a['platform']}; no reply yet",
                    "date": a["date"], "action": "awaiting reply",
                })
        for lead in self.db["leads"]:
            if lead["score"] >= 2 and not lead.get("status_note"):
                items.append({
                    "type": "lead", "title": lead["name"],
                    "detail": lead["fit"], "date": lead["sweep_date"],
                    "action": f"contact via {lead['contact'] or lead['source_platform']}",
                })
        items.sort(key=lambda i: i["date"] or "", reverse=True)
        return items[:limit]

    def search(self, query, limit=20):
        q = query.lower()
        hits = []
        for a in self.db["applications"]:
            if q in f"{a['company']} {a['role']} {a['platform']}".lower():
                hits.append({"kind": "application", **a})
        for lead in self.db["leads"]:
            if q in f"{lead['name']} {lead['fit']}".lower():
                hits.append({"kind": "lead", **lead})
        return hits[:limit]

    def stats(self):
        apps = self.db["applications"]
        by_status: dict[str, int] = {}
        for a in apps:
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        leads = self.db["leads"]
        return {
            "applications_total": len(apps),
            "applications_submitted": sum(
                1 for a in apps if a["status"].startswith("submitted") or a["status"] in ("sent",)),
            "applications_by_status": by_status,
            "leads_total": len(leads),
            "leads_contact_ready": sum(1 for l in leads if l["score"] >= 2),
            "companies_total": len(self.db["companies"]),
            "events_total": len(self.db["events"]),
        }

    def timeline(self, limit=50):
        rows = []
        for a in self.db["applications"]:
            rows.append({"kind": "application", "date": a["date"],
                         "title": f"{a['company']} — {a['role']}",
                         "detail": f"{a['status']} via {a['platform']}"})
        for lead in self.db["leads"]:
            rows.append({"kind": "lead", "date": lead["sweep_date"],
                         "title": lead["name"],
                         "detail": f"lead {lead['score']}/5 via {lead['source_platform']}"})
        for e in self.db["events"]:
            rows.append({"kind": "event", "date": e["date"],
                         "title": e["subject"], "detail": e["kind"]})
        rows.sort(key=lambda r: r["date"] or "", reverse=True)
        return rows[:limit]


class FalkorDBStore(AbstractStore):
    """FalkorDB-backed store. IMPLEMENT DURING THE EVENT WINDOW (Oct 15–18).

    Cypher sketches per method below. Connect with:
        from falkordb import FalkorDB
        db = FalkorDB(host="localhost", port=6379)
        g = db.select_graph("second_brain")
    """

    def __init__(self, host="localhost", port=6379):
        # from falkordb import FalkorDB
        # self.g = FalkorDB(host=host, port=port).select_graph("second_brain")
        raise NotImplementedError("Implement during the Graph Hacks window (Oct 15–18)")

    def add_application(self, company, role, platform, url, date, status,
                        reason="", evidence=""):
        # MERGE (c:Company {id:$cid}) SET c.name=$company
        # CREATE (a:Application {id:$aid, role:$role, platform:$platform, url:$url,
        #                        date:date($date), status:$status, reason:$reason})
        # CREATE (a)-[:APPLIED_TO {date:date($date)}]->(c)
        raise NotImplementedError

    def add_lead(self, name, source_platform, source_url, contact,
                 posted_date, sweep_date, fit, score, draft="", status_note=""):
        # CREATE (l:Lead {id:$lid, name:$name, source_platform:$sp, source_url:$su,
        #                 contact:$contact, posted_date:date($pd), sweep_date:date($sd),
        #                 fit:$fit, score:$score, draft:$draft})
        raise NotImplementedError

    def add_event(self, kind, subject, note, date):
        # CREATE (e:Event {kind:$kind, subject:$subject, note:$note, date:date($date)})
        raise NotImplementedError

    def get_followups(self, limit=20):
        # MATCH (a:Application) WHERE a.status IN ['needs_user','needs_browser']
        # RETURN a ORDER BY a.date DESC LIMIT $limit
        # UNION — leads: MATCH (l:Lead) WHERE l.score >= 2 AND NOT (l)-[:CONTACTED_VIA]-()
        # RETURN l ORDER BY l.sweep_date DESC
        raise NotImplementedError

    def search(self, query, limit=20):
        # MATCH (n) WHERE n:Application OR n:Lead
        #   AND (n.company CONTAINS $q OR n.role CONTAINS $q OR n.name CONTAINS $q)
        # RETURN n LIMIT $limit
        raise NotImplementedError

    def stats(self):
        # MATCH (a:Application) RETURN count(a) AS total, a.status, count(*)
        # MATCH (l:Lead) RETURN count(l)
        raise NotImplementedError

    def timeline(self, limit=50):
        # MATCH (n) WHERE n:Application OR n:Lead OR n:Event
        # RETURN n ORDER BY coalesce(n.date, n.sweep_date) DESC LIMIT $limit
        raise NotImplementedError
