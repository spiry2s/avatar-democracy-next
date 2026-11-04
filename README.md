# Avatar Democracy

Local prototype of an AI-mediated legislative process: plain-language bill analysis, one citizen Avatar, a bill lifecycle, and a synthetic Proxy Chamber.

The proposal is written up in [ai_avatar_democracy_v1.0.md](ai_avatar_democracy_v1.0.md) (rendered copy: [ai_avatar_democracy_v1.0.html](ai_avatar_democracy_v1.0.html)). Earlier drafts are `ai_proxy_democracy_v0.1.md` through `ai_proxy_democracy_v0.4.md`.

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

The tests cover bill lifecycle transitions, chamber tallies, population distribution, multi-model divergence matching, delegate grounding, which sections are eligible for a drift check, and the LLM call sites. Provider tests inject a fake provider, so analysis, voting, axis tags, drift, and conflict checks run with no API key and no network.

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
│   └── test_llm.py              # Fake-provider coverage of every model call site
├── ai_avatar_democracy_v1.0.md  # White paper
├── ai_avatar_democracy_v1.0.html
├── ai_proxy_democracy_v0.1.md
├── ai_proxy_democracy_v0.2.md
├── ai_proxy_democracy_v0.3.md
└── ai_proxy_democracy_v0.4.md
```

The prompts the running app sends are the constants in `backend/summarizer.py`, `backend/avatar.py`, `backend/chamber.py`, and `backend/conflict.py`. The vendor SDK is only constructed in `backend/llm.py`.

## What the prototype implements

- Text extraction from a paste, a URL, or a PDF, then one model call (Anthropic by default) that returns JSON: plain summary, sections, who benefits and who pays, red flags, comparable laws, and open questions.
- One local citizen profile: a six-axis compass from twelve Likert questions, a free-text values statement, and per-issue delegates. Adding a delegate waits out a cooling-off period before the Avatar follows it. Revoking a delegate is immediate. A delegate can carry recorded positions, which the vote prompt is told to cite.
- A per-section Avatar vote. The prompt's order is delegate, then values, then compass, otherwise abstain. When `MULTI_MODEL_CHECK` is on, the checker role flags sections where its position differs. When `DELEGATE_DRIFT_CHECK` is on, sections attributed to a grounded delegate can be flagged against that delegate's recorded text.
- A bill path of draft, new versions while still a draft, open for endorsement, freeze once a threshold is met, cooling-off, then voting. An emergency bill uses a higher threshold and a shorter cooling-off. On freeze the server can ask the checker role whether the bill conflicts with bills already marked passed.
- A chamber tally of the operator's Avatar plus a synthetic population. One model tags each section on six axes. Each synthetic citizen votes by compass alignment. A section where everyone abstains does not by itself defeat the bill. Every contested section has to pass.
- An append-only log of profile edits, endorsements, votes, and overrides in `data/audit.jsonl`.

Endorsements beyond the local operator are a demo counter on the bill (`simulated endorsements` in the Bills UI). The stored result of a chamber vote is the tally from the vote request. A later override on `/vote` is recorded in the audit log and shown on that page.

## Limits

The model can misread legal language, miss a provision, or describe one that is not in the text. Check the bill. By default the analysis and the checker are both Anthropic models, so disagreement between them is a weak signal for vendor-level or training-data bias. `backend/llm.py` is the seam a second vendor would plug into; that vendor is not in this build. The chamber is a sample of synthetic compasses, not a population of configured Avatars. This is a local single-user demo. It is not legal advice.

## License

MIT. See [LICENSE](LICENSE).
