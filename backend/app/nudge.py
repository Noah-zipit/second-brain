"""Second Brain — follow-up cadence / nudge rules (Day 3, Oct 16).

Two layers:
  1. Pure functions (days_since, classify_lead, classify_application, next_step)
     — no I/O, fully unit-testable.
  2. nudge_queue / followup_plan — read live graph state through the store,
     then apply the pure rules. Anti-decoration: the "when" and "why" come
     from Cypher over the graph; removing the graph leaves nothing to walk.

Dates are ISO "YYYY-MM-DD" strings everywhere (same as store.json), so
day arithmetic is plain and honest.
"""

from __future__ import annotations

from datetime import date


# ---------------------------------------------------------------------------
# Pure functions — no I/O
# ---------------------------------------------------------------------------

def days_since(date_str: str | None, today: str | None = None) -> int | None:
    """Whole days from `date_str` to `today`. None for missing/unparseable."""
    if not date_str:
        return None
    today = today or date.today().isoformat()
    try:
        d0 = date.fromisoformat(str(date_str).strip()[:10])
        d1 = date.fromisoformat(str(today).strip()[:10])
    except ValueError:
        return None
    return (d1 - d0).days


def classify_lead(score: int, age_days: int | None, idle_days: int | None) -> str:
    """Nudge cadence for a lead. Returns 'nudge' | 'wait' | 'drop'.

    Rules (user-tunable constants at the top of each branch):
      - high-score leads get nudged sooner (score 5: 3d idle; 4: 5d; 3: 7d; 2: 14d)
      - low-score, long-stale leads get dropped (score<=1, age>=14d)
      - everything else waits
    """
    score = int(score or 0)
    idle = idle_days if idle_days is not None else 10 ** 9  # never touched -> overdue
    age = age_days if age_days is not None else 0

    if score >= 5 and idle >= 3:
        return "nudge"
    if score == 4 and idle >= 5:
        return "nudge"
    if score == 3 and idle >= 7:
        return "nudge"
    if score == 2 and idle >= 14:
        return "nudge"
    if score <= 1 and age >= 14:
        return "drop"
    return "wait"


def classify_application(status: str, age_days: int | None,
                         idle_days: int | None) -> str:
    """Nudge cadence for an application. Returns
    'nudge' | 'wait' | 'act' | 'none'.

    'act' means the next move is the user's own (blocked/needs input) —
    the agent surfaces it, it doesn't message anyone.
    'none' means terminal — rejected, skipped, duplicate.
    """
    st = (status or "").lower()
    idle = idle_days if idle_days is not None else 10 ** 9

    if st == "rejected":
        return "none"
    if st in ("skipped", "duplicate"):
        return "none"
    if st in ("needs_user", "needs_browser", "blocked"):
        return "act"
    if st.startswith("submitted") or st in ("sent", "in_progress"):
        return "nudge" if idle >= 10 else "wait"
    return "wait"


def next_step(decision: str, kind: str, *, score: int = 0, status: str = "",
              age_days: int | None = None, idle_days: int | None = None,
              chain_len: int = 0, name: str = "") -> tuple[str, str]:
    """Turn a classification into (action, reason) the agent can report.

    The reason always cites the graph-walk evidence: how long idle, how many
    prior steps in the chain, what score/status — so 'why' is auditable.
    """
    idle_txt = f"{idle_days}d" if idle_days is not None else "never"
    steps_txt = f"{chain_len} prior touch(es)" if chain_len else "no prior touches"

    if decision == "nudge" and kind == "lead":
        return (
            "Send a nudge message",
            f"{name}: score {score}/5, last touch {idle_txt} ago, {steps_txt}. "
            "High enough fit to justify another ping.",
        )
    if decision == "nudge":
        return (
            "Send a follow-up email",
            f"{name}: applied {age_days}d ago, last touch {idle_txt} ago, "
            f"{steps_txt}. Ten quiet days is the nudge line.",
        )
    if decision == "act":
        return (
            "Unblock it — your move",
            f"{name}: status is '{status}'. The next step needs you, "
            "not another message.",
        )
    if decision == "drop":
        return (
            "Archive the lead",
            f"{name}: score {score}/5, {age_days}d old, {steps_txt} with no "
            "traction. Cut it and spend the energy on 4s and 5s.",
        )
    if decision == "none":
        return ("Nothing to do", f"{name}: '{status}' is terminal.")
    return (
        "Wait",
        f"{name}: last touch {idle_txt} ago, {steps_txt}. Too soon to nudge.",
    )


# ---------------------------------------------------------------------------
# Graph-backed queries — pure rules applied to live Cypher results
# ---------------------------------------------------------------------------

def nudge_queue(store, today: str | None = None) -> list[dict]:
    """One row per lead: graph state + pure-rule decision.

    Anti-decoration check: this cannot run without the graph — the touch
    state (last FOLLOWED_UP_BY date, touch count) only exists in Cypher.
    """
    today = today or date.today().isoformat()
    rows = []
    for lead in store.lead_touch_state():
        age = days_since(lead["sweep_date"], today)
        idle = days_since(lead["last_touch"], today)
        decision = classify_lead(lead["score"], age, idle)
        rows.append({
            "id": lead["id"], "name": lead["name"],
            "score": int(lead["score"]), "sweep_date": lead["sweep_date"],
            "last_touch": lead["last_touch"], "touches": lead["touches"],
            "age_days": age, "idle_days": idle, "decision": decision,
        })
    # Most actionable first: nudges, then drops, then the waiting room.
    order = {"nudge": 0, "drop": 1, "wait": 2}
    rows.sort(key=lambda r: (order[r["decision"]], -(r["idle_days"] or 0)))
    return rows


def followup_plan(store, subject_id: str, today: str | None = None) -> dict:
    """Walk one subject's FOLLOWED_UP_BY chain and decide the next move.

    Returns the chain (the graph path), the classification, and the
    (action, reason) pair — i.e. 'what should I do next and why',
    answered by walking the graph.
    """
    today = today or date.today().isoformat()
    chain = store.followup_chain(subject_id)

    # Identify the subject node itself (any label) for its base facts.
    subj_rows = store._q("MATCH (s) WHERE s.id = $sid RETURN s LIMIT 1",
                         {"sid": subject_id})
    if not subj_rows:
        raise ValueError(f"unknown subject id: {subject_id}")
    subj = dict(subj_rows[0][0].properties)
    labels = list(subj_rows[0][0].labels)
    kind = "lead" if "Lead" in labels else "application"

    base_date = subj.get("sweep_date") or subj.get("date")
    last_touch = chain[-1]["date"] if chain else None
    age = days_since(base_date, today)
    idle = days_since(last_touch or base_date, today)

    if kind == "lead":
        decision = classify_lead(subj.get("score", 0), age, idle)
    else:
        decision = classify_application(subj.get("status", ""), age, idle)

    action, reason = next_step(
        decision, kind,
        score=int(subj.get("score", 0) or 0), status=subj.get("status", ""),
        age_days=age, idle_days=idle, chain_len=len(chain),
        name=subj.get("name") or f"{subj.get('company','')} — {subj.get('role','')}",
    )
    return {
        "subject_id": subject_id, "kind": kind,
        "name": subj.get("name") or subj.get("company"),
        "chain": chain, "age_days": age, "idle_days": idle,
        "decision": decision, "next_action": action, "reason": reason,
    }
