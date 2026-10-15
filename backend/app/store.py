"""Second Brain storage layer — repository pattern.

Day 1 (Oct 14): JsonStore — real, working, file-backed store.
Day 2 (Oct 15–18, Graph Hacks window): FalkorDBStore implements the same
AbstractStore interface against FalkorDB via Cypher. The event rule requires
the graph implementation to be new work done during the window, so the stub
below only sketches the Cypher per method — nothing executes yet.
"""

from __future__ import annotations

import json
import os
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
    """FalkorDB-backed store — the Graph Hacks window work (built Oct 15, 2026).

    Same AbstractStore interface as JsonStore; every method's observable
    behavior matches JsonStore exactly. Graph model:

        (:Company {id, name, location})
        (:Application {id, company, role, platform, url, date, status, reason, evidence})
        (:Lead {id, name, source_platform, source_url, contact, posted_date,
                sweep_date, fit, score, draft, status_note})
        (:Event {id, kind, subject, note, date})

        (a:Application)-[:APPLIED_TO {date}]->(c:Company)
        (a:Application)-[:REJECTED_BY {date}]->(c:Company)   (status == "rejected")

    Dates are stored as ISO "YYYY-MM-DD" strings (same as store.json), so
    lexicographic ORDER BY matches JsonStore's string sort exactly.

    Connect with one of:
        FalkorDBStore()                                  # localhost:6379
        FalkorDBStore(host=..., port=...)
        FalkorDBStore(uri="redis://:pass@host:port")     # FalkorDB Cloud
    or via env: FALKORDB_URI, FALKORDB_HOST, FALKORDB_PORT.
    """

    GRAPH_NAME = "second_brain"

    def __init__(self, host=None, port=None, uri=None, username=None,
                 password=None, ssl=False):
        from urllib.parse import urlparse
        from falkordb import FalkorDB

        uri = uri or os.environ.get("FALKORDB_URI") or ""
        if uri:
            u = urlparse(uri)
            host = u.hostname or "localhost"
            port = u.port or 6379
            password = password or (u.password or None)
            username = username or (u.username or None)
            ssl = ssl or u.scheme in ("rediss", "tls")
        host = host or os.environ.get("FALKORDB_HOST", "localhost")
        port = int(port or os.environ.get("FALKORDB_PORT", "6379"))
        password = password or os.environ.get("FALKORDB_PASSWORD") or None

        self._db = FalkorDB(host=host, port=port, username=username,
                            password=password, ssl=ssl, socket_timeout=5)
        self.g = self._db.select_graph(self.GRAPH_NAME)
        # Fail fast if the server isn't reachable.
        self.g.query("RETURN 1")

    # -- internal helpers -------------------------------------------------
    def _q(self, cypher, params=None):
        res = self.g.query(cypher, params or {})
        return res.result_set

    @staticmethod
    def _props(row_node):
        """FalkorDB Node -> plain dict of properties."""
        return dict(row_node.properties)

    def _company_id(self, name: str) -> str:
        return "company:" + slugify(name)

    # -- interface ---------------------------------------------------------
    def add_application(self, company, role, platform, url, date, status,
                        reason="", evidence=""):
        cid = self._company_id(company)
        aid = "app:" + slugify(f"{company}-{role}-{date}")
        self._q(
            "MERGE (c:Company {id: $cid}) "
            "ON CREATE SET c.name = $company, c.location = '' "
            "CREATE (a:Application {id: $aid, company_id: $cid, company: $company, role: $role, "
            "platform: $platform, url: $url, date: $date, status: $status, "
            "reason: $reason, evidence: $evidence}) "
            "CREATE (a)-[:APPLIED_TO {date: $date}]->(c) "
            + ("CREATE (a)-[:REJECTED_BY {date: $date}]->(c) " if status == "rejected" else ""),
            {"cid": cid, "company": company, "aid": aid, "role": role,
             "platform": platform, "url": url, "date": date, "status": status,
             "reason": reason, "evidence": evidence})
        return aid

    def add_lead(self, name, source_platform, source_url, contact,
                 posted_date, sweep_date, fit, score, draft="", status_note=""):
        lid = "lead:" + slugify(f"{name}-{sweep_date}")
        self._q(
            "CREATE (l:Lead {id: $lid, name: $name, "
            "source_platform: $sp, source_url: $su, contact: $contact, "
            "posted_date: $pd, sweep_date: $sd, fit: $fit, score: $score, "
            "draft: $draft, status_note: $status_note})",
            {"lid": lid, "name": name, "sp": source_platform, "su": source_url,
             "contact": contact, "pd": posted_date, "sd": sweep_date,
             "fit": fit, "score": int(score), "draft": draft,
             "status_note": status_note})
        return lid

    def add_event(self, kind, subject, note, date):
        n = self._q("MATCH (e:Event) RETURN count(e)")[0][0]
        eid = f"event:{n}"
        self._q("CREATE (e:Event {id: $eid, kind: $kind, subject: $subject, "
                "note: $note, date: $date})",
                {"eid": eid, "kind": kind, "subject": subject,
                 "note": note, "date": date})
        return eid

    def _applications(self):
        return [self._props(r[0]) for r in
                self._q("MATCH (a:Application) RETURN a")]

    def _leads(self):
        return [self._props(r[0]) for r in self._q("MATCH (l:Lead) RETURN l")]

    def get_followups(self, limit=20):
        limit = int(limit)
        items = []
        for a in self._applications():
            if a["status"] in ("needs_user", "needs_browser"):
                items.append({
                    "type": "application",
                    "title": f"{a['company']} — {a['role']}",
                    "detail": a.get("reason") or "Blocked on your input",
                    "date": a["date"], "action": "needs your input",
                })
            elif a["status"] in ("sent", "submitted"):
                items.append({
                    "type": "application",
                    "title": f"{a['company']} — {a['role']}",
                    "detail": f"Applied {a['date']} via {a['platform']}; no reply yet",
                    "date": a["date"], "action": "awaiting reply",
                })
        for lead in self._leads():
            if int(lead["score"]) >= 2 and not lead.get("status_note"):
                items.append({
                    "type": "lead", "title": lead["name"],
                    "detail": lead["fit"], "date": lead["sweep_date"],
                    "action": f"contact via {lead['contact'] or lead['source_platform']}",
                })
        items.sort(key=lambda i: i["date"] or "", reverse=True)
        return items[:limit]

    def search(self, query, limit=20):
        limit = int(limit)
        q = query.lower()
        hits = []
        for r in self._q(
                "MATCH (a:Application) "
                "WHERE toLower(coalesce(a.company,'') + ' ' + coalesce(a.role,'') "
                "  + ' ' + coalesce(a.platform,'')) CONTAINS $q RETURN a",
                {"q": q}):
            a = self._props(r[0])
            hits.append({"kind": "application", **a})
        for r in self._q(
                "MATCH (l:Lead) "
                "WHERE toLower(coalesce(l.name,'') + ' ' + coalesce(l.fit,'')) "
                "CONTAINS $q RETURN l",
                {"q": q}):
            lead = self._props(r[0])
            hits.append({"kind": "lead", **lead})
        return hits[:limit]

    def stats(self):
        apps = self._applications()
        by_status: dict[str, int] = {}
        for a in apps:
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        leads = self._leads()
        n_companies = self._q("MATCH (c:Company) RETURN count(c)")[0][0]
        n_events = self._q("MATCH (e:Event) RETURN count(e)")[0][0]
        return {
            "applications_total": len(apps),
            "applications_submitted": sum(
                1 for a in apps if a["status"].startswith("submitted") or a["status"] in ("sent",)),
            "applications_by_status": by_status,
            "leads_total": len(leads),
            "leads_contact_ready": sum(1 for l in leads if int(l["score"]) >= 2),
            "companies_total": n_companies,
            "events_total": n_events,
        }

    def timeline(self, limit=50):
        limit = int(limit)
        rows = []
        for r in self._q("MATCH (a:Application) RETURN a ORDER BY a.date DESC"):
            a = self._props(r[0])
            rows.append({"kind": "application", "date": a["date"],
                         "title": f"{a['company']} — {a['role']}",
                         "detail": f"{a['status']} via {a['platform']}"})
        for r in self._q("MATCH (l:Lead) RETURN l ORDER BY l.sweep_date DESC"):
            lead = self._props(r[0])
            rows.append({"kind": "lead", "date": lead["sweep_date"],
                         "title": lead["name"],
                         "detail": f"lead {lead['score']}/5 via {lead['source_platform']}"})
        for r in self._q("MATCH (e:Event) RETURN e ORDER BY e.date DESC"):
            e = self._props(r[0])
            rows.append({"kind": "event", "date": e["date"],
                         "title": e["subject"], "detail": e["kind"]})
        rows.sort(key=lambda r: r["date"] or "", reverse=True)
        return rows[:limit]
