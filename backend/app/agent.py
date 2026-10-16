"""Second Brain agent — reads the graph, and learns back into it.

Day 1-2: the agent answered questions from a context built over the store.
Day 3 (Oct 16): the loop closes — every answered question also WRITES what
was learned back to the graph (Memory nodes, follow-up Events, status
changes), and "what should I do next" is answered by walking the
FOLLOWED_UP_BY paths in the graph. Removing the graph breaks both the
memory and the plans: that is the anti-decoration test.

Credentials stay inside the user's stored Groq connector CLI — this file
never touches a key.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from app.store import FalkorDBStore, JsonStore
from app.nudge import followup_plan

GROQ_CLI = Path.home() / "workspace/custom-connectors/custom.legacy-groq/bin/groq_chat.py"
MODEL = "openai/gpt-oss-120b"
STORE_PATH = Path.home() / "workspace/second-brain/data/store.json"

SYSTEM = (
    "You are Second Brain, a persistent-memory agent for a job hunt. "
    "Answer ONLY from the pipeline context below. Be concise and specific: "
    "name companies, roles, dates, and statuses. If the context doesn't contain "
    "the answer, say so plainly. Never invent facts."
)

EXTRACT_SYSTEM = (
    "From the Q&A below, extract durable facts worth remembering across "
    "sessions. Return ONLY a JSON array (no prose). Each item is one of:\n"
    '{"type":"memory","text":"...","topic":"short-topic"} — a general durable fact\n'
    '{"type":"event","kind":"rejected|replied|note|dm_sent|applied","subject":"company or lead name",'
    '"note":"...","date":"YYYY-MM-DD"} — something that happened\n'
    '{"type":"status","company":"...","role":"...","new_status":"...","note":"..."}'
    " — an application status change\n"
    f"Use today={date.today().isoformat()} for dates the exchange implies as now. "
    "Return [] if nothing is durable. Prefer fewer, higher-value facts."
)


def default_store():
    try:
        return FalkorDBStore()
    except Exception as e:  # FalkorDB down -> JSON fallback (read path only)
        print(f"[second-brain] FalkorDB unreachable ({e}); using JsonStore")
        return JsonStore(STORE_PATH)


class Agent:
    """Memory-aware agent over a store. Read + learn, both through the graph."""

    def __init__(self, store=None):
        self.store = store or default_store()

    # -- read ------------------------------------------------------------
    def build_context(self) -> str:
        store = self.store
        s = store.stats()
        lines = [
            f"Pipeline stats: {s['applications_total']} applications tracked, "
            f"{s['applications_submitted']} submitted, "
            f"{s['leads_total']} leads filed ({s['leads_contact_ready']} contact-ready), "
            f"{s['companies_total']} companies.",
            "Status breakdown: " + ", ".join(
                f"{k}={v}" for k, v in s["applications_by_status"].items()),
            "",
            "Learned facts (written back by earlier answers):",
        ]
        mems = store.memories(limit=10)
        lines.extend(f"- [{m['topic']}] {m['text']} ({m['date']})" for m in mems)
        if not mems:
            lines.append("- (none yet)")
        lines += ["", "Follow-ups due:"]
        for f in store.get_followups(limit=10):
            lines.append(f"- [{f['type']}] {f['title']} — {f['detail']} "
                         f"(action: {f['action']})")
        lines += ["", "Recent timeline:"]
        for t in store.timeline(limit=10):
            lines.append(f"- {t['date']} [{t['kind']}] {t['title']} — {t['detail']}")
        return "\n".join(lines)

    def ask_groq(self, question: str, context: str) -> str:
        prompt = f"{SYSTEM}\n\nPIPELINE CONTEXT:\n{context}\n\nQUESTION: {question}\nANSWER:"
        proc = subprocess.run(
            [sys.executable, str(GROQ_CLI), prompt, MODEL, "512"],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "groq call failed")
        return proc.stdout.strip()

    # -- learn (write-back) ----------------------------------------------
    def extract_facts(self, question: str, answer_text: str) -> list[dict]:
        """Distill durable facts from one Q&A exchange via the LLM."""
        prompt = (f"{EXTRACT_SYSTEM}\n\nQ: {question}\nA: {answer_text}\nFACTS:")
        proc = subprocess.run(
            [sys.executable, str(GROQ_CLI), prompt, MODEL, "256"],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "groq extract failed")
        return self._parse_facts(proc.stdout.strip())

    @staticmethod
    def _parse_facts(text: str) -> list[dict]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return []

    def _resolve_subject(self, name: str) -> dict | None:
        hits = self.store.search(name, limit=5)
        name_l = name.lower()
        for h in hits:
            hay = f"{h.get('company','')} {h.get('role','')} {h.get('name','')}".lower()
            if name_l in hay or hay and name_l.split()[0] in hay:
                return h
        return hits[0] if hits else None

    def write_back(self, facts: list[dict], today: str | None = None) -> list[dict]:
        """Persist extracted facts as graph entities/edges. Returns receipts."""
        today = today or date.today().isoformat()
        receipts = []
        for f in facts or []:
            ftype = f.get("type")
            try:
                if ftype == "memory":
                    mid = self.store.add_memory(
                        f.get("text", ""), f.get("topic", "general"), today)
                    receipts.append({"type": "memory", "id": mid})
                elif ftype == "event":
                    subj = self._resolve_subject(f.get("subject", ""))
                    if subj:
                        eid = self.store.record_followup(
                            subj["id"], f.get("kind", "note"),
                            f.get("note", ""), f.get("date", today))
                        receipts.append({"type": "event", "id": eid,
                                         "subject_id": subj["id"]})
                    else:
                        eid = self.store.add_event(
                            f.get("kind", "note"), f.get("subject", ""),
                            f.get("note", ""), f.get("date", today))
                        receipts.append({"type": "event", "id": eid,
                                         "subject_id": None})
                elif ftype == "status":
                    subj = self._resolve_subject(
                        f"{f.get('company','')} {f.get('role','')}".strip())
                    if subj and subj.get("kind") == "application":
                        eid = self.store.update_application_status(
                            subj["id"], f.get("new_status", ""),
                            f.get("note", ""), today)
                        receipts.append({"type": "status", "id": eid,
                                         "subject_id": subj["id"]})
                    else:
                        receipts.append({"type": "status", "id": None,
                                         "skipped": "no matching application"})
            except Exception as exc:  # learn must never break ask
                receipts.append({"type": ftype, "error": str(exc)})
        return receipts

    def learn(self, question: str, answer_text: str,
              today: str | None = None) -> list[dict]:
        """Write-back step: extract facts from the exchange, persist them."""
        today = today or date.today().isoformat()
        try:
            facts = self.extract_facts(question, answer_text)
        except Exception:
            # Deterministic fallback: the exchange itself becomes a memory.
            facts = [{"type": "memory",
                      "text": f"Asked: {question} — Answered: {answer_text[:300]}",
                      "topic": "qa-log"}]
        return self.write_back(facts, today)

    # -- ask: read + learn ------------------------------------------------
    def ask(self, question: str, learn: bool = True) -> dict:
        context = self.build_context()
        try:
            answer_text = self.ask_groq(question, context)
        except Exception as exc:  # LLM unavailable — degrade honestly
            fb = self.local_fallback(question)
            if fb:
                answer_text = fb
            else:
                answer_text = (f"The language model is unreachable ({exc}). "
                               "The pipeline data itself is intact — try a stats "
                               "question instead.")
        learned = self.learn(question, answer_text) if learn else []
        return {"answer": answer_text, "learned": learned}

    def local_fallback(self, question: str) -> str | None:
        """Deterministic answers for simple count questions, no LLM needed."""
        q = question.lower()
        s = self.store.stats()
        if "how many" in q and "application" in q and "submit" in q:
            return (f"[local answer] {s['applications_submitted']} applications submitted "
                    f"out of {s['applications_total']} tracked.")
        if "how many" in q and "lead" in q:
            return (f"[local answer] {s['leads_total']} leads filed, "
                    f"{s['leads_contact_ready']} contact-ready.")
        if "who owes me a reply" in q or "follow" in q:
            items = self.store.get_followups(limit=10)
            if not items:
                return "[local answer] Nothing is waiting on a reply right now."
            return "[local answer] Top follow-ups:\n" + "\n".join(
                f"- {i['title']} ({i['action']})" for i in items)
        return None

    # -- coordination: "what should I do next and why" --------------------
    def what_next(self, subject_query: str, today: str | None = None) -> dict:
        """Answer 'what should I do next and why' by walking the graph."""
        hits = self.store.search(subject_query, limit=5)
        if not hits:
            return {"subject_query": subject_query, "error":
                    "No matching application or lead in the graph."}
        subj = hits[0]
        plan = followup_plan(self.store, subj["id"], today)
        # Ground the answer in the walked path: name each prior step.
        steps = [f"{c['date']} — {c['kind']}: {c['note']}" for c in plan["chain"]]
        return {**plan, "path_steps": steps}


def answer(question: str, store=None) -> str:
    """CLI-compatible entry: returns just the answer text."""
    return Agent(store).ask(question)["answer"]


if __name__ == "__main__":
    print(answer(" ".join(sys.argv[1:]) or "how many applications have I submitted?"))
