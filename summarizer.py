"""
Core bill summarization logic.

This is where the actual analysis happens. The quality of this tool depends
almost entirely on the prompts in this file. Contributors who want to improve
output quality should focus here.

Design principles:
- Output is structured JSON, never free-form prose. This lets the UI and
  downstream tools consume it reliably.
- We ask Claude to identify what it's uncertain about, not just what it knows.
  Calibrated uncertainty is more useful than confident summaries.
- Red flags are explicitly enumerated rather than left to Claude's discretion,
  so contributors can see and improve the detection criteria.
- The system prompt explicitly tells Claude to avoid partisan framing and to
  flag its own potential biases.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

# Model selection. Opus is the quality-critical choice for red-flag detection,
# and since the 4.5 generation costs ~1.7x Sonnet rather than the old 5x premium.
# Haiku would be cheaper but miss subtle red flags.
MODEL = "claude-opus-4-8"

# Maximum bill length we'll process in a single pass. Larger bills get chunked.
MAX_SINGLE_PASS_CHARS = 200_000


@dataclass
class BillAnalysis:
    """Structured output from the summarizer."""

    title: str
    plain_summary: str
    sections: list[dict[str, Any]]
    beneficiaries: dict[str, list[str]]
    red_flags: list[dict[str, str]]
    comparable_laws: list[str]
    open_questions: list[str]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "plain_summary": self.plain_summary,
            "sections": self.sections,
            "beneficiaries": self.beneficiaries,
            "red_flags": self.red_flags,
            "comparable_laws": self.comparable_laws,
            "open_questions": self.open_questions,
            "metadata": self.metadata,
        }


SYSTEM_PROMPT = """You are a legislative analyst producing neutral, plain-language summaries of bills for ordinary citizens. Your goal is to help people understand what bills actually do, not what their proponents or opponents say they do.

Core principles:

1. NEUTRALITY. Do not use partisan framing, loaded terminology, or rhetorical flourishes. A bill that "expands social programs" should not be described as "generously providing for those in need" or "growing government overreach" — both are loaded. Just say what it does.

2. CALIBRATED UNCERTAINTY. When the bill is ambiguous, say so. When implementation will depend on agency interpretation, say so. When you are not sure what something means, say so. Confident wrong summaries are worse than honest uncertain ones.

3. WHAT IT DOES, NOT WHAT IT'S CALLED. Bill titles are often misleading ("Patriot Act", "Affordable Care Act", "Inflation Reduction Act"). Describe the actual mechanisms, not the marketing.

4. SURFACE THE BURIED. Bills often contain provisions unrelated to their nominal purpose. Funding for unrelated programs, changes to other laws by reference, sunset clauses on popular provisions paired with permanence on unpopular ones, broad delegations of authority to agencies, definitions that change the meaning of existing law. These are exactly the things citizens need to know about.

5. WHO BENEFITS, WHO PAYS. Most legislation creates winners and losers. Identify them concretely. "Pharmaceutical companies" is more useful than "industry stakeholders". "Households earning under $X" is more useful than "working families".

6. ACKNOWLEDGE YOUR OWN BIAS. You are an AI trained on a corpus that has political leanings. If you notice yourself framing something in a way that favors one political perspective, flag this in the open_questions field.

You will output ONLY a single JSON object matching the schema provided. No prose before or after. No markdown code fences. Just JSON."""


ANALYSIS_PROMPT = """Analyze the following legislative bill and produce a structured JSON analysis.

Output JSON schema:

