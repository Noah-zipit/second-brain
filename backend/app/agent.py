"""Groq-powered Q&A over the Second Brain store.

Answers are grounded in the store: the prompt carries a compact context
(stats + follow-ups + recent timeline) and the model reasons over it.
Credentials stay inside the user's stored Groq connector CLI — this file
never touches a key.

Deterministic fallback: if the LLM call fails, count-type questions are
answered directly from stats and clearly labeled as local answers.
"""

import subprocess
import sys
from pathlib import Path

from app.store import JsonStore

GROQ_CLI = Path.home() / "workspace/custom-connectors/custom.legacy-groq/bin/groq_chat.py"
MODEL = "openai/gpt-oss-120b"
STORE_PATH = Path.home() / "workspace/second-brain/data/store.json"

SYSTEM = (
    "You are Second Brain, a persistent-memory agent for a job hunt. "
    "Answer ONLY from the pipeline context below. Be concise and specific: "
    "name companies, roles, dates, and statuses. If the context doesn't contain "
    "the answer, say so plainly. Never invent facts."
)


def build_context(store: JsonStore) -> str:
    s = store.stats()
    lines = [
        f"Pipeline stats: {s['applications_total']} applications tracked, "
        f"{s['applications_submitted']} submitted, "
        f"{s['leads_total']} leads filed ({s['leads_contact_ready']} contact-ready), "
        f"{s['companies_total']} companies.",
        "Status breakdown: " + ", ".join(
            f"{k}={v}" for k, v in s["applications_by_status"].items()),
        "",
        "Follow-ups due:",
    ]
    for f in store.get_followups(limit=10):
        lines.append(f"- [{f['type']}] {f['title']} — {f['detail']} (action: {f['action']})")
    lines.append("")
    lines.append("Recent timeline:")
    for t in store.timeline(limit=10):
        lines.append(f"- {t['date']} [{t['kind']}] {t['title']} — {t['detail']}")
    return "\n".join(lines)


def ask_groq(question: str, context: str) -> str:
    prompt = f"{SYSTEM}\n\nPIPELINE CONTEXT:\n{context}\n\nQUESTION: {question}\nANSWER:"
    proc = subprocess.run(
        [sys.executable, str(GROQ_CLI), prompt, MODEL, "512"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "groq call failed")
    return proc.stdout.strip()


def local_fallback(question: str, store: JsonStore) -> str | None:
    """Deterministic answers for simple count questions, no LLM needed."""
    q = question.lower()
    s = store.stats()
    if "how many" in q and "application" in q and "submit" in q:
        return (f"[local answer] {s['applications_submitted']} applications submitted "
                f"out of {s['applications_total']} tracked.")
    if "how many" in q and "lead" in q:
        return (f"[local answer] {s['leads_total']} leads filed, "
                f"{s['leads_contact_ready']} contact-ready.")
    if "who owes me a reply" in q or "follow" in q:
        items = store.get_followups(limit=10)
        if not items:
            return "[local answer] Nothing is waiting on a reply right now."
        return "[local answer] Top follow-ups:\n" + "\n".join(
            f"- {i['title']} ({i['action']})" for i in items)
    return None


def answer(question: str, store_path: str | Path = STORE_PATH) -> str:
    store = JsonStore(store_path)
    context = build_context(store)
    try:
        return ask_groq(question, context)
    except Exception as exc:  # LLM unavailable — degrade honestly
        fb = local_fallback(question, store)
        if fb:
            return fb
        return (f"The language model is unreachable ({exc}). "
                "The pipeline data itself is intact — try a stats question instead.")


if __name__ == "__main__":
    print(answer(" ".join(sys.argv[1:]) or "how many applications have I submitted?"))
