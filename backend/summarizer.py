"""
Core bill analysis logic.

The quality of this tool lives almost entirely in the prompts. If you want
to improve output, start here. The API call structure is intentionally simple
so prompt work isn't buried in orchestration complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from backend import config, llm
from backend.llm import INT, LEVEL, STR, arr, enum, obj


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

Your output is a JSON object; the field meanings are described in the request."""


ANALYSIS_PROMPT = """Analyze the following legislative bill and produce a structured analysis.

Field guide:

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

Bill text follows.

---

"""


RED_FLAG_TYPES = [
    "buried_provision", "broad_delegation", "definition_change", "asymmetric_sunset",
    "hidden_funding", "cross_reference", "vague_language", "exemption",
    "retroactive_effect", "severability_risk",
]

ANALYSIS_SCHEMA = obj({
    "title": STR,
    "plain_summary": STR,
    "sections": arr(obj({
        "section_id": STR,
        "heading": STR,
        "summary": STR,
        "significance": LEVEL,
        "significance_reason": STR,
    })),
    "beneficiaries": obj({"benefits": arr(STR), "costs": arr(STR), "regulated": arr(STR)}),
    "red_flags": arr(obj({
        "type": enum(RED_FLAG_TYPES),
        "section": STR,
        "description": STR,
        "severity": LEVEL,
    })),
    "comparable_laws": arr(STR),
    "open_questions": arr(STR),
    "metadata": obj({
        "estimated_pages": INT,
        "complexity": LEVEL,
        "primary_subject": STR,
        "your_confidence": LEVEL,
    }),
})


def analyze_bill(bill_text: str, client: Anthropic | None = None) -> BillAnalysis:
    """
    Analyze a bill and return structured output.

    If the bill is longer than config.MAX_BILL_CHARS it is cut, and the cut is
    recorded in metadata (truncated / analyzed_chars / total_chars) so the UI
    can warn the citizen that the end of the bill was not read.

    Raises:
        ValueError: If bill text is empty or the response is unusable.
        RuntimeError: If API key is not configured.
    """
    if not bill_text or not bill_text.strip():
        raise ValueError("Bill text is empty")

    total = len(bill_text)
    truncated = total > config.MAX_BILL_CHARS
    if truncated:
        bill_text = bill_text[: config.MAX_BILL_CHARS] + "\n\n[BILL TRUNCATED FOR LENGTH]"

    data = llm.call_json(
        name="analysis",
        model=config.MODEL,
        system=SYSTEM_PROMPT,
        prompt=ANALYSIS_PROMPT + bill_text,
        schema=ANALYSIS_SCHEMA,
        max_tokens=64000,
        effort=config.MODEL_EFFORT,
        client=client,
    )

    metadata = dict(data.get("metadata", {}))
    metadata.update({
        "truncated": truncated,
        "analyzed_chars": min(total, config.MAX_BILL_CHARS),
        "total_chars": total,
        "model": config.MODEL,
    })
    open_questions = list(data.get("open_questions", []))
    if truncated:
        open_questions.insert(0, (
            f"Only the first {config.MAX_BILL_CHARS:,} of {total:,} characters were analyzed. "
            "Provisions after that point were NOT reviewed for red flags."
        ))

    return BillAnalysis(
        title=data.get("title", "Untitled bill"),
        plain_summary=data.get("plain_summary", ""),
        sections=data.get("sections", []),
        beneficiaries=data.get("beneficiaries", {"benefits": [], "costs": [], "regulated": []}),
        red_flags=data.get("red_flags", []),
        comparable_laws=data.get("comparable_laws", []),
        open_questions=open_questions,
        metadata=metadata,
    )
