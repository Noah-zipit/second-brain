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
| kind      | string   | rejected / replied / bounced / dm_sent / note |
| subject   | string   | human-readable summary                   |
| note      | string   | detail                                   |
| date      | date     |                                          |

## Edge types

| edge           | from → to              | properties              | meaning                              |
|----------------|------------------------|-------------------------|--------------------------------------|
| APPLIED_TO     | Application → Company  | date                    | the application targets the company  |
| WORKS_AT       | Person → Company       | role                    | person is at the company             |
| CONTACTED_VIA  | Lead → channel string  | date, channel           | outreach sent (channel: dm/email)    |
| REJECTED_BY    | Application → Company  | date                    | company rejected the application     |
| FOLLOW_UP_DUE  | Application/Lead → due | due_date, reason        | something needs a nudge              |
| REFERRED_BY    | Application → Person   | note                    | came via this person                 |

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
