# Avatar Democracy

Local prototype of an AI-mediated legislative process: plain-language bill analysis, one citizen Avatar, a bill lifecycle, and a synthetic Proxy Chamber.

The proposal is written up in [ai_avatar_democracy_v1.1.md](ai_avatar_democracy_v1.1.md). What changed since v1.0 is in [CHANGES-v1.1.md](CHANGES-v1.1.md). [ai_avatar_democracy_v1.0.md](ai_avatar_democracy_v1.0.md) (rendered copy: [ai_avatar_democracy_v1.0.html](ai_avatar_democracy_v1.0.html)) is kept for history. Earlier drafts are `ai_proxy_democracy_v0.1.md` through `ai_proxy_democracy_v0.4.md`.

The one-page project site is [docs/site](docs/site/README.md). It is static HTML and CSS, separate from this local app. Deploy steps for Vercel are in that README.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/spiry2s/avatar-democracy/actions/workflows/tests.yml/badge.svg)](https://github.com/spiry2s/avatar-democracy/actions/workflows/tests.yml)

## What runs

`python main.py` starts a FastAPI app. It serves the pages in `frontend/` and the API defined in `main.py`. Default address: [http://127.0.0.1:8000](http://127.0.0.1:8000).

| Page | What it does |
| --- | --- |
| `/` | Landing page |
| `/analyze` | Bill summary and red-flag analysis from pasted text, an official bill link, or a PDF |
| `/avatar` | Compass questionnaire, values statement, per-issue delegates, recorded positions |
| `/vote` | Analyze a bill, then cast the Avatar's per-section vote. Overrides update the page and the audit log |
| `/bills` | Draft, version, endorse, cool off, then a per-section chamber vote |
| `/chamber` | Synthetic population used as the Proxy Chamber |
| `/history` | Append-only audit log |

Bill analysis, the operator's Avatar vote, and chamber axis-tagging are the primary role. A checker role can cross-check that vote, compare a grounded delegate's vote with their recorded positions, and flag conflicts with bills that already passed. Every one of those calls goes through `backend/llm.py`: a system prompt and a user prompt in, text or JSON out. The provider and the model are chosen per role from config. The default provider is Anthropic for both roles, with `AVATAR_MODEL` (default `claude-sonnet-4-6`) on the primary role and `CHECKER_MODEL` (default `claude-haiku-4-5`) on the checker role, so an existing `.env` keeps today's models and today's API key. `MULTI_MODEL_CHECK` is a second Claude pass: another Anthropic model re-checking the vote. It is not an independent vendor. The synthetic chamber then votes from compass alignment, with no further model call per citizen.

An OpenAI-compatible provider (`LLM_PROVIDER=openai`, or `COMPARISON_PROVIDER=openai`) posts to `{OPENAI_BASE_URL}/chat/completions`. That covers OpenAI, OpenRouter, vLLM, and Ollama. It uses `OPENAI_MODEL`, `OPENAI_CHECKER_MODEL`, and `COMPARISON_MODEL`. It does not send `AVATAR_MODEL` or `CHECKER_MODEL`. When `COMPARISON_PROVIDER` is set, the same analysis prompt runs on that second model and the API returns a `model_comparison` report: sections only one model covered or summarized differently, red flags only one model raised, and beneficiary or loser claims that differ. Matching is deterministic (section id, then quote, then token overlap). No third model judges the pair. With `COMPARISON_PROVIDER` unset, analysis is unchanged and the response has no comparison report.

Those model calls need `ANTHROPIC_API_KEY` while the provider is Anthropic, and `OPENAI_API_KEY` while a role uses the OpenAI-compatible provider. Pages, the profile, the bill state machine, the population, and the audit log run without a key. `GET /api/health` reports whether the configured primary provider's credential is set, and, when a comparison is configured, that provider's id and model. It does not return API keys or other secrets. `POST /api/analyze` returns HTTP 500 with an explicit message when a required credential is missing, or when `LLM_PROVIDER` names a provider that is not implemented.

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

`HOST` defaults to `127.0.0.1`. `PORT` defaults to `8000`. If that port is already in use, the process prints an error naming `PORT` and exits before the startup banner. Binding anything other than `127.0.0.1`, `::1`, or `localhost` also exits before that banner unless `APP_ACCESS_TOKEN` is set to at least 16 characters. See Use on your phone.

`sample_bill.txt` is a short fictional bill you can paste on `/analyze`. Analysis and voting stay unavailable until `ANTHROPIC_API_KEY` is set.

For a click-through of the bill lifecycle without waiting out the default week-long timers:

```
BILL_COOLING_OFF_SECONDS=0
DELEGATE_COOLING_OFF_SECONDS=0
EMERGENCY_COOLING_OFF_SECONDS=0
```

The process writes runtime state under `data/` (gitignored): `profile.json`, one JSON file per bill, `audit.jsonl`, and `population.json`. Invite tokens, when you create them, are stored in that same directory. See Closed demo (invite tokens).

## Use on your phone

The pages fit a phone, and the app can be added to the home screen.

On iPhone, open the app in Safari, tap Share, then Add to Home Screen. On Android, open it in Chrome, open the browser menu, then Add to Home screen or Install app. The icon then opens the app on its own, without the browser toolbar.

The default stays bound to localhost (`127.0.0.1`). A phone cannot open that address. To reach this computer from a phone on the same Wi-Fi, opt in with a shared access token:

```bash
APP_ACCESS_TOKEN='paste-a-long-random-token' python main.py --host 0.0.0.0
```

Generate a token with `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`. `APP_ACCESS_TOKEN` must be at least 16 characters. `HOST=0.0.0.0` does the same thing as `--host`. If both are set, `--host` wins. The process refuses to start on any bind address other than `127.0.0.1`, `::1`, or `localhost` until that token is set. Then open `http://<this-computer-lan-ip>:8000` on the phone (`PORT` is still `8000` unless you change it). The phone asks for the token once and keeps it in local storage. Pages, the manifest, icons, and `sw.js` load without it. Every `/api` call sends it in the `X-Access-Token` header. Putting the token in the URL does not work.

Loopback does not ask for a token. It is protected by a Host allowlist and the same Origin check, not by "this request's Host matches its Origin". A browser can DNS-rebind a name it controls onto `127.0.0.1` and then send that name as both `Host` and `Origin`. Those requests are rejected. The allowlist is `127.0.0.1`, `localhost`, and `[::1]`, with the bound port or with no port. A LAN bind also allows the address you bound, each interface address when you bind `0.0.0.0` or `::`, and this machine's hostname. `ALLOWED_HOSTS` is an optional comma-separated list of extra names. `X-Forwarded-Host` is ignored. Anyone else on the same machine can still open the loopback address and use the app.

**Use this only on a trusted private Wi-Fi network. Never use it on public or shared Wi-Fi.** Anyone on that network who has the token can run analyses and votes, and that spends your configured API keys. Analysing an untrusted bill file still sends its text to the model providers. The token is a shared password for this process, not an account system, and it does not make a public network safe. Press Ctrl+C in the terminal to stop. It is still the local process on your machine.

A service worker caches only the static shell (pages, CSS, scripts, and icons) and only when the page is served over https or from localhost. It does not cache `/api` or any analysis or vote response, and it asks the network for HTML first so a new release shows up. When a new shell is waiting, the page offers a reload. Over plain `http://` on a LAN address the pages and the home-screen icon still work; the worker simply does not install.

## Closed demo (invite tokens)

`AVATAR_INVITE_MODE=1` closes the app for a small group of testers. Each tester gets a link. The provider API key stays in the server environment (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`). Testers never see it, and no page asks them to paste it.

```bash
python -m backend.invites create --label ada
AVATAR_INVITE_MODE=1 python main.py
```

`create` prints a link like `http://127.0.0.1:8000/?token=...` once. The token is `secrets.token_urlsafe(32)` (32 bytes from the operating system). The server stores only an HMAC-SHA256 of it, plus a label, `created_at`, `expires_at`, a revoked flag, budgets, and usage counters. The store is `invites.sqlite` under `AVATAR_INVITE_DATA_DIR` (default `data/`, gitignored), next to `invite-hmac.key`. A path that contains `..`, or a relative path that resolves outside the project through a symlink, is rejected.

Opening the link sets an `HttpOnly` cookie named `avatar_invite`, `SameSite=Strict`, and `Secure` when the request is https. The response is a redirect to the same path without `token`, so the secret is not left in the address bar. A missing, unknown, expired, or revoked token gets the same 403 page, with no reason. Static files under `/static/`, the manifest, and `sw.js` load without the cookie. Every other page and every `/api` route requires it. `python -m backend.invites` is the only admin interface. It is not mounted on the app. There is no invite route that creates, lists, or revokes tokens.

```bash
python -m backend.invites list
python -m backend.invites usage
python -m backend.invites revoke <id>
python -m backend.invites extend <id> --hours 48
python -m backend.invites expire <id>
```

`list` and `usage` show ids, labels, expiry, and counters, including daily bill usage for the current UTC day. They do not print token values. `revoke` keeps the row and rejects the cookie from then on. `expire` moves `expires_at` into the past. `extend` pushes expiry forward and does not clear a revoke.

Defaults are 30 bills in total (`AVATAR_INVITE_BILL_BUDGET`), 10 of those per UTC day (`AVATAR_INVITE_BILL_DAILY`), 60 model-call credits (`AVATAR_INVITE_LLM_BUDGET`), and 14 days (`AVATAR_INVITE_TTL_HOURS=336`). `--bills` overrides the lifetime bill cap for one invite. `--bills-daily` overrides that invite's daily cap. `--calls` overrides the model-call credit. The model-call budget stays a separate safety net. `0` is a real budget on the command line. A blank or non-positive environment value keeps the default.

The daily bill count is the UTC calendar day, `YYYY-MM-DD`. It resets at 00:00 UTC. `list` and `usage` show zero for today once that date has changed, even before the next analyze. The stored counter is cleared in the same transaction as the next bill reservation.

Those credits are reserved in the same gate as the shared per-IP spending limit, and only for the routes that limit already covers. `POST /api/analyze` — pasted text, an uploaded file, or an official bill URL — reserves one bill against both the lifetime cap and the UTC-day cap, plus one model-call credit, or two model-call credits when `COMPARISON_PROVIDER` is set (that request also runs the comparison pass). Both bill counters move in that one SQLite transaction, or neither does. `POST /api/avatar/vote`, `POST /api/bills/{id}/vote`, `POST /api/bills/{id}/endorse`, and `POST /api/bills/{id}/simulate-endorsements` each reserve one model-call credit and do not touch the bill counters. The reserve happens when the request is admitted, including when the handler later returns 400 or 404. A request rejected for Host, Origin, or content type is not charged. The per-IP limit is checked first; a hit there is `Too Many Requests` with `Retry-After` and does not spend invite credit. Past the lifetime bill cap, analyze returns 429 with `Invite bill budget exhausted`. Past the daily cap, while lifetime credit remains, it returns 429 with `Invite daily bill limit reached. It resets at UTC midnight.` Past the model-call credit, the route returns 429 with `Invite model-call budget exhausted`. If the lifetime cap is already used up, that is the response even when the daily cap is also used up.

Each admitted request is appended to `invite-usage.jsonl` in the data directory: invite id, label, route, timestamp, status, and, when a model call reported them, the call count and token total. The line does not include the raw token, the query string, or the API key. Counts in that log can be higher than the one credit the route reserved.

Invite mode does not replace the LAN controls. The Host allowlist, the Origin check, the content-type check, the content security policy, and the refusal to listen beyond loopback without `APP_ACCESS_TOKEN` all stay. On loopback, the invite cookie is the tester gate and `X-Access-Token` is not required. On any other bind, `/api` still requires that shared LAN token as well as the invite cookie, and the per-IP limit still applies. The browser field labelled "Enter access token" is the LAN token. It is not a provider key and it is not the invite.

One admitted vote, endorsement, or simulated endorsement can still perform more than one provider call (a checker pass, a drift check, axis tags, a schema retry, or analysis when a bill freezes). The credit stops further spending requests. It does not meter each of those inner calls. Set the budgets for the number of requests you want each tester to start, not for the number of provider round trips inside one request.

## Paste a bill link

On `/analyze`, next to the PDF upload, paste an `https` link. The same `url` field on `POST /api/analyze` fetches the document and runs it through the analysis pipeline used for an uploaded file. The field is optional. Pasted text and PDF upload are unchanged.

Only the hosts in `OFFICIAL_BILL_HOSTS` (`backend/bill_fetch.py`) are fetched. The match is the exact hostname. A subdomain, a suffix, a look-alike, or any other site is rejected with a message that tells you to download the file and upload it. There is no environment variable that adds a host.

| Publisher | Hosts |
| --- | --- |
| Sejm | `sejm.gov.pl`, `api.sejm.gov.pl`, `www.sejm.gov.pl`, `orka.sejm.gov.pl` |
| ISAP | `isap.sejm.gov.pl` |
| Stortinget | `stortinget.no`, `www.stortinget.no`, `data.stortinget.no` |
| Lovdata | `lovdata.no`, `www.lovdata.no` |
| Congress | `congress.gov`, `www.congress.gov`, `api.congress.gov` |

A pasted link is trimmed. If it has no scheme, `https://` is added. `http://` is still rejected. One trailing dot on the host is removed, so `www.congress.gov.` is the same host as `www.congress.gov`. A second trailing dot is rejected. The host is converted with IDNA and then has to match the table exactly, so a Cyrillic or other look-alike host does not match. Letters such as ł, ó, ø, and å in the path or the query are kept and sent as UTF-8 percent-encoding. The fragment is not sent. A percent-encoded hostname, userinfo, any port other than 443, and an IP address are rejected.

DNS is resolved in this process. Private, loopback, link-local, multicast, reserved, and IPv4-mapped IPv6 addresses are not connected to. The socket is opened to the address that passed that check, with the TLS name and the `Host` header set to the official hostname, so a DNS change between the check and the connect does not retarget the request. Redirects are followed by the app, at most three, and every hop is normalised and checked again. A redirect off the list is rejected. `HTTP_PROXY`, `HTTPS_PROXY`, and the other proxy variables are not used. A DNS lookup that does not return within the budget is abandoned and does not open a socket; the resolver thread itself cannot be cancelled.

The download stops at about 15 MB while the body is being read, not only when `Content-Length` says so. The connect and the read share a budget of about 10 seconds. HTML, PDF, and plain text are accepted. Anything else is rejected. `gzip`, `deflate`, and brotli (`br`, via the `brotli` package) are decoded under that same 15 MB cap on the decoded bytes. HTML is reduced to text: scripts, styles, and navigation are dropped, and the page renders that text as text. PDFs are read with pypdf (`pypdf` in `requirements.txt`), with a page cap and a character cap. A PDF whose text is empty or only a few characters is reported as having no text layer; upload a text file instead. HTML parsing time is not part of the 10 second socket budget.

The `url` field is on `POST /api/analyze`, which already spends a model call. On a non-loopback bind it requires `X-Access-Token`, the Host and Origin checks, `multipart/form-data`, and the shared spending-route rate limit. Loopback does not require the token. The same Host and Origin checks still apply.

### Add a source

1. Add the exact lowercase hostname to `OFFICIAL_BILL_HOSTS` in `backend/bill_fetch.py`. Do not add a wildcard, a suffix, or an IP address.
2. Confirm the site serves the bill over https on port 443 as HTML, PDF, or plain text.
3. Add that hostname to the acceptance list in `tests/test_bill_fetch.py`, and keep a rejection test for a subdomain or look-alike that is not on the list.
4. Mention the host in the table above. The analyzer page's "Supported sources" hint names the publishers; change it if the new host is a new publisher.

## Run the tests

From the repository root. No API key and no network:

```bash
python -m pytest
```

The tests cover bill lifecycle transitions, chamber tallies, population distribution, multi-model divergence matching, delegate grounding, which sections are eligible for a drift check, and the LLM call sites. Provider tests inject a fake provider, so analysis, voting, axis tags, drift, and conflict checks run with no API key and no network. `tests/test_injection.py` feeds hostile bills and fabricated quotes through the same fakes: no network, and no claim that a prompt sandboxes the model. That file also walks every Unicode code point. The sweep expects `unicodedata` 15.0.0 or newer (Python 3.12 or newer). CI runs it on Python 3.13.

## Continuous integration

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs the suite on every push and pull request. The job is Ubuntu, Python 3.13 via `actions/setup-python` with pip caching, then `python -m pip install -r requirements.txt` and `python -m pytest -q`. Pytest is already in `requirements.txt`; there is no separate test-requirements file. One step prints `sys.version` and `unicodedata.unidata_version`, and a test fails if that Unicode database is older than 15.0.0, so the full-code-point sweep is not a silent pass on an older database.

The workflow's only permission is `contents: read`. It does not read secrets. `ANTHROPIC_API_KEY` is set to an empty string, and the tests pass a fake provider, so the job does not call a model.

## Configuration

Optional. Defaults live in `backend/config.py` and `backend/profile.py`. Copy `.env.example` and uncomment a line to override it.

| Variable | Default | Role |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | unset | Credential for the Anthropic provider. Required for analysis, Avatar votes, axis tagging, drift checks, and conflict checks while that provider is selected |
| `LLM_PROVIDER` | `anthropic` | Vendor for the primary role. `anthropic` or `openai` |
| `CHECKER_PROVIDER` | `LLM_PROVIDER` | Vendor for the checker role (the second Claude pass, by default). Unset uses the primary provider |
| `AVATAR_MODEL` | `claude-sonnet-4-6` | Model id sent to Anthropic for the primary role. Not sent to the OpenAI-compatible provider |
| `CHECKER_MODEL` | `claude-haiku-4-5` | Model id sent to Anthropic for the checker role. Not sent to the OpenAI-compatible provider |
| `OPENAI_API_KEY` | unset | Credential for the OpenAI-compatible provider. Any non-empty placeholder works for a local server that does not check a key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Base URL. The client posts to `{base}/chat/completions` |
| `OPENAI_MODEL` | unset | Model id for the primary role when that role uses `openai`. Required in that case |
| `OPENAI_CHECKER_MODEL` | unset | Model id for the checker role when that role uses `openai`. Falls back to `OPENAI_MODEL`, never to `CHECKER_MODEL` |
| `OPENAI_TIMEOUT` | `600` | Seconds for one OpenAI-compatible HTTP call. Reasoning models need this; a DeepSeek bill took 78–94 seconds |
| `LLM_MAX_TOKENS` | `32000` | Output budget for a model call, including bill analysis. Reasoning tokens count against it |
| `COMPARISON_MAX_TOKENS` | `32000` | Output budget for the comparison analysis. Independent of `LLM_MAX_TOKENS` |
| `COMPARISON_PROVIDER` | unset | Second model family for bill analysis. Unset leaves analysis unchanged |
| `COMPARISON_MODEL` | unset | Model id for the comparison role. Required when `COMPARISON_PROVIDER` is set. Not taken from `AVATAR_MODEL` |
| `COMPARISON_VOTE_POLICY` | `abstain` | `abstain` (safe default) or `review` (demos only). See the warning below |
| `MULTI_MODEL_CHECK` | `1` | `0` skips the second Claude pass on the Avatar vote |
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
| `HOST` | `127.0.0.1` | Bind address. `python main.py --host` overrides it. Default stays localhost |
| `APP_ACCESS_TOKEN` | unset | Required when the bind address is not `127.0.0.1`, `::1`, or `localhost`. At least 16 characters. The browser sends it as `X-Access-Token`. Loopback does not use a token; Host and Origin checks still apply, and anyone else on the same machine can reach it. Invite mode does not remove this requirement |
| `AVATAR_INVITE_MODE` | unset | `1` (also `true`, `yes`, `on`) requires an invite cookie on every page and `/api` route except static files and the token landing. See Closed demo (invite tokens) |
| `AVATAR_INVITE_DATA_DIR` | `data` | Directory for `invites.sqlite`, `invite-hmac.key`, and `invite-usage.jsonl`. Keep it gitignored. A `..` segment is rejected |
| `AVATAR_INVITE_BILL_BUDGET` | `30` | Lifetime analyze requests each new invite may start. `create --bills` overrides it, including `0` |
| `AVATAR_INVITE_BILL_DAILY` | `10` | Analyze requests per invite on one UTC calendar day. Resets at 00:00 UTC. `create --bills-daily` overrides it, including `0` |
| `AVATAR_INVITE_LLM_BUDGET` | `60` | Model-call credits for each new invite, separate from the bill caps. `create --calls` overrides it |
| `AVATAR_INVITE_TTL_HOURS` | `336` | Default lifetime of a new invite (14 days) |
| `AVATAR_INVITE_BASE_URL` | `http://127.0.0.1:8000` | Base used when `create` prints the one-time link |
| `ALLOWED_HOSTS` | unset | Optional comma-separated host names added to the Host and Origin allowlist. `*` is not a wildcard |
| `PORT` | `8000` | Bind port |

`MULTI_MODEL_CHECK`, `DELEGATE_DRIFT_CHECK`, and `CROSS_BILL_CONFLICT_CHECK` turn off when set to `0`, `false`, or an empty string. `COMPARISON_VOTE_POLICY` accepts `abstain` or `review`. Anything else is treated as `abstain`.

> **Warning.** `COMPARISON_VOTE_POLICY=review` is for demos only. The default, `abstain`, is the safe setting. If the comparison is unavailable: (a) default `abstain` makes the Avatar abstain; (b) `review` with no high-severity primary flag on that section keeps the primary yes or no marked `needs_human_review=true`, which is excluded from the automatic yes/no tallies; (c) `review` with a high-severity primary flag on that section abstains. `review` does not always keep the primary position. Excluding a flagged yes or no shrinks the denominator, so anyone who can make the comparison model unavailable (a broken key, an outage, a blocked network, a timeout) for chosen sections could shift which votes count and flip a 50% passage result.

### A second model family

Leave the primary role on Anthropic and point comparison at another server. The Claude ids stay on the Anthropic calls.

OpenAI:

```
COMPARISON_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
COMPARISON_MODEL=gpt-4o
```

Ollama (open weights, OpenAI-compatible endpoint). The key is not checked; any non-empty value satisfies the missing-key check:

```
COMPARISON_PROVIDER=openai
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
COMPARISON_MODEL=llama3.1:8b
```

vLLM is the same shape, for example `OPENAI_BASE_URL=http://127.0.0.1:8001/v1` and `COMPARISON_MODEL=meta-llama/Llama-3.1-8B-Instruct`. OpenRouter uses `https://openrouter.ai/api/v1` and an OpenRouter key.

DeepSeek is another OpenAI-compatible server. Leave the primary role on Anthropic and point comparison at it:

```
COMPARISON_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com
COMPARISON_MODEL=deepseek-flash
COMPARISON_VOTE_POLICY=abstain
LLM_MAX_TOKENS=32000
COMPARISON_MAX_TOKENS=32000
OPENAI_TIMEOUT=600
```

`deepseek-flash` and `deepseek-v4-pro` are the model ids. `deepseek-flash` is a reasoning model: the tokens it spends thinking count toward `max_tokens`, and with the old hardcoded budget of 8000 the call stopped before any JSON (or stopped with a truncated object). Set `COMPARISON_MAX_TOKENS` (and `LLM_MAX_TOKENS` if the primary role is also a reasoning model) to at least 32000. Those calls are slow. One bill in a live run took about 2 to 5 minutes, longer when both models run and one of them is reasoning. The two analysis calls run at the same time, and a comparison failure keeps the primary analysis with `model_comparison.status` of `unavailable`. That is not agreement. If the comparison is unavailable: (a) default `abstain` makes the Avatar abstain; (b) `review` with no high-severity primary flag on that section keeps the primary yes or no marked `needs_human_review=true`, which is excluded from the automatic yes/no tallies; (c) `review` with a high-severity primary flag on that section abstains. `review` does not always keep the primary position. A failed check does not become an unflagged yes. `OPENAI_TIMEOUT` defaults to 600 seconds. A DeepSeek reasoning call on one bill took 78–94 seconds and can run past two minutes, so the old 120-second default was tight. The Anthropic primary call streams its reply, so the default `LLM_MAX_TOKENS` of 32000 is not rejected by the SDK's non-streaming 10-minute limit. The client posts to `https://api.deepseek.com/chat/completions`. A base URL of `https://api.deepseek.com/v1` is the same API with `/v1` in the path.

To run the primary role itself on that endpoint, set `LLM_PROVIDER=openai` and `OPENAI_MODEL`. Set `OPENAI_CHECKER_MODEL` if the checker role is also `openai`. Do not put a Claude id in those variables.

`COMPARISON_VOTE_POLICY=abstain` (the default) withholds the vote. If one model raises a high-severity red flag the other does not, they rate the severity differently, or their high-severity conclusions contradict (including "shall" against "shall not", or different amounts), the Avatar abstains on that section. The yes or no is not counted. The primary model's position is kept on the vote as `comparison_prior_position` so you can see what was withheld. A flag labeled blank or `multiple` holds every section. An ungrounded high-severity flag is not treated as the other model's agreement.

`COMPARISON_VOTE_POLICY=review` is for demos only. The default, `abstain`, is the safe setting. If the comparison is unavailable: (a) default `abstain` makes the Avatar abstain; (b) `review` with no high-severity primary flag on that section keeps the primary yes or no marked `needs_human_review=true`, which is excluded from the automatic yes/no tallies; (c) `review` with a high-severity primary flag on that section abstains. `review` does not always keep the primary position. On a high-severity disagreement, `review` keeps the primary yes or no and marks `needs_human_review`, and automatic yes/no tallies exclude that vote. Use `abstain` when a disagreement should stop the vote.

The comparison counts as independent only when the two models come from different vendors. The check compares the vendor, not the model family. Sonnet against Haiku is refused, and so is `gpt-4o` against `gpt-4o-mini`, a date suffix, or a `-latest` tag on the same vendor. Two OpenAI-compatible models still count as different vendors when the model id names the vendor (`meta-llama/...` against `qwen/...`).

### How amounts, dates, and versions are read

Amounts are an ordered sequence. `from 5 to 10` is not `from 10 to 5`.

Failing closed on an ambiguous amount is deliberate. There is no bill-locale switch. A model can write English thousands or a Polish decimal comma in the same sentence, and guessing a locale would create false agreements.

A comma after a leading 0 is a decimal mark, so `0,500` is `0.5`, not `500`. A single group of exactly three digits after a comma or a dot, with no other separator, is ambiguous: `1,500` and `1.500` can be 1500 or 1.5. An ambiguous amount on either side fails the match, and the Avatar abstains. These forms still resolve, because they can only be read one way: `1,500.00`, `1.500,00`, `1,500,000`, and `1.500.000`. A comma followed by one or two digits is a decimal comma, so `1,5` and `1,50` are `1.5`. An apostrophe thousands group equals the plain number by design: `1'500` is `1500`. That is not a space-grouped amount.

A space-grouped number and an ungrouped number are never the same amount. `12 500 zł` is not `12500 zł`, and the Avatar abstains. That is deliberate: a space can be a thousands separator or a break between two numbers, and joining them would agree when the amounts differ. Two space-grouped numbers match only when the digit groups are exactly the same. `12 500 zł` matches `12 500 zł`. A normal space, a non-breaking space, a thin space, a narrow no-break space, a line break, and a tab are the same gap, so `12\n500 zł` matches `12 500 zł` and still does not match `12500 zł`. A zero-width space is not a gap. `12\u200b500` stays two numbers, so it matches neither `12 500` nor `12500`. The word in front does not change this. `art. 12 500 zł`, `kap.`, `rozdz.`, `no.`, `par.`, `Abs.`, `tit.`, `§`, `ledd`, and a hyphenated range such as `art. 12-14 500 zł` all abstain against the joined digits.

The wording around an amount keeps every sign that is not sentence punctuation. A period, comma, colon, semicolon, exclamation mark, question mark, quote, or a hyphen between words does not by itself make two claims differ. Any other sign does, so `§ 1 000 kr` is not `1 000 kr`, and the same is true of `§§`, `№`, `#`, and a parenthesis.

Scale words do not join groups and do not multiply the number. `mln`, `mld`, `tys.`, `million`, and `milliard` stay ordinary words, so `1 500 mln` is not read as 1.5 billion and does not match `1500`. That disagreement is deliberate.

Lists stay in order. `sections 3, 4 and 5` is three numbers, not 3.4 and not 345. Dates are their own tokens: `2026-11-04`, a day-month-year date whose day is greater than 12 such as `15.11.2026`, and a named month such as `4 listopada 2026` or `4. november 2026`. A dotted date with both parts at most 12, such as `04.11.2026`, is ambiguous and fails the match. Versions such as `v1.2.3` are not three plain numbers. A different sequence of any of these is a disagreement.

Agreement is checked by comparing the wording, not the meaning. The same words in a different order of numbers disagree ("from 5 to 10" is not "from 10 to 5"). A paraphrase a person would call the same claim still disagrees, so the Avatar is expected to abstain more often than a careful reader would. A review of cases that should have agreed found that about 5 of 7 abstained for that reason.

The report is `model_comparison` on `POST /api/analyze` (and on the stored bill analysis). The analyzer page shows it next to the grounding banner. The vote page shows an abstention or a human-review flag per section, alongside any quote-grounding abstention. Both models go through the same fence or document block, the same schema retry, and the same verbatim-quote check. A quote marked `grounded: false` is displayed and is not used as a match.

Two models can still share biases. They can be trained on the same public text, refuse the same topics, or miss the same provision. Disagreement is evidence that the wording did not match. Agreement is not evidence that the bill says what they say, and it is not a judgment that two paraphrases mean the same thing.

## Project structure

```
avatar-democracy/
├── .github/workflows/tests.yml  # Push and pull_request suite on Python 3.13
├── main.py                      # FastAPI app. This is the entry point.
├── requirements.txt
├── .env.example
├── sample_bill.txt              # Short fictional bill for /analyze
├── Start Avatar Democracy.bat   # Windows launcher; cooling-off timers set to 0
├── backend/
│   ├── llm.py                   # Provider interface, Anthropic, OpenAI-compatible
│   ├── grounding.py             # Quote checks, JSON shape, fences, truncation
│   ├── comparison.py            # Second-family analysis, disagreement report, vote policy
│   ├── summarizer.py            # Bill-analysis prompt and primary-role model call
│   ├── bill_fetch.py            # Official-host allowlist and pinned https fetch
│   ├── extractors.py            # Pasted text, official URL (HTML or PDF), uploaded PDF
│   ├── avatar.py                # Avatar vote, second Claude pass, drift check
│   ├── profile.py               # Compass, values, delegates, recorded positions
│   ├── questionnaire.py         # Six-axis seeding questionnaire
│   ├── bills.py                 # Draft, endorse, cool off, vote, pass or fail
│   ├── chamber.py               # Axis tags and deterministic population votes
│   ├── population.py            # Synthetic citizens
│   ├── conflict.py              # Cross-bill conflict check
│   ├── audit.py                 # Append-only JSONL log
│   ├── config.py                # Environment-backed settings
│   └── invites.py               # Invite tokens, budgets, usage log, admin CLI
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
│   ├── history.js
│   ├── api.js                 # Shared /api fetch. Sends the stored access token
│   ├── pwa.js                 # Registers the worker and offers a reload
│   ├── sw.js                  # Caches the shell only; never /api
│   ├── manifest.webmanifest
│   └── icons/                 # Home-screen mark, from the same bars as docs/site/favicon.svg
│       ├── icon.svg
│       ├── icon-192.png
│       ├── icon-512.png
│       └── icon-maskable-512.png
├── docs/
│   └── site/                    # Static project page, not the local app. See docs/site/README.md
├── tests/
│   ├── test_bills.py
│   ├── test_chamber.py
│   ├── test_divergence.py
│   ├── test_drift.py
│   ├── test_grounding.py
│   ├── test_injection.py        # Fences, schema, quote checks, truncation, no network
│   ├── test_llm.py              # Fake-provider coverage of every model call site
│   ├── test_comparison.py       # OpenAI-compatible HTTP shape, disagreement, vote policy
│   ├── test_mobile.py           # Manifest, icons, viewport, service worker, bind host
│   ├── test_lan_access.py       # LAN token, origin check, content type, banner
│   ├── test_bill_fetch.py       # Official bill links, SSRF checks, no live network
│   └── test_invites.py          # Invite cookies, budgets, CLI, no key leak
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

- Text extraction from a paste, an official bill link, or a PDF, then a model call (Anthropic by default) that must return JSON in a fixed shape: plain summary, sections, who benefits and who pays, red flags, comparable laws, and open questions. Section summaries, red flags, and beneficiary claims are grounded findings only when a verbatim quote occurs in the bill. The rest is returned and labeled ungrounded. A configured comparison provider runs that same prompt, schema, and quote check on a second model, and the page shows where they disagree. See Limits.
- One local citizen profile: a six-axis compass from twelve Likert questions, a free-text values statement, and per-issue delegates. Adding a delegate waits out a cooling-off period before the Avatar follows it. Revoking a delegate is immediate. A delegate can carry recorded positions, which the vote prompt is told to cite.
- A per-section Avatar vote. The prompt's order is delegate, then values, then compass, otherwise abstain. When `MULTI_MODEL_CHECK` is on, a second Claude pass flags sections where its position differs. When a comparison provider is configured and the analyses disagree on a high-severity red flag, the Avatar abstains on that section (or flags it for human review, if `COMPARISON_VOTE_POLICY=review`). When `DELEGATE_DRIFT_CHECK` is on, sections attributed to a grounded delegate can be flagged against that delegate's recorded text.
- A bill path of draft, new versions while still a draft, open for endorsement, freeze once a threshold is met, cooling-off, then voting. An emergency bill uses a higher threshold and a shorter cooling-off. On freeze the server can ask the checker role whether the bill conflicts with bills already marked passed.
- A chamber tally of the operator's Avatar plus a synthetic population. One model tags each section on six axes. Each synthetic citizen votes by compass alignment. A section where everyone abstains does not by itself defeat the bill. Every contested section has to pass.
- An append-only log of profile edits, endorsements, votes, and overrides in `data/audit.jsonl`.

Endorsements beyond the local operator are a demo counter on the bill (`simulated endorsements` in the Bills UI). The stored result of a chamber vote is the tally from the vote request. A later override on `/vote` is recorded in the audit log and shown on that page.

## Limits

The model can misread legal language, miss a provision, or describe one that is not in the text. Check the bill. A pasted link is fetched only from the official hosts under Paste a bill link. Any other address, including a private address that an allowed name resolves to, is refused. The vote checker is a second Claude pass, so disagreement there is a weak signal for vendor-level bias. A comparison provider runs the same analysis on another family, which catches some of those misses. It does not cancel shared bias: two models can be trained on the same public text, refuse the same topics, or repeat the same mistake about a statute. Agreement between them is not proof. The chamber is a sample of synthetic compasses, not a population of configured Avatars. This is a local single-user demo. It is not legal advice.

### What the bill path defends

Hostile bill text is the analyzer's main input. When the provider accepts data blocks, `backend/summarizer.py` does not put that text in the instruction string. The Anthropic provider sends the bill as a separate Messages API document block. The instructions stay in the system prompt and a text block. Providers that only have a single user string wrap the bill in `<<<BILL_DATA>>>` … `<<<END_BILL_DATA>>>`. A copy of those delimiters inside the bill is broken up, so the bill cannot close the fence early. A short instruction after the fence says the region is data.

The analyzer keeps a response only when it is one JSON object of a fixed shape: the required objects and lists, string fields, and the red-flag and severity enums. Unknown properties are discarded. A response that is not JSON, or that breaks the shape, is retried once and then rejected. A reply that stopped because it hit the output budget (`finish_reason` `length`, Anthropic `stop_reason` `max_tokens`, or empty text) is not parsed and is not retried as a schema error. `POST /api/analyze` returns HTTP 502 with that truncation message instead of storing it. The output budget is `LLM_MAX_TOKENS` / `COMPARISON_MAX_TOKENS` (default 32000). When the comparison call fails for any other reason, the primary analysis is still returned and the comparison is marked `unavailable`.

After the call, every section summary, red flag, beneficiary claim, plain summary, and title is checked for a quote. The match is not raw code-point identity. Both sides are put in NFC, then a small fold is applied: curly single and double quotes become straight quotes, en dashes, em dashes, and the Unicode minus become "-", non-breaking spaces become ordinary spaces, and the ligatures ﬁ, ﬂ, ﬀ, ﬃ, ﬄ, ﬅ, and ﬆ become their letters (ﬅ and ﬆ become "st"). Ĳ and ĳ become "IJ" and "ij". Œ and œ are not folded: French treats œ as a letter, and Unicode does not decompose it, while Norwegian "oe" is a romanization of ø, a different letter. Runs of whitespace (spaces, tabs, newlines, and other Unicode spaces in category Zs) collapse to one space for this match only. A soft hyphen is dropped. An ASCII hyphen is joined only when it ends the line and the next line continues a lowercase syllable: the left piece is at least four lowercase letters and the right piece is two to four. That is the PDF split in "regional-\nnego", including indentation on the next line. A space before the break ("word- \nword"), "cat-\ndog", "state-\nowned", a capitalized fragment, and an em dash are not joined. A zero-width space is not whitespace and is not removed. A hit is then mapped back to the original characters, including a newline or hyphen that was folded away, so section assignment and the truncated-tail check still use the bill's own offsets. NFKC is not used for that match. It is used only for the length floor. The floor is an allowlist: only letters, numbers, punctuation, and symbols count, and at least 20 of those characters must be letters or digits. That drops format characters (category Cf, including U+200B), combining marks (category M*, including U+0301 and U+034F), variation selectors (U+FE00–U+FE0F and U+E0100–U+E01EF), whitespace, the Hangul fillers U+115F, U+1160, U+3164, and U+FFA0, the Braille blank U+2800, and the Egyptian hieroglyph blanks U+13441 and U+13442. Those two are letters (category Lo) but they draw nothing, so they are on the blank list and do not count. Punctuation whose Unicode name matches *FILLER does not count either, including the numbered FILLER-1 and FILLER-2 forms: U+A8F9, U+10AF6, U+1144E, U+11945, U+11C44, U+11C45, and U+11F48. A run of the same punctuation or symbol counts once, so dots or underscores cannot pad a short word. "shall", "The Secretary shall", and "shall" followed by any of those pads are not grounds. A section summary or a red flag is checked only inside the heading it cites. Headings are `Sec.` / `Section` / `Title`, a Norwegian `§` or `§§` line (`§ 1`, `§ 1a`), and a Polish `Art.` / `ust.` / `pkt.` line (`Art. 1`, `Art. 12a`, `ust. 2`, `pkt 3`). A bare `1.` or `1)` line counts as ust. or pkt. only inside an `Art.` span, so a Norwegian numbered list and a wrapped lowercase "art. 11" cross-reference do not open a section. A singular `§` line is a heading when it has a period (`§ 6.`), a capitalized title (`§ 1 Formål`), or the marker alone ends the line. A bare `§ N` does not open a section when the line above does not end a sentence and the next line continues in lowercase. `§§` on its own line (`§§ 3`) is a cross-reference, not a heading. `§§ 4 til 6.` still is. A quote may skip one editorial journal citation when the parenthetical is only `(Dz. U.` or `(Dz.U.`, an optional `z YYYY r.`, `poz.` and/or `nr` number lists joined by a comma, ` i `, or ` oraz `, and an optional `, z późn. zm.`. Anything else inside the parentheses, including `nie`, an amount, or `art.` / `ust.` / `pkt`, is not skipped, and a second citation in the same quote is not skipped either. `(Dz. Urz. ...)`, `(M.P. ...)`, and `(t.j. Dz. U. ...)` are not skipped. A quote that includes a journal citation still has to match that citation. A title quote uses a shorter length floor than a section quote, and the title wording still has to appear inside it. An article span includes its ustępy, so a quote cited to the article may sit in one of them, and a quote cited to `ust. 2` has to sit in that ustęp. No matching heading, or more than one, and the claim is ungrounded. The abstain text names the failure: quote not found, section mismatch, comparison unavailable, or models disagree. Character offsets are stored when the quote matches that span, and replaced when the model gave the right quote with the wrong offsets. The search uses only the characters that were sent to the model. A quote that appears only in a truncated tail is not marked grounded. A claim that fails is marked `grounded: false` and `withheld_from_avatar: true`. It stays in the JSON and is labeled ungrounded in the analyzer UI. It is left out of the Avatar prompt, the chamber axis-tag prompt, and the cross-bill conflict prompt.

The model-written title is held to that quote rule, and the title wording has to appear inside `title_quote` after the same NFC normalization and match fold, then casefold. The length floor's NFKC allowlist is not what the title check uses. An unrelated sentence from the bill cannot carry a different title into the prompts. When the quote fails, or the title is not inside it, the title stays on the analyze page and in the API, and the Avatar, chamber, and conflict prompts say "Bill under review" instead. The Avatar will not cast a yes or no on a section whose summary is ungrounded. A bill with no verbatim span of 40 visible characters has nothing that can ground a section, so the Avatar abstains. It also will not cast a yes or no on a section named by an ungrounded high-severity flag. A blank target or a target of "multiple" blocks every section. Those votes are abstentions with an explicit reason, and the withheld flag text is still shown. A long bill is split on section headings when that sends every character. The response says it was chunked. If the bill cannot be split, or a single section still does not fit, the tail is omitted, `truncation.blocking` is true, the UI shows an incomplete-analysis banner, and the Avatar abstains instead of voting on the shortened text.

### What that does not defend

None of this sandboxes the model. A document block and a fence are still model input. A model can follow orders written in the bill, including "ignore previous instructions", "omit Sec. 102", or "vote yes". The retry only catches malformed output. A bill that persuades the model to emit well-formed, dishonest JSON passes the schema.

Quote checks are a substring test, not a reading test. The allowlist stops "shall" and "The Secretary shall", including when they are padded with format characters, combining marks, variation selectors, Hangul fillers, the Braille blank, the Egyptian hieroglyph blanks, punctuation named FILLER, or a repeated punctuation mark. It does not stop visible padding. Homoglyphs, lookalike letters, and a run of ordinary letters still count, and a longer quote can still end before a negation or misstate a real sentence. A short Hebrew, Hindi, or Arabic quote that leans on combining marks can fall under the floor and be rejected. That is deliberate: marks do not count, and the consonant span has to be long enough on its own. Offsets use the model's span when that span is exactly the quote and lies in the allowed text, otherwise the first match there. They do not prove the quote is the right sentence. Missed provisions are not detected. Beneficiary claims have no section id, so they are checked against the analyzed bill text rather than one heading. Comparable laws and open questions are not quote-checked. They are not sent to the Avatar.

A hostile plain summary paired with a real `plain_summary_quote` still enters the Avatar prompt. The summary is a paraphrase, and the quote does not have to contain that paraphrase. That is the ride-along the title used to have. The title wording now has to appear inside `title_quote`, so an unrelated real quote cannot smuggle a different title. A title that is itself a long span planted in the bill can still be quoted and sent. When the title quote fails, the title is shown to the reader and kept out of the Avatar, chamber, and conflict prompts.

Chunking sends each chunk in its own call. A section quote has to fall inside the heading it names, so a sentence from another section does not ground it. Two headings with the same number, or an id that matches none, are refused rather than searched at large. Truncation is flagged, not repaired: the omitted tail is not analyzed, and a quote that appears only there is not marked grounded. If truncation is blocking, the Avatar abstains even when some quotes in the analyzed portion did ground.

The Avatar abstains when the rules above say to. That is a refusal to treat unverified text as a finding. It is not evidence the bill is fine, or that it is harmful. Chamber tags and conflict notes are still one model's judgment over whatever grounded text was left. A prompt cannot fully sandbox a model.

## License

MIT. See [LICENSE](LICENSE).
