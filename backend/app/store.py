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

    # -- Day 3 (Oct 16): agent write-back + follow-up coordination -----------
    @abstractmethod
    def add_memory(self, text, topic, date):
        """Store a learned fact (Memory node). Returns the memory id."""

    @abstractmethod
    def memories(self, limit=20):
        """Learned facts, newest first."""

    @abstractmethod
    def record_followup(self, subject_id, kind, note, date):
        """Record a follow-up step: creates an Event and chains it onto the
        subject's FOLLOWED_UP_BY path (subject -> e1 -> e2 -> ...).
        Returns the event id."""

    @abstractmethod
    def followup_chain(self, subject_id):
        """Walk the subject's FOLLOWED_UP_BY chain, oldest step first."""

    @abstractmethod
    def lead_touch_state(self):
        """Per-lead graph state for nudge rules: id, name, score,
        sweep_date, last_touch (date or None), touches (count)."""

    @abstractmethod
    def update_application_status(self, app_id, new_status, note, date):
        """Set an application's status AND record the change as a
        follow-up step on its chain. Returns the event id."""


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
    # -- Day 3: write-back + follow-up coordination (JSON mirror) -----------
    def add_memory(self, text, topic, date):
        mems = self.db.setdefault("memories", [])
        mid = f"memory:{len(mems)}"
        mems.append({"id": mid, "text": text, "topic": topic, "date": date})
        return mid

    def memories(self, limit=20):
        mems = sorted(self.db.get("memories", []),
                      key=lambda m: (m.get("date") or "", m["id"]), reverse=True)
        return mems[:limit]

    def record_followup(self, subject_id, kind, note, date):
        known = ({a["id"] for a in self.db["applications"]}
                 | {l["id"] for l in self.db["leads"]}
                 | {c["id"] for c in self.db["companies"]})
        if subject_id not in known:
            raise ValueError(f"unknown subject id: {subject_id}")
        eid = self.add_event(kind, subject_id, note, date)
        # Chain tip: last event on the subject's path (itself if none yet).
        chain = self.followup_chain(subject_id)
        prev = chain[-1]["id"] if chain else subject_id
        self.db["edges"].append({
            "type": "FOLLOWED_UP_BY",
            "from": prev, "to": eid, "props": {"date": date},
        })
        return eid

    def followup_chain(self, subject_id):
        by_from = {}
        for e in self.db["edges"]:
            if e["type"] == "FOLLOWED_UP_BY":
                by_from[e["from"]] = e["to"]
        events = {e["id"]: e for e in self.db["events"]}
        chain, cur = [], by_from.get(subject_id)
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            if cur in events:
                chain.append(events[cur])
            cur = by_from.get(cur)
        return chain

    def lead_touch_state(self):
        rows = []
        for lead in self.db["leads"]:
            chain = self.followup_chain(lead["id"])
            rows.append({
                "id": lead["id"], "name": lead["name"],
                "score": lead["score"], "sweep_date": lead["sweep_date"],
                "last_touch": chain[-1]["date"] if chain else None,
                "touches": len(chain),
            })
        return rows

    def update_application_status(self, app_id, new_status, note, date):
        app = next((a for a in self.db["applications"] if a["id"] == app_id),
                   None)
        if app is None:
            raise ValueError(f"unknown application id: {app_id}")
        app["status"] = new_status
        return self.record_followup(app_id, "status_change", note, date)


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

    # -- Day 3: write-back + follow-up coordination (Cypher) ----------------
    # Graph model additions:
    #   (:Memory {id, text, topic, date})
    #   (subject)-[:FOLLOWED_UP_BY {date}]->(:Event), chained as
    #   subject -> e1 -> e2 -> e3, so each outreach plan IS a graph path.

    def add_memory(self, text, topic, date):
        n = self._q("MATCH (m:Memory) RETURN count(m)")[0][0]
        mid = f"memory:{n}"
        self._q("CREATE (m:Memory {id: $mid, text: $text, topic: $topic, "
                "date: $date})",
                {"mid": mid, "text": text, "topic": topic, "date": date})
        return mid

    def memories(self, limit=20):
        return [self._props(r[0]) for r in self._q(
            "MATCH (m:Memory) RETURN m ORDER BY m.date DESC, m.id DESC "
            "LIMIT $limit", {"limit": int(limit)})]

    def record_followup(self, subject_id, kind, note, date):
        n = self._q("MATCH (e:Event) RETURN count(e)")[0][0]
        eid = f"event:{n}"
        created = self._q(
            "MATCH (s) WHERE s.id = $sid "
            "CREATE (e:Event {id: $eid, kind: $kind, subject: $sid, "
            "note: $note, date: $date}) "
            "RETURN e.id",
            {"sid": subject_id, "eid": eid, "kind": kind,
             "note": note, "date": date})
        if not created:
            raise ValueError(f"unknown subject id: {subject_id}")
        # Chain tip: the event on this subject's path with no outgoing step.
        tip = self._q(
            "MATCH (s) WHERE s.id = $sid "
            "OPTIONAL MATCH (prev:Event) "
            "WHERE (s)-[:FOLLOWED_UP_BY*]->(prev) "
            "  AND NOT (prev)-[:FOLLOWED_UP_BY]->(:Event) "
            "RETURN prev.id ORDER BY prev.date DESC LIMIT 1",
            {"sid": subject_id})
        from_id = tip[0][0] if tip and tip[0][0] else subject_id
        self._q(
            "MATCH (f) WHERE f.id = $fid "
            "MATCH (e:Event {id: $eid}) "
            "CREATE (f)-[:FOLLOWED_UP_BY {date: $date}]->(e)",
            {"fid": from_id, "eid": eid, "date": date})
        return eid

    def followup_chain(self, subject_id):
        return [self._props(r[0]) for r in self._q(
            "MATCH p = (s {id: $sid})-[:FOLLOWED_UP_BY*]->(e:Event) "
            "WITH e, min(length(p)) AS depth "
            "RETURN e ORDER BY depth", {"sid": subject_id})]

    def lead_touch_state(self):
        out = []
        for r in self._q(
                "MATCH (l:Lead) "
                "OPTIONAL MATCH (l)-[:FOLLOWED_UP_BY*]->(e:Event) "
                "WITH l, max(e.date) AS last_touch, count(e) AS touches "
                "RETURN l.id, l.name, l.score, l.sweep_date, "
                "last_touch, touches"):
            out.append({
                "id": r[0], "name": r[1], "score": r[2],
                "sweep_date": r[3], "last_touch": r[4], "touches": r[5],
            })
        return out

    def update_application_status(self, app_id, new_status, note, date):
        rows = self._q("MATCH (a:Application {id: $aid}) "
                       "SET a.status = $ns RETURN a.id",
                       {"aid": app_id, "ns": new_status})
        if not rows:
            raise ValueError(f"unknown application id: {app_id}")
        return self.record_followup(app_id, "status_change", note, date)
