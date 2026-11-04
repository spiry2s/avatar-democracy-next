"""
Bill analyzer — sends bill text to Claude and structures the output.

This module is intentionally simple. The complexity is in the prompts
(see prompts.py), not the orchestration. That's by design — better
prompts mean better analysis, and prompts are easier to review and
improve than complex code.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts import (
    SUMMARY_PROMPT,
    SECTIONS_PROMPT,
    RED_FLAGS_PROMPT,
    BENEFICIARIES_PROMPT,
    SYSTEM_PROMPT,
)

load_dotenv()


@dataclass
class BillAnalysis:
    """Structured output of a bill analysis."""

    summary: str = ""
    sections: list[dict] = field(default_factory=list)
    red_flags: list[dict] = field(default_factory=list)
    beneficiaries: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class BillAnalyzer:
    """
    Analyzes legislative bill text using Claude.

    Each analysis aspect (summary, sections, red flags, beneficiaries)
    is a separate API call. This costs more but produces better results
    than asking one prompt to do everything — Claude has more attention
    for each task and the prompts can be refined independently.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-opus-4-8",
        max_tokens: int = 4096,
    ):
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "No API key provided. Set ANTHROPIC_API_KEY environment "
                "variable or pass api_key argument."
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def analyze(
        self,
        bill_text: str,
        bill_title: Optional[str] = None,
        skip: Optional[list[str]] = None,
    ) -> BillAnalysis:
        """
        Run full analysis on a bill.

        Args:
            bill_text: The complete text of the bill.
            bill_title: Optional title/identifier for the bill.
            skip: Optional list of analyses to skip
                  (e.g. ["red_flags"] to skip red flag detection).

        Returns:
            BillAnalysis with all requested aspects populated.
        """
        skip = skip or []
        analysis = BillAnalysis()
        analysis.metadata = {
            "title": bill_title or "Untitled bill",
            "length_chars": len(bill_text),
            "model": self.model,
        }

        if "summary" not in skip:
            try:
                analysis.summary = self._get_summary(bill_text)
            except Exception as e:
                analysis.errors.append(f"Summary failed: {e}")

        if "sections" not in skip:
            try:
                analysis.sections = self._get_sections(bill_text)
            except Exception as e:
                analysis.errors.append(f"Sections failed: {e}")

        if "red_flags" not in skip:
            try:
                analysis.red_flags = self._get_red_flags(bill_text)
            except Exception as e:
                analysis.errors.append(f"Red flags failed: {e}")

        if "beneficiaries" not in skip:
            try:
                analysis.beneficiaries = self._get_beneficiaries(bill_text)
            except Exception as e:
                analysis.errors.append(f"Beneficiaries failed: {e}")

        return analysis

    def _call_claude(self, prompt: str, bill_text: str) -> str:
        """Make a single API call to Claude."""
        message = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"{prompt}\n\n---BILL TEXT BEGINS---\n{bill_text}\n---BILL TEXT ENDS---",
                }
            ],
        )
        return message.content[0].text

    def _parse_json_response(self, response: str) -> dict | list:
        """
        Parse JSON from Claude's response.

        Claude sometimes wraps JSON in markdown code fences or includes
        prose before/after. Strip those and parse what's left.
        """
        text = response.strip()

        if "```json" in text:
            text = text.split("```json", 1)[1]
            text = text.split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1]
            text = text.split("```", 1)[0]

        text = text.strip()
        return json.loads(text)

    def _get_summary(self, bill_text: str) -> str:
        return self._call_claude(SUMMARY_PROMPT, bill_text).strip()

    def _get_sections(self, bill_text: str) -> list[dict]:
        response = self._call_claude(SECTIONS_PROMPT, bill_text)
        result = self._parse_json_response(response)
        return result if isinstance(result, list) else result.get("sections", [])

    def _get_red_flags(self, bill_text: str) -> list[dict]:
        response = self._call_claude(RED_FLAGS_PROMPT, bill_text)
        result = self._parse_json_response(response)
        return result if isinstance(result, list) else result.get("red_flags", [])

    def _get_beneficiaries(self, bill_text: str) -> dict:
        response = self._call_claude(BENEFICIARIES_PROMPT, bill_text)
        result = self._parse_json_response(response)
        return result if isinstance(result, dict) else {"raw": result}
