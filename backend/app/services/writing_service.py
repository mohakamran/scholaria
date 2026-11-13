"""Academic writing workflows and LLM explanation of computed statistics."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import OllamaError, ValidationFailed
from app.core.prompts import ACADEMIC_WRITING_PROMPT, INTERPRET_STATS_PROMPT
from app.database.models import AnalysisRun, Citation
from app.database.repositories import CitationRepository, ProjectRepository
from app.schemas.writing import WritingRequest
from app.services.ollama_service import OllamaService, estimate_tokens
from app.services.settings_service import get_runtime_settings

# task -> (label, instruction, whether input text is required)
WRITING_TASKS: dict[str, tuple[str, str, bool]] = {
    "title": ("Research title development",
              "Propose 5 concise, informative academic titles for the research described. Vary the style "
              "(descriptive, question, colon format). Briefly note the focus of each.", True),
    "abstract": ("Abstract drafting",
                 "Draft a structured abstract (Background, Objective, Methods, Results, Conclusion; 200-250 words) "
                 "using only the information provided. Mark missing elements as [to be completed].", True),
    "introduction": ("Introduction development",
                     "Draft an introduction that moves from the broad context to the specific problem, research gap, "
                     "aim and contribution. Insert [citation needed] where claims require support.", True),
    "literature_review": ("Literature review organization",
                          "Organise the supplied literature notes into a thematic literature review outline with "
                          "themes, key arguments, points of agreement/disagreement, and how each theme relates to "
                          "the study. Only cite works the user supplied.", True),
    "research_gap": ("Research gap identification",
                     "Identify plausible research gaps from the supplied material (theoretical, methodological, "
                     "contextual, empirical). Explain each gap and why it matters. Do not invent prior studies.", True),
    "hypotheses": ("Hypothesis development",
                   "Develop testable hypotheses (H1, H2, ...) from the described constructs and theory. For each, "
                   "give the theoretical rationale and the expected direction of the relationship.", True),
    "research_questions": ("Research question development",
                           "Develop clear, focused, researchable research questions (RQ1, RQ2, ...) aligned with the "
                           "aim. Indicate whether each is descriptive, comparative or relational.", True),
    "methodology": ("Methodology drafting",
                    "Draft a methodology section: research design, population and sampling, data collection, "
                    "instruments/measures, procedure, data analysis and ethics. Use [details needed] for missing "
                    "information; never invent sample sizes or instruments.", True),
    "results": ("Results explanation",
                "Write a results section in academic style that reports the supplied statistical output exactly. "
                "Do not change, round differently, or add any numbers. Follow APA reporting conventions.", True),
    "discussion": ("Discussion drafting",
                   "Draft a discussion: summarise key findings, interpret them, relate them to the supplied "
                   "literature, state implications, limitations and future research. Do not overstate findings.", True),
    "conclusion": ("Conclusion drafting",
                   "Draft a concise conclusion summarising the contribution, main findings, implications and "
                   "future directions, using only the information provided.", True),
    "improve": ("Academic language improvement",
                "Improve the academic tone, clarity, flow and concision of the text while preserving its meaning "
                "and all factual content. Then list the main changes briefly.", True),
    "grammar": ("Grammar correction",
                "Correct grammar, spelling and punctuation only. Preserve wording and meaning as much as possible. "
                "Return the corrected text, then a short list of corrections.", True),
    "paraphrase": ("Paraphrasing",
                   "Paraphrase the text in an academic register with substantially different wording and structure "
                   "while preserving the exact meaning. Keep any citations attached to the same claims.", True),
    "latex": ("LaTeX formatting",
              "Convert the text into clean, compilable LaTeX (sections, lists, equations, tables as appropriate). "
              "Escape special characters. Keep citation markers as \\cite{key} only if keys were supplied; otherwise "
              "keep [citation needed]. Output only the LaTeX in one code block.", True),
}

FORMAT_RULES = {
    "markdown": "Format the output in Markdown.",
    "latex": "Format the output as LaTeX (no preamble unless asked), inside one ```latex code block.",
    "plain": "Format the output as plain text without Markdown.",
}


def list_tasks() -> list[dict[str, str]]:
    return [{"id": key, "label": label, "instruction": instruction}
            for key, (label, instruction, _) in WRITING_TASKS.items()]


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def unsupported_citation_warnings(text: str, refs: list[Citation]) -> list[str]:
    """Flag citations of bibliography entries that have no user notes.

    The model only knows such an entry's metadata, so any finding it attributes
    to the source is unverified. This check is deterministic and runs on the
    full generated text.
    """
    warnings: list[str] = []
    for ref in refs:
        if ref.notes or not ref.authors:
            continue
        surname = re.split(r"[,\s]", ref.authors.strip())[0]
        if len(surname) < 2:
            continue
        if re.search(rf"\b{re.escape(surname)}\b", text):
            year = ref.publication_year or "n.d."
            warnings.append(
                f"The draft cites {surname} ({year}), but that bibliography entry has no notes on what the source "
                "reports. Check every claim attributed to it against the original paper."
            )
    return warnings


async def stream_llm_sse(ollama: OllamaService, model: str, messages: list[dict[str, str]], *, temperature: float,
                         num_ctx: int, num_predict: int, keep_alive: str,
                         post_check: Callable[[str], list[str]] | None = None) -> AsyncIterator[str]:
    """Stream a stateless generation as SSE (meta, token*, done | error)."""
    yield _sse("meta", {"model": model})
    total = 0
    parts: list[str] = []
    stats: dict[str, Any] = {}
    try:
        async for event in ollama.chat_stream(model, messages, temperature=temperature, num_ctx=num_ctx,
                                              num_predict=num_predict, keep_alive=keep_alive):
            if event.done:
                stats = event.stats
                continue
            total += len(event.content)
            parts.append(event.content)
            yield _sse("token", {"content": event.content})
    except OllamaError as exc:
        yield _sse("error", exc.to_dict()["error"])
        return
    warnings = post_check("".join(parts)) if post_check else []
    yield _sse("done", {"status": "complete", "chars": total, "completion_tokens": stats.get("eval_count"),
                        "note": "Reached the maximum response length." if stats.get("done_reason") == "length" else None,
                        "warnings": warnings})


class WritingService:
    def __init__(self, db: Session, user_id: int | None = None, ollama: OllamaService | None = None) -> None:
        self.db = db
        self.user_id = user_id
        self.runtime = get_runtime_settings(db)
        self.ollama = ollama or OllamaService(self.runtime.ollama_url)

    def _project_context(self, req: WritingRequest) -> str:
        if req.project_id is None:
            return ""
        project = ProjectRepository(self.db, self.user_id).get_or_404(req.project_id)
        parts: list[str] = []
        if req.include_project_context:
            for label, value in (("Project title", project.title), ("Description", project.description),
                                 ("Objectives", project.objectives), ("Research questions", project.research_questions),
                                 ("Hypotheses", project.hypotheses)):
                if value:
                    parts.append(f"{label}: {value}")
        if req.include_bibliography:
            refs = CitationRepository(self.db).list_for_project(project.id)
            if refs:
                lines = []
                for c in refs[:40]:
                    bits = [c.authors or "[authors missing]", f"({c.publication_year})" if c.publication_year else "(n.d.)",
                            c.title, c.journal or "", f"https://doi.org/{c.doi}" if c.doi else ""]
                    line = "- " + " ".join(b for b in bits if b)
                    if c.notes:
                        line += f"\n  User's notes on what this source reports: {c.notes[:400]}"
                    lines.append(line)
                parts.append(
                    "Verified bibliography supplied by the user (the ONLY references you may cite):\n"
                    + "\n".join(lines)
                    + "\nYou only know these references' metadata and the user's notes. Never state what a "
                    "reference found, argued or demonstrated unless the user's notes for it say so; otherwise "
                    "write the claim with [citation needed] or mark it '[verify against source]'."
                )
            else:
                parts.append("The user's bibliography is empty: do not cite any specific works.")
        return "\n\n".join(parts)

    async def prepare(self, req: WritingRequest) -> tuple[str, list[dict[str, str]], float, int, int]:
        if req.task not in WRITING_TASKS:
            raise ValidationFailed(f"Unknown writing task '{req.task}'")
        label, instruction, needs_text = WRITING_TASKS[req.task]
        if needs_text and not req.text.strip() and not (req.context or "").strip():
            raise ValidationFailed("Please provide text or research details for this task.")
        model = await self.ollama.resolve_model(req.model, self.runtime.default_model, self.runtime.fallback_model)
        num_ctx = self.runtime.context_length
        num_predict = req.max_tokens or self.runtime.max_tokens
        fmt = "latex" if req.task == "latex" else req.output_format
        system = f"{ACADEMIC_WRITING_PROMPT}\n\n{FORMAT_RULES[fmt]}"
        sections = [f"Task: {label}. {instruction}"]
        project_context = self._project_context(req)
        if project_context:
            sections.append(project_context)
        if req.context and req.context.strip():
            sections.append(f"Additional context from the user:\n{req.context.strip()}")
        if req.text.strip():
            sections.append(f"<input_text>\n{req.text.strip()}\n</input_text>")
        user = "\n\n".join(sections)
        budget = num_ctx - num_predict - estimate_tokens(system) - 32
        if estimate_tokens(user) > budget:
            raise ValidationFailed(
                f"The input is too long for the current context window (~{budget * 4} characters available). "
                "Shorten the text, split it into parts, or increase the context length in Settings."
            )
        temperature = req.temperature if req.temperature is not None else self.runtime.temperature
        return model, [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature, num_ctx, num_predict

    def citation_check(self, req: WritingRequest) -> Callable[[str], list[str]] | None:
        if req.project_id is None or not req.include_bibliography:
            return None
        ProjectRepository(self.db, self.user_id).get_or_404(req.project_id)
        refs = list(CitationRepository(self.db).list_for_project(req.project_id))
        return lambda text: unsupported_citation_warnings(text, refs)

    async def interpret_analysis(self, run: AnalysisRun, model: str | None, question: str | None) -> dict[str, Any]:
        resolved = await self.ollama.resolve_model(model, self.runtime.default_model, self.runtime.fallback_model)
        results = json.loads(run.results)
        results.pop("charts", None)  # chart data is large and not needed for interpretation
        payload = json.dumps(results, default=str)
        max_chars = (self.runtime.context_length - self.runtime.max_tokens - 400) * 3
        truncated = len(payload) > max_chars
        payload = payload[:max_chars]
        user = (f"Analysis type: {run.analysis_type}\nComputed results (JSON):\n{payload}\n\n"
                + (f"User question: {question}\n\n" if question else "")
                + "Explain these results for an academic report.")
        result = await self.ollama.chat(
            resolved, [{"role": "system", "content": INTERPRET_STATS_PROMPT}, {"role": "user", "content": user}],
            temperature=0.2, num_ctx=self.runtime.context_length, num_predict=self.runtime.max_tokens,
            keep_alive=self.runtime.keep_alive,
        )
        note = "Explanation generated by the language model from stored results; numbers come from the analysis."
        if truncated:
            note += " Results were truncated to fit the context window."
        return {"analysis_id": run.id, "model": resolved, "explanation": result.content, "note": note}
