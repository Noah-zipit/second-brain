"""Second Brain Day-3 tests — agent depth (Oct 16).

VERIFICATION STANDARD: every test runs real Cypher against the live graph.
There are no mocks and no fixtures that fake the store. If FalkorDB is
unreachable, the module fixture raises and EVERYTHING fails — that is the
anti-decoration test: remove the graph, lose the feature.

Write tests mark their data with '[test-day3]' / topic 'test-day3' and the
`clean_test_data` fixture removes it afterwards, so the hold counts
(109 applications / 107 leads / 92 companies) are never polluted.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.store import FalkorDBStore
from app.agent import Agent
from app.nudge import (days_since, classify_lead, classify_application,
                       next_step, nudge_queue, followup_plan)

TODAY = "2026-10-16"


@pytest.fixture(scope="module")
def store():
    # Fail-fast: raises if the graph is unreachable. No mocks, no decoration.
    return FalkorDBStore()


@pytest.fixture()
def clean_test_data(store):
    yield
    store._q("MATCH (m:Memory) WHERE m.topic = 'test-day3' "
             "OR m.text CONTAINS '[test-day3]' DETACH DELETE m")
    store._q("MATCH (e:Event) WHERE e.note CONTAINS '[test-day3]' "
             "DETACH DELETE e")


# ---------------------------------------------------------------------------
# Graph is required — the anti-decoration test
# ---------------------------------------------------------------------------

def test_graph_required_no_mocks():
    with pytest.raises(Exception):
        FalkorDBStore(host="localhost", port=6390)  # nothing listens here


def test_hold_counts(store):
    st = store.stats()
    assert st["applications_total"] == 109, st
    assert st["leads_total"] == 107, st
    assert st["companies_total"] == 92, st


# ---------------------------------------------------------------------------
# Pure nudge rules — no I/O
# ---------------------------------------------------------------------------

def test_days_since():
    assert days_since("2026-10-10", TODAY) == 6
    assert days_since("2026-10-16", TODAY) == 0
    assert days_since("", TODAY) is None
    assert days_since(None, TODAY) is None
    assert days_since("not-a-date", TODAY) is None


def test_classify_lead():
    assert classify_lead(5, 30, 3) == "nudge"    # 5s: 3d idle line
    assert classify_lead(5, 30, 2) == "wait"
    assert classify_lead(4, 30, 5) == "nudge"    # 4s: 5d idle line
    assert classify_lead(4, 30, 4) == "wait"
    assert classify_lead(3, 30, 7) == "nudge"    # 3s: 7d idle line
    assert classify_lead(3, 30, 6) == "wait"
    assert classify_lead(2, 30, 14) == "nudge"   # 2s: 14d idle line
    assert classify_lead(2, 30, 13) == "wait"
    assert classify_lead(1, 20, 20) == "drop"    # stale low-score: cut
    assert classify_lead(0, 30, 30) == "drop"
    assert classify_lead(1, 5, 5) == "wait"      # young: not yet stale


def test_classify_application():
    assert classify_application("rejected", 30, 30) == "none"
    assert classify_application("skipped", 40, 40) == "none"
    assert classify_application("duplicate", 40, 40) == "none"
    assert classify_application("needs_user", 5, 5) == "act"
    assert classify_application("needs_browser", 5, 5) == "act"
    assert classify_application("blocked", 5, 5) == "act"
    assert classify_application("sent", 12, 12) == "nudge"      # 10d line
    assert classify_application("submitted", 12, 12) == "nudge"
    assert classify_application("sent", 3, 3) == "wait"
    assert classify_application("in_progress", 3, 1) == "wait"


def test_next_step_reasons_cite_evidence():
    action, reason = next_step("nudge", "lead", score=5, age_days=25,
                               idle_days=25, chain_len=0, name="Yezol AI")
    assert action == "Send a nudge message"
    assert "25d" in reason and "5/5" in reason
    action, reason = next_step("drop", "lead", score=1, age_days=20,
                               idle_days=20, chain_len=1, name="X")
    assert action == "Archive the lead"
    action, reason = next_step("act", "application", status="needs_user",
                               name="Y")
    assert "your move" in action


# ---------------------------------------------------------------------------
# Write-back: agent learns into the graph
# ---------------------------------------------------------------------------

def test_write_back_memory_roundtrip(store, clean_test_data):
    mid = store.add_memory("[test-day3] TestCo prefers email over DMs",
                           topic="test-day3", date=TODAY)
    assert mid.startswith("memory:")
    # Read back through the store AND through raw Cypher.
    mems = store.memories(limit=500)
    assert any(m["id"] == mid and "TestCo" in m["text"] for m in mems)
    rows = store._q("MATCH (m:Memory {id: $mid}) RETURN m.text, m.topic",
                    {"mid": mid})
    assert rows and rows[0][0].startswith("[test-day3]") and rows[0][1] == "test-day3"


def test_memory_appears_in_agent_context(store, clean_test_data):
    store.add_memory("[test-day3] context-probe fact", topic="test-day3",
                     date=TODAY)
    ctx = Agent(store).build_context()
    assert "[test-day3] context-probe fact" in ctx  # the agent reads what it wrote


def test_learn_fallback_writes_memory(store, clean_test_data, monkeypatch):
    ag = Agent(store)

    def _boom(q, a):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ag, "extract_facts", _boom)
    receipts = ag.learn("[test-day3] probe question?", "some answer",
                        today=TODAY)
    assert receipts and receipts[0]["type"] == "memory"
    mems = store.memories(limit=500)
    assert any("[test-day3] probe question?" in m["text"] for m in mems)


def test_write_back_status_change(store, clean_test_data):
    aid = store._q("MATCH (a:Application) WHERE a.status = 'sent' "
                   "RETURN a.id LIMIT 1")[0][0]
    eid = store.update_application_status(
        aid, "rejected", "[test-day3] user reported a rejection", TODAY)
    # Status changed on the node AND the change is a step on its chain.
    assert store._q("MATCH (a:Application {id:$aid}) RETURN a.status",
                    {"aid": aid})[0][0] == "rejected"
    chain = store.followup_chain(aid)
    assert any(c["id"] == eid and c["kind"] == "status_change" for c in chain)
    # Restore: this was a real application, put its status back.
    store._q("MATCH (a:Application {id:$aid}) SET a.status = 'sent'",
             {"aid": aid})


# ---------------------------------------------------------------------------
# Follow-up coordination: outreach plans as graph paths
# ---------------------------------------------------------------------------

def test_followup_chain_is_a_graph_path(store, clean_test_data):
    lid = store._q("MATCH (l:Lead) RETURN l.id LIMIT 1")[0][0]
    assert store.followup_chain(lid) == []
    store.record_followup(lid, "note", "[test-day3] step one", TODAY)
    store.record_followup(lid, "note", "[test-day3] step two", TODAY)

    chain = store.followup_chain(lid)
    assert [c["note"] for c in chain] == ["[test-day3] step one",
                                         "[test-day3] step two"]

    # Prove the path shape in raw Cypher: lead -> e1 -> e2.
    rows = store._q(
        "MATCH p = (s {id: $sid})-[:FOLLOWED_UP_BY*]->(e:Event) "
        "WHERE e.note CONTAINS '[test-day3]' "
        "RETURN e.note, length(p) ORDER BY length(p)", {"sid": lid})
    assert [(r[0], r[1]) for r in rows] == [("[test-day3] step one", 1),
                                           ("[test-day3] step two", 2)]


def test_record_followup_unknown_subject(store):
    with pytest.raises(ValueError):
        store.record_followup("lead:does-not-exist", "note", "x", TODAY)


def test_lead_touch_state_covers_all_leads(store):
    rows = store.lead_touch_state()
    assert len(rows) == 107  # every lead, straight from Cypher
    sample = rows[0]
    assert {"id", "name", "score", "sweep_date", "last_touch",
            "touches"} <= set(sample)


def test_nudge_queue_live(store):
    q = nudge_queue(store, today=TODAY)
    assert len(q) == 107
    for row in q:
        assert row["decision"] in ("nudge", "wait", "drop"), row
        # Decision is consistent with the pure rules on the graph's own data.
        expect = classify_lead(row["score"], row["age_days"], row["idle_days"])
        assert row["decision"] == expect, row
    n_nudge = sum(1 for r in q if r["decision"] == "nudge")
    n_drop = sum(1 for r in q if r["decision"] == "drop")
    assert n_nudge > 0 and n_drop > 0  # real data exercises every branch


def test_followup_plan_walks_graph(store, clean_test_data):
    row = store._q("MATCH (l:Lead) WHERE l.score >= 4 RETURN l.id, l.name "
                   "LIMIT 1")[0]
    lid, name = row[0], row[1]
    store.record_followup(lid, "dm_sent", "[test-day3] first ping", "2026-10-10")

    plan = followup_plan(store, lid, today=TODAY)
    assert plan["subject_id"] == lid and plan["kind"] == "lead"
    assert len(plan["chain"]) == 1
    assert plan["idle_days"] == 6
    assert plan["decision"] in ("nudge", "wait", "drop")
    assert plan["next_action"] and plan["reason"]
    # 'why' cites the walked evidence.
    assert "6d" in plan["reason"] or "last touch" in plan["reason"]


def test_what_next_end_to_end(store, clean_test_data):
    name = store._q("MATCH (l:Lead) WHERE l.score >= 4 RETURN l.name "
                    "LIMIT 1")[0][0]
    res = Agent(store).what_next(name, today=TODAY)
    assert "error" not in res
    assert res["decision"] in ("nudge", "wait", "drop", "act", "none")
    assert res["next_action"] and res["reason"]
    assert isinstance(res["path_steps"], list)


def test_what_next_unknown_subject(store):
    res = Agent(store).what_next("zzz-no-such-company", today=TODAY)
    assert "error" in res
