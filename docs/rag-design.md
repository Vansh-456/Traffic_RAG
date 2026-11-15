# RAG design: every technique, why it exists, how to see it

Each technique below is visible in the **RAG internals** drawer under every answer, and each
can be switched off for an ablation (`copilot eval run --ablation ...`). Measured effects are in
[evaluation.md](evaluation.md).

## Ingestion side

### Structure-aware chunking + contextual headers (`ingestion/chunking.py`)
DTP advisories are short notices or long multi-section documents (Republic Day, G-20). Short ones
(< ~800 tokens) stay as **one chunk**, so a diversion list is never separated from its heading.
Long ones split on detected section headings (Restrictions, Diversions, Parking, Metro, Alternate
routes, Do's and Don'ts, plus the Hindi equivalents) and on numbered lists. Windows are 300–700
tokens with ~12 % overlap **inside a section only**, and every window repeats its heading.
Every chunk gets a header (`[Source | Issued by | Title | Published | Valid | Section]`) and the
header plus text is what gets embedded. *Why:* a chunk like "3. Vehicles will be diverted at Vijay
Chowk" is useless without knowing which event and date it belongs to.

### LLM metadata enrichment (`ingestion/enrichment.py`)
The fast model returns schema-validated JSON (event type, places, roads, validity window resolved
in IST against the publish date, severity, diversion points, summaries, 3–5 hypothetical
questions). Invalid JSON is re-prompted with the validation error. A rule-based fallback (keyword
event types, gazetteer mention spotting, regex dates) keeps ingestion working without a key.
*Why:* validity windows and places become hard filters at query time.

### Geocoding (`ingestion/geocoding.py`)
Order: alias index (English, Hindi, abbreviations: ITO, CP, DND, NH-48, लाल किला) → pg_trgm word
similarity → Nominatim bounded to the NCR bbox (cached, 1 req/s). Roads keep line geometry and
get a 150 m buffer. *Why:* Delhi place names are written many ways ("Dhaula Kuan", "Dhaulakuan",
"धौला कुआं").

### Cross-source dedup (`ingestion/dedup.py`)
Chunks from *different sources* within ±48 h cluster when SimHash differs by ≤ 3 bits or embedding
cosine ≥ 0.92. The canonical member is official > sensor > news, then the most complete. Retrieval
only sees canonical chunks; citations say "also reported by N sources". Same-source documents are
never clustered: DTP's templated press releases for different ranges are near-identical but are
different events (this was found on live data).

### Hypothetical-question index
Each chunk's enrichment questions are embedded separately (`question_embeddings`). User questions
look like questions, not advisories, so this index matches phrasing that dense chunk search misses.

## Query side

### Semantic cache (`rag/cache.py`)
Bucketed by intent + resolved places + time bucket (15-minute buckets for live queries), cosine
≥ 0.95, with TTLs of 5 min (live), 30 min (advisories) and 24 h (historical). *Why:* repeated
"ITO today" questions shouldn't cost five LLM calls, and a cached live answer must never outlive
the live data.

### Query understanding (`rag/query_understanding.py`, `rag/timewindow.py`)
The LLM extracts the *time phrase*; the rule engine resolves it against the IST clock ("kal" is
tomorrow with future tense and yesterday with past tense; "6 pm Friday" becomes a 17:00–19:30
window). Rewrites add an English, a Devanagari and a keyword query (multi-query retrieval).
Follow-ups inherit places from earlier turns.

### Agentic routing (`rag/router.py`)
The LLM may choose any tool, but per-intent defaults are always enforced (e.g. `cause_explanation`
→ live traffic + incidents + advisories + weather), capped at 4 calls with timeouts. *Why:* the
model can add context-specific tools, but it can't forget the live data a "why is it slow?"
answer depends on.

### Hybrid retrieval (`rag/retrieval/`)
1. **Hard filters**: validity-window overlap (published date ± 1 day when validity is unknown); a
   spatial `ST_DWithin` filter applied only if it leaves ≥ k results, relaxed ×2 once, then dropped.
2. **Dense**: top-30 per rewrite over HNSW, plus top-20 from the question index.
3. **Sparse**: `ts_rank_cd` over the `english` and `simple` tsvectors (the simple config keeps
   Hindi tokens), plus trigram similarity for place and road names.
4. **RRF** (k = 60) over all lists, then **cross-encoder rerank** of the top 40.
5. **Final score** = rerank × freshness × trust × proximity:
   - freshness `exp(-Δt/τ)`, where Δt = 0 while the validity window covers the reference time;
     τ = 6 h (live), 3 days (advisory/plan), disabled for historical questions;
   - trust: official 1.0, sensor 0.95, news 0.85;
   - proximity `1 + 0.3·(1 − d/r)` inside the radius. Outside it a **location-mismatch decay**
     `max(0.2, exp(−(d − r)/2r))` applies. This extension was added because, once the spatial filter
     is dropped, a question about Dwarka would otherwise be answered with an ITO advisory 18 km away.
6. **Relevance gate**: abstain when the best final score < `RELEVANCE_THRESHOLD` and no tool
   returned data.

### Generation (`rag/generation.py`)
Evidence (chunks + tool results with "as of" timestamps) is numbered `[1]..[n]`. The system
prompt requires a citation on every factual sentence, a lead-with-the-answer structure, hedged
ranked causes, timestamps for live data, the user's language, and an explicit
insufficient-evidence path pointing to DTP's official channels. Map features, cards and citation
metadata are built deterministically from the evidence, not parsed from model output.
Follow-ups arrive in a `<followups>` block that is stripped from the stream.

### Verification (`rag/verification.py`)
Every checkable sentence is judged against the evidence it cites (one batched fast-model call;
lexical overlap when no LLM is available). Unsupported sentences are removed, or marked
"(Unconfirmed)" if they carry a citation. Citations to nonexistent evidence always fail. More
than 30 % unsupported triggers one strict regeneration. The faithfulness score is stored with the
message and shown in admin.

## Prompt-injection hygiene
Retrieved text sits inside `<document>` / `<evidence>` tags and every prompt states that this
text is data. Tools take no free-form SQL. User input is length-limited and rate-limited.
