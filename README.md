# Avatar Democracy

Local prototype of an AI-mediated legislative process: plain-language bill analysis, one citizen Avatar, a bill lifecycle, and a synthetic Proxy Chamber.

The proposal is written up in [ai_avatar_democracy_v1.1.md](ai_avatar_democracy_v1.1.md). What changed since v1.0 is in [CHANGES-v1.1.md](CHANGES-v1.1.md). [ai_avatar_democracy_v1.0.md](ai_avatar_democracy_v1.0.md) (rendered copy: [ai_avatar_democracy_v1.0.html](ai_avatar_democracy_v1.0.html)) is kept for history. Earlier drafts are `ai_proxy_democracy_v0.1.md` through `ai_proxy_democracy_v0.4.md`.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

## What runs

`python main.py` starts a FastAPI app. It serves the pages in `frontend/` and the API defined in `main.py`. Default address: [http://127.0.0.1:8000](http://127.0.0.1:8000).

| Page | What it does |
| --- | --- |
| `/` | Landing page |
| `/analyze` | Bill summary and red-flag analysis from pasted text, a URL, or a PDF |
| `/avatar` | Compass questionnaire, values statement, per-issue delegates, recorded positions |
| `/vote` | Analyze a bill, then cast the Avatar's per-section vote. Overrides update the page and the audit log |
| `/bills` | Draft, version, endorse, cool off, then a per-section chamber vote |
| `/chamber` | Synthetic population used as the Proxy Chamber |
| `/history` | Append-only audit log |

Bill analysis, the operator's Avatar vote, and chamber axis-tagging are the primary role. A checker role can cross-check that vote, compare a grounded delegate's vote with their recorded positions, and flag conflicts with bills that already passed. Every one of those calls goes through `backend/llm.py`: a system prompt and a user prompt in, text or JSON out. The provider and the model are chosen per role from config. The default provider is Anthropic for both roles, with `AVATAR_MODEL` (default `claude-sonnet-4-6`) on the primary role and `CHECKER_MODEL` (default `claude-haiku-4-5`) on the checker role, so an existing `.env` keeps today's models and today's API key. Only the Anthropic provider is implemented. The synthetic chamber then votes from compass alignment, with no further model call per citizen.

Those model calls need `ANTHROPIC_API_KEY` while the provider is Anthropic. Pages, the profile, the bill state machine, the population, and the audit log run without a key. `GET /api/health` reports whether the configured provider's credential is set. `POST /api/analyze` returns HTTP 500 with an explicit message when that credential is missing, or when `LLM_PROVIDER` names a provider that is not implemented.

## Setup

Python 3.10 or newer.

```bash
git clone https://github.com/spiry2s/avatar-democracy.git
cd avatar-democracy
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\activate`.

Edit `.env` and set a real key. Create one at [console.anthropic.com](https://console.anthropic.com).

```
ANTHROPIC_API_KEY=sk-ant-...
```

`requirements.txt` already includes the runtime stack (FastAPI, Uvicorn, Anthropic SDK, httpx, pypdf, BeautifulSoup, python-dotenv) and pytest.

## Run the app

From the repository root:

```bash
python main.py
```

The same app can be started with Uvicorn directly:

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

`Start Avatar Democracy.bat` starts `python main.py` on Windows and sets the cooling-off timers to `0`, so a new delegate and a frozen bill can advance immediately.

`HOST` defaults to `127.0.0.1`. `PORT` defaults to `8000`.

`sample_bill.txt` is a short fictional bill you can paste on `/analyze`. Analysis and voting stay unavailable until `ANTHROPIC_API_KEY` is set.

For a click-through of the bill lifecycle without waiting out the default week-long timers:

```
BILL_COOLING_OFF_SECONDS=0
DELEGATE_COOLING_OFF_SECONDS=0
EMERGENCY_COOLING_OFF_SECONDS=0
```

The process writes runtime state under `data/` (gitignored): `profile.json`, one JSON file per bill, `audit.jsonl`, and `population.json`.

## Run the tests

From the repository root. No API key and no network:

```bash
python -m pytest
```

The tests cover bill lifecycle transitions, chamber tallies, population distribution, multi-model divergence matching, delegate grounding, which sections are eligible for a drift check, and the LLM call sites. Provider tests inject a fake provider, so analysis, voting, axis tags, drift, and conflict checks run with no API key and no network. `tests/test_injection.py` feeds hostile bills and fabricated quotes through the same fakes: no network, and no claim that a prompt sandboxes the model.

## Configuration

Optional. Defaults live in `backend/config.py` and `backend/profile.py`. Copy `.env.example` and uncomment a line to override it.

| Variable | Default | Role |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | unset | Credential for the Anthropic provider. Required for analysis, Avatar votes, axis tagging, drift checks, and conflict checks while that provider is selected |
| `LLM_PROVIDER` | `anthropic` | Vendor for the primary role. Only `anthropic` is implemented |
| `CHECKER_PROVIDER` | `LLM_PROVIDER` | Vendor for the checker role. Unset uses the primary provider |
| `AVATAR_MODEL` | `claude-sonnet-4-6` | Model id sent to the primary provider. The default is an Anthropic id |
| `CHECKER_MODEL` | `claude-haiku-4-5` | Model id sent to the checker provider. The default is an Anthropic id |
| `MULTI_MODEL_CHECK` | `1` | `0` skips the second-model vote check |
| `DELEGATE_DRIFT_CHECK` | `1` | `0` skips drift checks |
| `CROSS_BILL_CONFLICT_CHECK` | `1` | `0` skips conflict checks on freeze |
| `POPULATION_SIZE` | `200` | Synthetic chamber size |
| `BILL_COOLING_OFF_SECONDS` | `604800` (7 days) | Wait between draft freeze and voting |
| `EMERGENCY_COOLING_OFF_SECONDS` | `3600` | Cooling-off for an emergency bill |
| `DELEGATE_COOLING_OFF_SECONDS` | `604800` (7 days) | Wait before a new delegate is followed. Revocation is immediate |
| `ENDORSE_THRESHOLD_ORDINARY` | `25` | Demo-scaled. The white paper sketches about 50,000 for a US-sized population |
| `ENDORSE_THRESHOLD_MAJOR` | `250` | Demo-scaled |
| `ENDORSE_THRESHOLD_CONSTITUTIONAL` | `2500` | Demo-scaled |
| `EMERGENCY_THRESHOLD_MULTIPLIER` | `2` | Emergency bills need a higher endorsement bar |
| `ENDORSEMENT_REVOCATION_SECONDS` | `86400` | Window during which an endorsement can be revoked |
| `SECTION_PASSAGE_RATIO` | `0.5` | `yes / (yes + no)` required for a contested section |
| `CHAMBER_VOTE_TAU` | `0.12` | Compass alignment below this abstains |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8000` | Bind port |

`MULTI_MODEL_CHECK`, `DELEGATE_DRIFT_CHECK`, and `CROSS_BILL_CONFLICT_CHECK` turn off when set to `0`, `false`, or an empty string.

## Project structure

```
avatar-democracy/
├── main.py                      # FastAPI app. This is the entry point.
├── requirements.txt
├── .env.example
├── sample_bill.txt              # Short fictional bill for /analyze
├── Start Avatar Democracy.bat   # Windows launcher; cooling-off timers set to 0
├── backend/
│   ├── llm.py                   # Provider interface and the Anthropic implementation
│   ├── grounding.py             # Quote checks, JSON shape, fences, truncation
│   ├── summarizer.py            # Bill-analysis prompt and primary-role model call
│   ├── extractors.py            # Pasted text, URL (HTML or PDF), uploaded PDF
│   ├── avatar.py                # Avatar vote, second-model divergence, drift check
│   ├── profile.py               # Compass, values, delegates, recorded positions
│   ├── questionnaire.py         # Six-axis seeding questionnaire
│   ├── bills.py                 # Draft, endorse, cool off, vote, pass or fail
│   ├── chamber.py               # Axis tags and deterministic population votes
│   ├── population.py            # Synthetic citizens
│   ├── conflict.py              # Cross-bill conflict check
│   ├── audit.py                 # Append-only JSONL log
│   └── config.py                # Environment-backed settings
├── frontend/                    # Pages and scripts served by main.py
│   ├── index.html
│   ├── analyze.html
│   ├── avatar.html
│   ├── vote.html
│   ├── bills.html
│   ├── chamber.html
│   ├── history.html
│   ├── style.css
│   ├── app.js
│   ├── avatar.js
│   ├── vote.js
│   ├── bills.js
│   ├── chamber.js
│   └── history.js
├── tests/
│   ├── test_bills.py
│   ├── test_chamber.py
│   ├── test_divergence.py
│   ├── test_drift.py
│   ├── test_grounding.py
│   ├── test_injection.py        # Fences, schema, quote checks, truncation, no network
│   └── test_llm.py              # Fake-provider coverage of every model call site
├── ai_avatar_democracy_v1.1.md  # Current white paper
├── CHANGES-v1.1.md              # Changes since v1.0
├── ai_avatar_democracy_v1.0.md  # Kept for history
├── ai_avatar_democracy_v1.0.html
├── ai_proxy_democracy_v0.1.md
├── ai_proxy_democracy_v0.2.md
├── ai_proxy_democracy_v0.3.md
└── ai_proxy_democracy_v0.4.md
```

The prompts the running app sends are the constants in `backend/summarizer.py`, `backend/avatar.py`, `backend/chamber.py`, and `backend/conflict.py`. The vendor SDK is only constructed in `backend/llm.py`.

## What the prototype implements

- Text extraction from a paste, a URL, or a PDF, then a model call (Anthropic by default) that must return JSON in a fixed shape: plain summary, sections, who benefits and who pays, red flags, comparable laws, and open questions. Section summaries, red flags, and beneficiary claims are grounded findings only when a verbatim quote occurs in the bill. The rest is returned and labeled ungrounded. See Limits.
- One local citizen profile: a six-axis compass from twelve Likert questions, a free-text values statement, and per-issue delegates. Adding a delegate waits out a cooling-off period before the Avatar follows it. Revoking a delegate is immediate. A delegate can carry recorded positions, which the vote prompt is told to cite.
- A per-section Avatar vote. The prompt's order is delegate, then values, then compass, otherwise abstain. When `MULTI_MODEL_CHECK` is on, the checker role flags sections where its position differs. When `DELEGATE_DRIFT_CHECK` is on, sections attributed to a grounded delegate can be flagged against that delegate's recorded text.
- A bill path of draft, new versions while still a draft, open for endorsement, freeze once a threshold is met, cooling-off, then voting. An emergency bill uses a higher threshold and a shorter cooling-off. On freeze the server can ask the checker role whether the bill conflicts with bills already marked passed.
- A chamber tally of the operator's Avatar plus a synthetic population. One model tags each section on six axes. Each synthetic citizen votes by compass alignment. A section where everyone abstains does not by itself defeat the bill. Every contested section has to pass.
- An append-only log of profile edits, endorsements, votes, and overrides in `data/audit.jsonl`.

Endorsements beyond the local operator are a demo counter on the bill (`simulated endorsements` in the Bills UI). The stored result of a chamber vote is the tally from the vote request. A later override on `/vote` is recorded in the audit log and shown on that page.

## Limits

The model can misread legal language, miss a provision, or describe one that is not in the text. Check the bill. By default the analysis and the checker are both Anthropic models, so disagreement between them is a weak signal for vendor-level or training-data bias. `backend/llm.py` is the seam a second vendor would plug into; that vendor is not in this build. The chamber is a sample of synthetic compasses, not a population of configured Avatars. This is a local single-user demo. It is not legal advice.

### What the bill path defends

Hostile bill text is the analyzer's main input. When the provider accepts data blocks, `backend/summarizer.py` does not put that text in the instruction string. The Anthropic provider sends the bill as a separate Messages API document block. The instructions stay in the system prompt and a text block. Providers that only have a single user string wrap the bill in `<<<BILL_DATA>>>` … `<<<END_BILL_DATA>>>`. A copy of those delimiters inside the bill is broken up, so the bill cannot close the fence early. A short instruction after the fence says the region is data.

The analyzer keeps a response only when it is one JSON object of a fixed shape: the required objects and lists, string fields, and the red-flag and severity enums. Unknown properties are discarded. A response that is not JSON, or that breaks the shape, is retried once and then rejected. `POST /api/analyze` returns HTTP 502 instead of storing it.

After the call, every section summary, red flag, beneficiary claim, plain summary, and title is checked for a quote. The match is not raw code-point identity. Both sides are put in NFC, then a small fold is applied: curly single and double quotes become straight quotes, en dashes, em dashes, and the Unicode minus become "-", non-breaking spaces become ordinary spaces, and the ligatures ﬁ, ﬂ, ﬀ, ﬃ, and ﬄ become their letters. A hit is then mapped back to the original characters, so section assignment and the truncated-tail check still use the bill's own offsets. NFKC is not used for that match. It is used only for the length floor. The floor is an allowlist: only letters, numbers, punctuation, and symbols count, and at least 20 of those characters must be letters or digits. That drops format characters (category Cf, including U+200B), combining marks (category M*, including U+0301 and U+034F), variation selectors (U+FE00–U+FE0F and U+E0100–U+E01EF), whitespace, the Hangul fillers U+115F, U+1160, U+3164, and U+FFA0, the Braille blank U+2800, and the Egyptian hieroglyph blanks U+13441 and U+13442. Those two are letters (category Lo) but they draw nothing, so they are on the blank list and do not count. A run of the same punctuation or symbol counts once, so dots or underscores cannot pad a short word. "shall", "The Secretary shall", and "shall" followed by any of those pads are not grounds. A section summary or a red flag is checked only inside the heading it cites. No matching heading, or more than one, and the claim is ungrounded. Character offsets are stored when the quote matches that span, and replaced when the model gave the right quote with the wrong offsets. The search uses only the characters that were sent to the model. A quote that appears only in a truncated tail is not marked grounded. A claim that fails is marked `grounded: false` and `withheld_from_avatar: true`. It stays in the JSON and is labeled ungrounded in the analyzer UI. It is left out of the Avatar prompt, the chamber axis-tag prompt, and the cross-bill conflict prompt.

The model-written title is held to that quote rule, and the title wording has to appear inside `title_quote` after the same normalization. An unrelated sentence from the bill cannot carry a different title into the prompts. When the quote fails, or the title is not inside it, the title stays on the analyze page and in the API, and the Avatar, chamber, and conflict prompts say "Bill under review" instead. The Avatar will not cast a yes or no on a section whose summary is ungrounded. A bill with no verbatim span of 40 visible characters has nothing that can ground a section, so the Avatar abstains. It also will not cast a yes or no on a section named by an ungrounded high-severity flag. A blank target or a target of "multiple" blocks every section. Those votes are abstentions with an explicit reason, and the withheld flag text is still shown. A long bill is split on section headings when that sends every character. The response says it was chunked. If the bill cannot be split, or a single section still does not fit, the tail is omitted, `truncation.blocking` is true, the UI shows an incomplete-analysis banner, and the Avatar abstains instead of voting on the shortened text.

### What that does not defend

None of this sandboxes the model. A document block and a fence are still model input. A model can follow orders written in the bill, including "ignore previous instructions", "omit Sec. 102", or "vote yes". The retry only catches malformed output. A bill that persuades the model to emit well-formed, dishonest JSON passes the schema.

Quote checks are a substring test, not a reading test. The allowlist stops "shall" and "The Secretary shall", including when they are padded with format characters, combining marks, variation selectors, Hangul fillers, the Braille blank, the Egyptian hieroglyph blanks, or a repeated punctuation mark. It does not stop visible padding. Homoglyphs, lookalike letters, and a run of ordinary letters still count, and a longer quote can still end before a negation or misstate a real sentence. A short Hebrew, Hindi, or Arabic quote that leans on combining marks can fall under the floor and be rejected. That is deliberate: marks do not count, and the consonant span has to be long enough on its own. Offsets use the model's span when that span is exactly the quote and lies in the allowed text, otherwise the first match there. They do not prove the quote is the right sentence. Missed provisions are not detected. Beneficiary claims have no section id, so they are checked against the analyzed bill text rather than one heading. Comparable laws and open questions are not quote-checked. They are not sent to the Avatar.

A hostile plain summary paired with a real `plain_summary_quote` still enters the Avatar prompt. The summary is a paraphrase, and the quote does not have to contain that paraphrase. That is the ride-along the title used to have. The title wording now has to appear inside `title_quote`, so an unrelated real quote cannot smuggle a different title. A title that is itself a long span planted in the bill can still be quoted and sent. When the title quote fails, the title is shown to the reader and kept out of the Avatar, chamber, and conflict prompts.

Chunking sends each chunk in its own call. A section quote has to fall inside the heading it names, so a sentence from another section does not ground it. Two headings with the same number, or an id that matches none, are refused rather than searched at large. Truncation is flagged, not repaired: the omitted tail is not analyzed, and a quote that appears only there is not marked grounded. If truncation is blocking, the Avatar abstains even when some quotes in the analyzed portion did ground.

The Avatar abstains when the rules above say to. That is a refusal to treat unverified text as a finding. It is not evidence the bill is fine, or that it is harmful. Chamber tags and conflict notes are still one model's judgment over whatever grounded text was left. A prompt cannot fully sandbox a model.

## License

MIT. See [LICENSE](LICENSE).
