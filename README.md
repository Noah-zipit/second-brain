# Second Brain

A persistent-memory agent for a job hunt. Every application, lead, outreach email,
rejection, and follow-up becomes a node in a knowledge graph; the agent answers
memory questions ("who owes me a reply?", "which leads are going cold?") and
coordinates multi-step outreach.

**Hackathon:** Graph Hacks (WeMakeDevs × FalkorDB) — track **Agent Memory and Coordination**

## Why this wins its track

Remove the graph and the agent has amnesia. FalkorDB isn't decoration here — it's
the load-bearing memory layer. The anti-decoration test is the whole design: every
answer the agent gives is grounded in graph facts, and every fact it learns lands
back in the graph.

Unlike a throwaway entry, this is a tool that keeps working after the hackathon —
it runs against the real job-hunt pipeline (100+ applications, dozens of leads).

## Architecture

```
┌─────────┐     ┌──────────────┐     ┌──────────────┐
│  UI     │────▶│  FastAPI     │────▶│ AbstractStore│
│ (dash)  │     │  /api/*      │     │  (repo iface)│
└─────────┘     └──────────────┘     └──────┬───────┘
                                           │
                          ┌────────────────┼────────────────┐
                          │ Day 1 (Oct 14) │ Day 2 (Oct 15+)│
                          ▼                ▼                ▼
                    JsonStore (fallback)  FalkorDBStore (live)
```

- **Day 1 (Oct 14):** `JsonStore` — a real, working file-backed store in `data/store.json`,
  seeded from the real pipeline (`applied.json`, daily lead files).
- **Day 2 (Oct 15+):** `FalkorDBStore` implements the same `AbstractStore` interface
  against FalkorDB via Cypher and is now the live store — `main.py` tries it first and
  falls back to `JsonStore` when the graph is unreachable.

## Quickstart

```bash
# 1. Ingest the real pipeline data
python3 backend/app/ingest.py

# 2. Start the API (serves the dashboard at /)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 3. Open http://localhost:8000 — ask the brain anything
```

### FalkorDB (Day 2+, live store)

```bash
docker-compose up -d          # FalkorDB on :6379
# FalkorDBStore is the live store; JsonStore is the offline fallback.
# Override the connection with FALKORDB_HOST / FALKORDB_PORT or FALKORDB_URI.
```

## API

| Method | Path            | Description                              |
|--------|-----------------|------------------------------------------|
| GET    | /api/stats      | Pipeline counts (apps by status, leads)  |
| GET    | /api/followups  | Items needing action right now           |
| GET    | /api/timeline   | Chronological pipeline events            |
| POST   | /api/ask        | Ask the brain: `{"question": "..."}`     |
| GET    | /api/search     | Search companies, leads and events (`?q=...`) |
| GET    | /api/next       | What to do next about `?q=<name>`, and why |
| GET    | /api/nudge      | Lead nudge queue with per-lead decisions  |

## Repo pattern contract

`backend/app/store.py` exposes `AbstractStore` with exactly these methods:
`add_application`, `add_lead`, `add_event`, `get_followups`, `search`, `stats`, `timeline`.
Anything the API or agent needs goes through the interface — never around it.

## License

MIT — see LICENSE.
