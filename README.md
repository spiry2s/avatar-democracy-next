# Avatar Democracy — Bill Summary Tool

> Plain-language summaries and red-flag detection for legislation. The first concrete tool of a broader proposal for AI-mediated direct democracy.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#)

---

## What this is

A working tool that takes legislative bills (text or PDF), produces:

- **Plain-language summary** of what the bill actually does
- **Section-by-section breakdown** so nothing is buried
- **Red-flag detection** — provisions that look like lobbyist insertions, hidden giveaways, or buried problematic language
- **Beneficiary analysis** — who gains, who loses
- **Constitutional and legal concerns** if any

The goal: make it feasible for ordinary citizens to actually understand legislation, instead of relying on partisan spin or trusting that someone else has read it.

## Why this exists

This is the first concrete tool from a broader proposal called **Avatar Democracy** — the idea that the lower legislative chamber of representative democracies could be replaced with AI agents that vote for each citizen, executing their delegated political will.

You can read the full white paper here: [ai_avatar_democracy_v1.0.md](ai_avatar_democracy_v1.0.md)

The bill summary tool exists independently of that vision. It works with current legislatures, current democracy, current institutions. It just makes legislation legible to citizens. If the broader vision never happens, this tool still has value.

If the broader vision does happen, this becomes part of the audit infrastructure described in the white paper (Design Decision #19: "Independent competing AI summary services with red-flag detection for hidden provisions").

## Quick start

### Prerequisites

- Python 3.10 or higher
- An Anthropic API key — get one at [console.anthropic.com](https://console.anthropic.com)

### Installation

```bash
git clone https://github.com/spiry2s/avatar-democracy.git
cd avatar-democracy
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
```

### Run the web interface

```bash
python main.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

### Android app

The Android app in [`android/`](android/) has two modes:

- **Standalone (default):** the APK bundles the web UI plus a JavaScript port of the backend, and calls Claude directly with an API key you enter on the phone. No PC needed.
- **Remote:** a thin WebView onto this FastAPI server. Run `Start Avatar Democracy LAN.bat` and open the printed `http://YOUR-PC-IP:8000/?token=...` URL in the app. LAN mode always requires an access token.

See [`ANDROID.md`](ANDROID.md) for setup and APK build notes.

### Security notes

- State-changing requests from other origins are refused, so a website you visit can't drive your local server.
- Binding to a non-loopback address (LAN mode) without `AVATAR_ACCESS_TOKEN` generates a token at startup; every request must present it.
- Analyze-by-URL only fetches public http/https addresses (no LAN or loopback targets), follows at most 5 redirects, and caps downloads at 25 MB.

## Example output

For a bill about agricultural subsidies, the tool produces something like:

```
SUMMARY
This bill reauthorizes the Farm Bill for 5 years with $428B in spending.
Major provisions cover crop insurance, conservation programs, SNAP funding,
and rural development.

KEY SECTIONS
1. Title I — Commodities ($63B): expands crop insurance subsidies...
2. Title II — Conservation ($60B): continues CRP at current levels...
[...]

RED FLAGS DETECTED
⚠ Section 12047(c)(ii): exempts a single corporation by name from
  reporting requirements — appears to be lobbyist insertion
⚠ Section 8801: $2.3B sugar subsidy concentrated to 4,000 producers
⚠ Buried in Title VII: removes country-of-origin labeling for beef

WHO BENEFITS
- Large commodity producers (top 10% receive 70% of subsidies)
- Specific named corporations exempted in Section 12047
- Sugar industry (concentrated benefit)

WHO LOSES
- Small farmers (no targeted support added)
- Consumers (labeling removal reduces transparency)
- Conservation programs (real-dollar cuts due to inflation)
```

## How it works

Every model call goes through [`backend/llm.py`](backend/llm.py) and returns schema-validated JSON (structured outputs). Section ids in the vote, cross-check, and chamber schemas are enums of the bill's real section ids, so results from different calls always line up.

The prompts are designed to:

1. Avoid partisan framing
2. Surface specific concrete provisions, not vague summaries
3. Flag patterns associated with lobbyist insertions (named entities, narrow exemptions, buried provisions)
4. Identify who benefits and loses concretely

The prompts live next to the code that uses them ([`backend/summarizer.py`](backend/summarizer.py), [`backend/avatar.py`](backend/avatar.py), [`backend/chamber.py`](backend/chamber.py)) and are open for critique and improvement.

## Project structure

```
avatar-democracy/
├── main.py               # FastAPI server (pages + /api/*), access control
├── backend/
│   ├── llm.py            # Single Claude client: structured outputs, streaming, stop-reason checks
│   ├── summarizer.py     # Neutral bill analysis + red flags
│   ├── avatar.py         # Per-citizen Avatar vote, multi-model cross-check, delegate drift
│   ├── chamber.py        # Proxy Chamber: section axis tags + deterministic population vote
│   ├── bills.py          # Bill lifecycle state machine and tally
│   ├── profile.py        # Citizen profile: compass, values, delegates (asymmetric edits)
│   ├── questionnaire.py  # Compass-seeding questionnaire
│   ├── population.py     # Synthetic population
│   ├── conflict.py       # Cross-bill conflict detection
│   ├── audit.py          # Hash-chained, tamper-evident audit log
│   ├── storage.py        # Atomic JSON writes, per-key locks, corruption detection
│   ├── extractors.py     # Text / URL / PDF extraction (SSRF-hardened)
│   ├── sections.py       # Section-id and name normalization
│   └── config.py         # Env-overridable settings
├── frontend/             # Web UI (also bundled unmodified into the Android APK)
├── android/              # Android app (standalone + remote modes)
└── tests/                # pytest suite (pure logic + API endpoints with a fake model)
```

The white paper drafts are the `ai_avatar_democracy_*.md` / `ai_proxy_democracy_*.md` files in the repo root.

## Roadmap

This is **alpha**. Current capabilities:

- ✅ Text and PDF input
- ✅ Plain-language summaries
- ✅ Section breakdown
- ✅ Red-flag detection
- ✅ Beneficiary analysis
- ✅ Web interface
- ✅ Per-citizen Avatar voting with delegates
- ✅ Proxy Chamber (synthetic population)
- ✅ Multi-model cross-check and delegate-drift flags
- ✅ Tamper-evident audit log

Planned:

- Cross-check with a model from a different provider (today's check uses a second Claude model)
- Historical comparison (how does this bill compare to similar past bills?)
- Citation tracking (which sections cite which laws?)
- Lobbying disclosure cross-reference (was this language proposed by registered lobbyists?)
- Multi-language support
- Self-hosted model option (no Anthropic API required)

## Contributing

Contributions are welcome. Particularly valuable:

- **Better prompts** — if you can produce more accurate, less biased analysis with different prompts, please submit them
- **Real-world bill testing** — try the tool on actual legislation from your country and report failure modes
- **Translation** — bills exist in many languages
- **UI improvements** — make the output more readable and useful
- **Critique** — we want to know what doesn't work

## Limitations and honest framing

**This tool is not magic.** Claude can:

- Misunderstand technical legal language
- Miss truly subtle provisions
- Reflect biases from its training data
- Hallucinate provisions that aren't there

**Always verify against the actual bill text.** This tool is a reading aid, not an oracle.

**Votes are cross-checked by a second model, but analysis is still one model's view.** Disagreements between models are flagged on each section; a cross-provider check would be a stronger signal.

**Do not use this to make legal decisions.** It's not legal advice. Consult actual lawyers for actual legal matters.

## License

MIT — see [LICENSE](LICENSE)

## Acknowledgments

This project emerged from a long-form dialogue between a human (Filip) and Claude (Anthropic) exploring whether AI Avatar Democracy could be a workable reform for representative government. The white paper documents that conversation. The bill summary tool is the first piece we're building.

If this work is useful to you, the most valuable contribution you can make is a thoughtful critique. We'd rather find out what's broken than have something polished but wrong.

---

**Status:** alpha — works, but expect rough edges. Issues and PRs welcome.
