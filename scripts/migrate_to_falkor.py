"""Replay data/store.json into a fresh FalkorDB graph and verify parity.

Usage:
    .venv/bin/python scripts/migrate_to_falkor.py [--db path/to/store.json] [--uri ...]

Steps:
  1. Load the JSON store.
  2. Delete any existing `second_brain` graph (fresh migration).
  3. Replay companies / applications (+APPLIED_TO / REJECTED_BY edges) /
     leads / events through FalkorDBStore.
  4. Verify: node/edge counts and per-label breakdown match JsonStore exactly.
  5. Verify: stats(), get_followups(), search(), timeline() outputs are
     IDENTICAL between JsonStore and FalkorDBStore (behavioral parity).
  6. Run 3 spot Cypher queries that only the graph can answer natively.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.store import FalkorDBStore, JsonStore, slugify  # noqa: E402


def migrate(json_path: Path, **conn) -> tuple[JsonStore, FalkorDBStore]:
    jstore = JsonStore(json_path)
    fstore = FalkorDBStore(**conn)
    # Fresh graph (fail loudly if the delete doesn't work).
    fstore._db.execute_command("GRAPH.DELETE", FalkorDBStore.GRAPH_NAME)
    fstore = FalkorDBStore(**conn)

    db = json.loads(json_path.read_text())
    for c in db["companies"]:
        fstore._q("MERGE (c:Company {id: $cid}) SET c.name=$name, c.location=$loc",
                  {"cid": c["id"], "name": c["name"], "loc": c.get("location", "")})
    for a in db["applications"]:
        fstore.add_application(a["company"], a["role"], a["platform"], a["url"],
                               a["date"], a["status"], a.get("reason", ""),
                               a.get("evidence", ""))
    for lead in db["leads"]:
        fstore.add_lead(lead["name"], lead["source_platform"], lead["source_url"],
                        lead["contact"], lead["posted_date"], lead["sweep_date"],
                        lead["fit"], lead["score"], lead.get("draft", ""),
                        lead.get("status_note", ""))
    for e in db["events"]:
        fstore.add_event(e["kind"], e["subject"], e["note"], e["date"])
    return jstore, fstore


def graph_counts(fstore: FalkorDBStore) -> dict:
    counts = {}
    for r in fstore._q(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c"):
        counts[str(r[0])] = int(r[1])
    counts["edges"] = int(fstore._q("MATCH ()-[r]->() RETURN count(r)")[0][0])
    return counts


def verify(jstore: JsonStore, fstore: FalkorDBStore) -> None:
    jdb = jstore.db
    expected = {
        "Company": len(jdb["companies"]),
        "Application": len(jdb["applications"]),
        "Lead": len(jdb["leads"]),
        "Event": len(jdb["events"]),
        "edges": len(jdb["edges"]),
    }
    got = graph_counts(fstore)
    print("JSON counts   :", expected)
    print("Graph counts  :", got)
    assert got == expected, f"COUNT MISMATCH: {expected} != {got}"

    # Behavioral parity on every public method.
    js, fs = jstore.stats(), fstore.stats()
    assert js == fs, f"stats() mismatch:\n{js}\n{fs}"
    jf, ff = jstore.get_followups(), fstore.get_followups()
    assert jf == ff, "get_followups() mismatch"
    for q in ("shopify", "ai", "2026-10", "xyz-no-such-thing"):
        assert jstore.search(q) == fstore.search(q), f"search({q!r}) mismatch"
    jt, ft = jstore.timeline(), fstore.timeline()
    assert jt == ft, "timeline() mismatch"
    print("parity OK: stats/get_followups/search/timeline identical "
          f"(followups={len(ff)}, timeline={len(ft)})")


def spot_queries(fstore: FalkorDBStore) -> None:
    print("\n--- 3 spot Cypher queries (graph-native answers) ---")
    q1 = fstore._q(
        "MATCH (c:Company)<-[r:APPLIED_TO]-() "
        "RETURN c.name AS company, count(*) AS apps "
        "ORDER BY apps DESC LIMIT 5")
    print("1. Top companies by application count:")
    for r in q1:
        print(f"   {r[0]}: {r[1]}")

    q2 = fstore._q(
        "MATCH (a:Application) WHERE a.status IN ['sent','submitted'] "
        "OPTIONAL MATCH (e:Event) "
        "WHERE e.kind IN ['replied','rejected','dm_sent'] "
        "AND toLower(e.subject) CONTAINS toLower(a.company) "
        "WITH a, count(e) AS ev WHERE ev = 0 "
        "RETURN count(a) AS awaiting_reply")
    print(f"2. Applications still awaiting any reply/event: {q2[0][0]}")

    q3 = fstore._q(
        "MATCH (c:Company)<-[rej:REJECTED_BY]-(a:Application) "
        "WITH c, count(rej) AS rejections "
        "MATCH (c)<-[:APPLIED_TO]-(a2:Application) "
        "WITH c, rejections, count(a2) AS total "
        "WHERE total > rejections "
        "RETURN c.name AS company, rejections, total "
        "ORDER BY total DESC LIMIT 5")
    print("3. Companies that rejected me but got re-applied to anyway:")
    for r in q3:
        print(f"   {r[0]}: {r[1]} rejections / {r[2]} total applications")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "store.json"))
    ap.add_argument("--uri", default=os.environ.get("FALKORDB_URI", ""))
    args = ap.parse_args()
    conn = {"uri": args.uri} if args.uri else {}
    jstore, fstore = migrate(Path(args.db), **conn)
    verify(jstore, fstore)
    spot_queries(fstore)
    print("\nMIGRATION COMPLETE — graph is live and verified.")


if __name__ == "__main__":
    main()
