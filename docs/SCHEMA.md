# Second Brain — Graph Schema

## Node types

### Person
A human in the pipeline — hiring manager, poster of a lead, referrer.
| property  | type   | description                          |
|-----------|--------|--------------------------------------|
| id        | string | `person:<slug>`                      |
| name      | string | display name                         |
| handle    | string | e.g. Threads/X handle (optional)     |
| role      | string | e.g. "hiring manager" (optional)     |

### Company
| property  | type   | description                          |
|-----------|--------|--------------------------------------|
| id        | string | `company:<slug>`                     |
| name      | string | canonical name                       |
| location  | string | optional                             |

### Application
One job application the user made.
| property  | type     | description                                          |
|-----------|----------|------------------------------------------------------|
| id        | string   | `app:<slug>`                                         |
| role      | string   | job title                                            |
| platform  | string   | wellfound / ycombinator / linkedin / company site…   |
| url       | string   | application or posting URL                           |
| date      | date     | when submitted/attempted                             |
| status    | string   | submitted / skipped / rejected / needs_user / blocked… |
| reason    | string   | why skipped/blocked (truth-gate notes)               |
| evidence  | string   | confirmation evidence text                           |

### Lead
A client/outreach prospect.
| property      | type     | description                              |
|---------------|----------|------------------------------------------|
| id            | string   | `lead:<slug>`                            |
| name          | string   | business or person name                  |
| source_platform | string | threads / x / freeindex / hn / linkedin… |
| source_url    | string   | original post URL                        |
| contact       | string   | how to reach them (DM handle, phone…)    |
| posted_date   | date     | when the post went up                    |
| sweep_date    | date     | when the lead hunter filed it            |
| fit           | string   | one-line fit note                        |
| score         | int      | 1–5                                      |
| draft         | string   | outreach draft text                      |
| status_note   | string   | e.g. "CLOSED", "emailed with CV"         |

### Email
An email sent or received that matters to the pipeline.
| property  | type     | description                 |
|-----------|----------|-----------------------------|
| id        | string   | `email:<slug>`              |
| subject   | string   |                             |
| to        | string   | recipient address           |
| date      | date     |                             |
| purpose   | string   | application / follow-up / outreach |

### Event
Anything that happened: rejection, reply, bounce, DM sent.
| property  | type     | description                              |
|-----------|----------|------------------------------------------|
| id        | string   | `event:<n>`                              |
| kind      | string   | rejected / replied / bounced / dm_sent / note / status_change |
| subject   | string   | human-readable summary                   |
| note      | string   | detail                                   |
| date      | string   | ISO `YYYY-MM-DD`                         |

### Memory (Day 3)
A durable fact the agent learned from an answered question — written back
to the graph so future answers remember it.
| property  | type   | description                          |
|-----------|--------|--------------------------------------|
| id        | string | `memory:<n>`                         |
| text      | string | the fact itself                      |
| topic     | string | short topic tag (e.g. `qa-log`)      |
| date      | string | ISO `YYYY-MM-DD` learned on          |

## Edge types

| edge           | from → to              | properties              | meaning                              |
|----------------|------------------------|-------------------------|--------------------------------------|
| APPLIED_TO     | Application → Company  | date                    | the application targets the company  |
| WORKS_AT       | Person → Company       | role                    | person is at the company             |
| CONTACTED_VIA  | Lead → channel string  | date, channel           | outreach sent (channel: dm/email)    |
| REJECTED_BY    | Application → Company  | date                    | company rejected the application     |
| FOLLOW_UP_DUE  | Application/Lead → due | due_date, reason        | something needs a nudge              |
| REFERRED_BY    | Application → Person   | note                    | came via this person                 |
| FOLLOWED_UP_BY | Subject → Event → Event… | date                  | outreach plan as a graph path: each step chains onto the previous tip, so walking the path replays the plan |

## Day-3 additions (Oct 16)

- `Memory` nodes: agent write-back — answered questions distill durable facts
  (via Groq JSON extraction, deterministic qa-log fallback) and persist them;
  `build_context` reads them back, closing the learn loop.
- `record_followup(subject_id, kind, note, date)`: creates an `Event` and
  chains it onto the subject's `FOLLOWED_UP_BY` tip
  (`subject -> e1 -> e2 -> …`).
- `followup_chain(subject_id)`: walks the path oldest-first (Cypher
  variable-length traversal ordered by path length).
- `lead_touch_state()`: per-lead `{last_touch, touches}` straight from Cypher
  — the "when" behind the nudge rules.
- `update_application_status(...)`: sets status AND records the change as a
  chain step (status changes are follow-ups too).
- `backend/app/nudge.py`: pure `days_since` / `classify_lead` /
  `classify_application` / `next_step` + graph-backed `nudge_queue` /
  `followup_plan`. The agent's `what_next` answers "what should I do next and
  why" from the walked path.

## Day-1 JSON mapping

`data/store.json` holds:
```json
{
  "companies":    [ {"id","name","location"} ],
  "applications": [ {"id","company_id","role","platform","url","date","status","reason","evidence"} ],
  "leads":        [ {"id","name","source_platform","source_url","contact","posted_date","sweep_date","fit","score","draft","status_note"} ],
  "events":       [ {"id","kind","subject","note","date"} ],
  "edges":        [ {"type","from","to","props"} ]
}
```

## Day-2 FalkorDB mapping

One label per node type (`Person`, `Company`, `Application`, `Lead`, `Email`, `Event`);
one relationship type per edge type. Node `id` property is unique (Cypher `MERGE` key).
Example per-method Cypher is sketched in `backend/app/store.py` (`FalkorDBStore`).