{
  "title": "Plain-language descriptive title (NOT the bill's official name unless that name accurately describes the bill)",
  "plain_summary": "2-4 sentences. What does this bill actually do? Written for a non-lawyer who has never heard of it.",
  "sections": [
    {
      "section_id": "Section identifier from the bill (e.g., 'Sec. 101', 'Title II Part A')",
      "heading": "Plain-language heading",
      "summary": "1-3 sentences explaining what this section does",
      "significance": "high | medium | low",
      "significance_reason": "Why this section matters or doesn't"
    }
  ],
  "beneficiaries": {
    "benefits": ["Specific groups, industries, or entities that benefit from this bill"],
    "costs": ["Specific groups that bear costs or new obligations"],
    "regulated": ["Specific groups whose behavior is constrained or regulated"]
  },
  "red_flags": [
    {
      "type": "One of: buried_provision, broad_delegation, definition_change, asymmetric_sunset, hidden_funding, cross_reference, vague_language, exemption, retroactive_effect, severability_risk",
      "section": "Which section of the bill (or 'multiple')",
      "description": "Plain-language explanation of what's concerning and why",
      "severity": "high | medium | low"
    }
  ],
  "comparable_laws": [
    "Existing laws this bill amends, replaces, or interacts with"
  ],
  "open_questions": [
    "Things the bill doesn't specify but that will matter for implementation",
    "Areas where reasonable interpretations differ",
    "Aspects where you (the analyst AI) noticed potential framing bias in your own analysis"
  ],
  "metadata": {
    "estimated_pages": "Number",
    "complexity": "high | medium | low",
    "primary_subject": "One-line description of subject matter",
    "your_confidence": "high | medium | low — how confident you are in this analysis being accurate"
  }
}

Red flag types defined:
- buried_provision: Substantive provision in an unrelated section, easy to miss
- broad_delegation: Authority delegated to executive/agency with insufficient guardrails
- definition_change: Changes a defined term in a way that affects other laws
- asymmetric_sunset: Popular provisions sunset, unpopular ones permanent (or vice versa)
- hidden_funding: Money for things not described in the bill's title or summary
- cross_reference: Modifies existing law by reference in ways not obvious from this bill alone
- vague_language: Key terms undefined or so broad as to be unconstrained
- exemption: Specific entity or class exempted from rules others must follow
- retroactive_effect: Provisions that apply backwards in time
- severability_risk: Drafting that makes the bill all-or-nothing despite mixed support

Be thorough on red flags. Citizens depend on you to find what others would miss.

Bill text follows below the divider. Output ONLY the JSON, no other text.

---

"""


def get_client() -> Anthropic:
    """Get an Anthropic client. Reads ANTHROPIC_API_KEY from environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Get a key at https://console.anthropic.com and set it before running."
        )
    return Anthropic(api_key=api_key)


def analyze_bill(bill_text: str, client: Anthropic | None = None) -> BillAnalysis:
    """
    Analyze a bill and return structured output.

    Args:
        bill_text: The full text of the bill
        client: Optional pre-configured Anthropic client

    Returns:
        BillAnalysis with structured analysis

    Raises:
        ValueError: If the bill text is empty or the response can't be parsed
        anthropic.APIError: If the API call fails
    """
    if not bill_text or not bill_text.strip():
        raise ValueError("Bill text is empty")

    if client is None:
        client = get_client()

    # For very long bills, we'd ideally chunk and aggregate. For v1, we
    # pass through and let the model handle context length. Claude's context
    # window handles most bills (200K tokens ~= 150K words ~= 300 pages).
    if len(bill_text) > MAX_SINGLE_PASS_CHARS:
        bill_text = bill_text[:MAX_SINGLE_PASS_CHARS] + "\n\n[BILL TRUNCATED FOR LENGTH]"

    full_prompt = ANALYSIS_PROMPT + bill_text

    message = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": full_prompt}],
    )

    # Extract text from response. Content is a list of blocks; we want the text.
    response_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            response_text += block.text

    # Parse JSON. Claude should return pure JSON per our prompt, but we strip
    # markdown fences if they snuck in.
    response_text = response_text.strip()
    if response_text.startswith("```"):
        # Strip markdown code fence
        lines = response_text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        response_text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Could not parse Claude's response as JSON: {e}\n\nResponse was:\n{response_text[:500]}"
        ) from e

    # Build BillAnalysis with safe defaults for missing fields
    return BillAnalysis(
        title=data.get("title", "Untitled bill"),
        plain_summary=data.get("plain_summary", ""),
        sections=data.get("sections", []),
        beneficiaries=data.get("beneficiaries", {"benefits": [], "costs": [], "regulated": []}),
        red_flags=data.get("red_flags", []),
        comparable_laws=data.get("comparable_laws", []),
        open_questions=data.get("open_questions", []),
        metadata=data.get("metadata", {}),
    )
