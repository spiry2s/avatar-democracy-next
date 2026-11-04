"""
Core bill analysis logic.

The quality of this tool lives almost entirely in the prompts. If you want
to improve output, start here. The API call structure is intentionally simple
so prompt work isn't buried in orchestration complexity.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from backend.config import MODEL

MAX_SINGLE_PASS_CHARS = 200_000


@dataclass
class BillAnalysis:
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

1. NEUTRALITY. Do not use partisan framing, loaded terminology, or rhetorical flourishes. Describe what the bill does mechanically.

2. CALIBRATED UNCERTAINTY. When the bill is ambiguous, say so. When implementation depends on agency interpretation, say so.

3. WHAT IT DOES, NOT WHAT IT'S CALLED. Bill titles are often misleading. Describe the actual mechanisms, not the marketing.

4. SURFACE THE BURIED. Bills often contain provisions unrelated to their nominal purpose. These are exactly what citizens need to know about.

5. WHO BENEFITS, WHO PAYS. Most legislation creates winners and losers. Identify them concretely. "Pharmaceutical companies" is more useful than "industry stakeholders".

6. ACKNOWLEDGE YOUR OWN BIAS. If you notice partisan framing in your own analysis, flag it in open_questions.

Output ONLY a single JSON object matching the schema provided. No prose before or after. No markdown code fences. Just JSON."""


ANALYSIS_PROMPT = """Analyze the following legislative bill and produce a structured JSON analysis.

Output JSON schema:

{
  "title": "Plain-language descriptive title (NOT the bill's official name unless accurate)",
  "plain_summary": "2-4 sentences. What does this bill actually do? Written for a non-lawyer.",
  "sections": [
    {
      "section_id": "Section identifier from the bill (e.g. 'Sec. 101')",
      "heading": "Plain-language heading",
      "summary": "1-3 sentences explaining what this section does",
      "significance": "high | medium | low",
      "significance_reason": "Why this section matters or doesn't"
    }
  ],
  "beneficiaries": {
    "benefits": ["Specific groups, industries, or entities that benefit"],
    "costs": ["Specific groups that bear costs or new obligations"],
    "regulated": ["Specific groups whose behavior is constrained"]
  },
  "red_flags": [
    {
      "type": "One of: buried_provision, broad_delegation, definition_change, asymmetric_sunset, hidden_funding, cross_reference, vague_language, exemption, retroactive_effect, severability_risk",
      "section": "Which section (or 'multiple')",
      "description": "Plain-language explanation of what's concerning and why",
      "severity": "high | medium | low"
    }
  ],
  "comparable_laws": ["Existing laws this bill amends, replaces, or interacts with"],
  "open_questions": [
    "Things the bill doesn't specify but that will matter for implementation",
    "Areas where reasonable interpretations differ",
    "Any potential framing bias you noticed in your own analysis"
  ],
  "metadata": {
    "estimated_pages": "Number",
    "complexity": "high | medium | low",
    "primary_subject": "One-line description",
    "your_confidence": "high | medium | low"
  }
}

Red flag types:
- buried_provision: Substantive provision in an unrelated section
- broad_delegation: Authority delegated to executive/agency without guardrails
- definition_change: Changes a defined term affecting other laws
- asymmetric_sunset: Popular provisions temporary, unpopular ones permanent (or vice versa)
- hidden_funding: Money for things not in the title
- cross_reference: Modifies existing law in ways not obvious from this bill alone
- vague_language: Key terms undefined or unconstrained
- exemption: Specific entity exempted from rules others follow
- retroactive_effect: Provisions applying backwards in time
- severability_risk: All-or-nothing drafting despite mixed support

Be thorough on red flags. Citizens depend on you to find what others miss.

Bill text follows. Output ONLY the JSON.

---

"""


def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Get a key at https://console.anthropic.com"
        )
    return Anthropic(api_key=api_key)


def analyze_bill(bill_text: str, client: Anthropic | None = None) -> BillAnalysis:
    """
    Analyze a bill and return structured output.

    Raises:
        ValueError: If bill text is empty or response can't be parsed.
        RuntimeError: If API key is not configured.
        anthropic.APIError: If the API call fails.
    """
    if not bill_text or not bill_text.strip():
        raise ValueError("Bill text is empty")

    if client is None:
        client = get_client()

    if len(bill_text) > MAX_SINGLE_PASS_CHARS:
        bill_text = bill_text[:MAX_SINGLE_PASS_CHARS] + "\n\n[BILL TRUNCATED FOR LENGTH]"

    message = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": ANALYSIS_PROMPT + bill_text}],
    )

    response_text = "".join(
        block.text for block in message.content if hasattr(block, "text")
    ).strip()

    # Strip markdown fences if they snuck in
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        response_text = "\n".join(lines[1:end])

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Could not parse response as JSON: {e}\n\nResponse:\n{response_text[:500]}"
        ) from e

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
