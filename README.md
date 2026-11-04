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

You can read the full white paper here: [docs/white-paper.md](docs/white-paper.md)

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

An Android WebView client lives in [`android/`](android/). It connects to the same FastAPI backend rather than bundling the Python server or API key into the APK.

- Emulator backend URL: `http://10.0.2.2:8000`
- Physical phone: run `Start Avatar Democracy LAN.bat`, then enter the printed `http://YOUR-PC-IP:8000` URL in the app.

See [`ANDROID.md`](ANDROID.md) for setup and APK build notes.

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

The tool sends the bill text to Claude with carefully crafted prompts that ask for structured analysis. The prompts are designed to:

1. Avoid partisan framing
2. Surface specific concrete provisions, not vague summaries
3. Flag patterns associated with lobbyist insertions (named entities, narrow exemptions, buried provisions)
4. Identify who benefits and loses concretely

The prompts are in [`backend/prompts.py`](backend/prompts.py) and are open for critique and improvement. Better prompts produce better analysis.

## Project structure

```
avatar-democracy/
├── backend/
│   ├── app.py            # FastAPI web server
│   ├── analyzer.py       # Bill analysis logic
│   ├── prompts.py        # All Claude prompts (open for review)
│   ├── pdf_parser.py     # PDF extraction
│   └── cli.py            # Command-line interface
├── frontend/
│   ├── index.html        # Web UI
│   ├── style.css         # Styling
│   └── app.js            # Browser logic
├── docs/
│   ├── white-paper.md    # The full Avatar Democracy proposal
│   └── prompt-design.md  # Why the prompts are written this way
├── examples/
│   └── sample-bills/     # Example bills for testing
└── tests/
    └── test_analyzer.py  # Tests
```

## Roadmap

This is **alpha**. Current capabilities:

- ✅ Text and PDF input
- ✅ Plain-language summaries
- ✅ Section breakdown
- ✅ Red-flag detection
- ✅ Beneficiary analysis
- ✅ Web interface
- ✅ CLI tool

Planned:

- Comparison across multiple AI providers (catch single-model bias)
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Limitations and honest framing

**This tool is not magic.** Claude can:

- Misunderstand technical legal language
- Miss truly subtle provisions
- Reflect biases from its training data
- Hallucinate provisions that aren't there

**Always verify against the actual bill text.** This tool is a reading aid, not an oracle.

**This tool reflects one model's analysis.** A future version should run the same bill through multiple models and surface disagreements — that's a much stronger signal than any single model's output.

**Do not use this to make legal decisions.** It's not legal advice. Consult actual lawyers for actual legal matters.

## License

MIT — see [LICENSE](LICENSE)

## Acknowledgments

This project emerged from a long-form dialogue between a human (Filip) and Claude (Anthropic) exploring whether AI Avatar Democracy could be a workable reform for representative government. The white paper documents that conversation. The bill summary tool is the first piece we're building.

If this work is useful to you, the most valuable contribution you can make is a thoughtful critique. We'd rather find out what's broken than have something polished but wrong.

---

**Status:** alpha — works, but expect rough edges. Issues and PRs welcome.
