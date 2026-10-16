# Second Brain — Build Plan

Track: **Agent Memory and Coordination** (Graph Hacks, Oct 15–18)

## Day 1 — Wed Oct 14 (scaffold, done by subagent)

- [x] Repo layout, README, LICENSE, .gitignore, .env.example, docker-compose.yml
- [x] `AbstractStore` repository interface + working `JsonStore`
- [x] `FalkorDBStore` stub with Cypher sketched per method (NOT implemented — event rule)
- [x] Schema design doc (`docs/SCHEMA.md`)
- [x] Ingestion of the real pipeline: `applied.json` (109 entries) + `daily-leads-*.md`
- [x] FastAPI: `/api/stats`, `/api/followups`, `/api/timeline`, `/api/ask`
- [x] Groq-powered Q&A agent over the store
- [x] Single-file dashboard UI (real data, no placeholders)
- [x] Public repo created: `Noah-zipit/second-brain` (push handled separately)

## Day 2 — Thu Oct 15 (event starts; FalkorDB day)

- [ ] `docker-compose up -d` — FalkorDB on :6379
- [ ] Implement `FalkorDBStore` per the Cypher sketches in `store.py`
- [ ] Migration: replay `data/store.json` into FalkorDB, verify counts match
- [ ] Flip `main.py` to `FalkorDBStore`; all API tests green against the graph

## Day 3 — Fri Oct 16 (agent depth)

- [x] Memory-aware agent: writes new facts back to the graph (learned entities/edges)
  - `Agent.ask` → `learn` → Groq JSON fact extraction → `Memory` nodes /
    follow-up `Event`s / status changes, all via `FalkorDBStore`;
    deterministic qa-log fallback when the LLM is down
- [x] Follow-up coordination: multi-step outreach plans as graph paths
  - `record_followup` chains `subject -> e1 -> e2 -> …` via `FOLLOWED_UP_BY`;
    `followup_plan` / `Agent.what_next` answer "what next and why" by walking
    the path; new API: `GET /api/next?q=…`, `GET /api/nudge`
- [x] Follow-up cadence logic (nudge rules per lead score/age)
  - `backend/app/nudge.py`: pure `days_since`/`classify_lead`/
    `classify_application`/`next_step` + Cypher-backed `nudge_queue`;
    17 live-graph tests, all green (tests fail if the graph is unreachable)
- [x] Counts held: 109 applications / 107 leads / 92 companies; live nudge
  queue on real data: 56 nudge / 25 drop / 26 wait

## Day 4 — Sat Oct 17 (demo + polish)

- [ ] Demo UI polish, mobile check
- [ ] Demo video (< 3 min) + architecture diagram
- [ ] Blog post (blog prize track)
- [ ] Full 20-point ship audit on the dashboard

## Day 5 — Sun Oct 18 (submit)

- [ ] Final QA pass, Devpost submission before cutoff
- [ ] Repo README final, repo pushed with clean history

## Anti-decoration test (the whole design)

Every feature must answer: *does removing the graph break it?*
If yes, it belongs. If no, it's cut.
