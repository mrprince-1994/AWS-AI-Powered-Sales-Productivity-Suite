"""
Live Meeting Assistant module.

Provides real-time MEDDPICC sales coaching during live calls by analyzing
streaming transcript lines and surfacing contextual question suggestions
to help account managers explore uncovered MEDDPICC elements.
"""

import json
import logging
import threading
import time

import boto3
from botocore.config import Config

from config import (
    AWS_REGION,
    HAIKU_MODEL_ID,
    MEDDPICC_BUFFER_MAX_LINES,
    MEDDPICC_DEBOUNCE_SECONDS,
    MEDDPICC_INFERENCE_TIMEOUT,
    MEDDPICC_MAX_CONSECUTIVE_FAILURES,
    MEDDPICC_MAX_DEBOUNCE_SECONDS,
)

logger = logging.getLogger(__name__)


class TranscriptBuffer:
    """Rolling window of recent finalized transcript lines.

    Thread-safe buffer that accumulates transcript lines for batched
    inference requests. Trims to the most recent max_lines entries.
    """

    def __init__(self, max_lines: int = MEDDPICC_BUFFER_MAX_LINES):
        self._max_lines = max_lines
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._new_since_last_flush = False

    def add(self, line: str) -> None:
        """Append a line, trim to max_lines (keeping most recent). Thread-safe."""
        with self._lock:
            self._lines.append(line)
            self._lines = self._lines[-self._max_lines:]
            self._new_since_last_flush = True

    def get_context(self) -> str:
        """Return all buffered lines joined by newline. Marks as flushed. Thread-safe."""
        with self._lock:
            self._new_since_last_flush = False
            if not self._lines:
                return ""
            return "\n".join(self._lines)

    def has_new_content(self) -> bool:
        """Return True if lines were added since last get_context() call. Thread-safe."""
        with self._lock:
            return self._new_since_last_flush

    def clear(self) -> None:
        """Reset buffer. Thread-safe."""
        with self._lock:
            self._lines = []
            self._new_since_last_flush = False


MEDDPICC_ELEMENTS = [
    "Metrics", "Economic Buyer", "Decision Criteria", "Decision Process",
    "Paper Process", "Implicate the Pain", "Champion", "Competition"
]

MEDDPICC_ABBREVIATIONS = ["M", "E", "D", "D", "P", "I", "C", "C"]


class MEDDPICCTracker:
    """Maintains coverage state for all 8 MEDDPICC elements.

    Coverage is monotonic within a session — once an element is marked
    as covered, it stays covered until reset() is called.
    """

    def __init__(self):
        self._coverage: dict[str, bool] = {e: False for e in MEDDPICC_ELEMENTS}
        self._evidence: dict[str, str] = {e: "" for e in MEDDPICC_ELEMENTS}

    def update(self, element: str, covered: bool, evidence: str = "") -> None:
        """Mark an element as covered with optional evidence snippet.

        Coverage is monotonic — once covered, stays covered (covered=False is ignored).
        Unknown elements are silently ignored.
        """
        if element not in MEDDPICC_ELEMENTS:
            return
        if covered:
            self._coverage[element] = True
            if evidence:
                self._evidence[element] = evidence

    def get_state(self) -> dict[str, bool]:
        """Return a copy of the current coverage dict."""
        return dict(self._coverage)

    def get_uncovered(self) -> list[str]:
        """Return list of element names where covered is False."""
        return [e for e, covered in self._coverage.items() if not covered]

    def get_summary(self) -> dict[str, dict]:
        """Return dict mapping each element to {'covered': bool, 'evidence': str}."""
        return {
            e: {"covered": self._coverage[e], "evidence": self._evidence[e]}
            for e in MEDDPICC_ELEMENTS
        }

    def reset(self) -> None:
        """Set all elements back to uncovered with empty evidence."""
        self._coverage = {e: False for e in MEDDPICC_ELEMENTS}
        self._evidence = {e: "" for e in MEDDPICC_ELEMENTS}


MEDDPICC_SYSTEM_PROMPT = """You are a MEDDPICC sales coach analyzing a live call transcript. Return ONLY valid JSON.

MEDDPICC elements: Metrics, Economic Buyer, Decision Criteria, Decision Process, Paper Process, Implicate the Pain, Champion, Competition.

Given the transcript and current coverage state:
1. Identify any MEDDPICC elements newly covered or strengthened in the conversation
2. Suggest 3-5 questions that naturally fit the current discussion topic to deepen MEDDPICC coverage
3. Questions should be conversational, not interrogative

Response format:
{"coverage_updates":[{"element":"...","covered":true,"evidence":"..."}],"suggestions":[{"element":"...","question":"..."}],"all_covered":false}

Rules:
- Suggest questions for ANY element — both uncovered AND already-covered elements benefit from deeper exploration
- Prioritize uncovered elements, but also suggest deepening questions for covered elements when relevant to the conversation
- Questions must relate to what's currently being discussed
- For covered elements, suggest questions that strengthen evidence, uncover nuance, or validate assumptions
- Keep evidence snippets under 15 words
- Return 0 suggestions only if no element fits the current topic at all
- Try to cover multiple different MEDDPICC elements across your suggestions"""


class MeetingAssistant:
    """Central orchestrator for real-time MEDDPICC coaching.

    Owns the transcript buffer, coverage tracker, debounce logic, and
    inference lifecycle. All UI updates are dispatched via root.after()
    to keep the main thread responsive.
    """

    def __init__(self, root, on_suggestions, on_coverage, on_status, on_summary):
        """
        Args:
            root: tkinter root for root.after() scheduling
            on_suggestions: callback(list[dict]) — called with new suggestions
            on_coverage: callback(dict[str, bool]) — called with updated coverage
            on_status: callback(str) — called with status messages (errors, etc.)
            on_summary: callback(str) — called with post-call MEDDPICC summary
        """
        self._root = root
        self._on_suggestions = on_suggestions
        self._on_coverage = on_coverage
        self._on_status = on_status
        self._on_summary = on_summary

        self._buffer = TranscriptBuffer()
        self._tracker = MEDDPICCTracker()

        self._active = False
        self._last_inference_time: float = 0
        self._inference_in_progress = False
        self._queued_inference = False
        self._consecutive_failures = 0
        self._current_debounce: float = MEDDPICC_DEBOUNCE_SECONDS
        self._inference_thread: threading.Thread | None = None

        # Question history: accumulates all questions generated during a call
        # Each entry: {"element": str, "question": str, "addressed": bool, "timestamp": float}
        self._question_history: list[dict] = []
        self._history_lock = threading.Lock()

    def activate(self) -> None:
        """Start monitoring. Called when recording begins."""
        self._active = True
        self._tracker.reset()
        self._buffer.clear()
        self._consecutive_failures = 0
        self._current_debounce = MEDDPICC_DEBOUNCE_SECONDS
        self._last_inference_time = 0
        self._inference_in_progress = False
        self._queued_inference = False
        with self._history_lock:
            self._question_history.clear()

    def deactivate(self) -> None:
        """Stop monitoring, wait for in-flight inference, generate post-call summary."""
        self._active = False

        if self._inference_thread is not None and self._inference_thread.is_alive():
            self._inference_thread.join(timeout=5)

        summary_thread = threading.Thread(
            target=self._generate_post_call_summary, daemon=True
        )
        summary_thread.start()

    def add_line(self, text: str) -> None:
        """Feed a finalized transcript line. Thread-safe."""
        if not self._active:
            return

        self._buffer.add(text)

        elapsed = time.time() - self._last_inference_time
        if elapsed >= self._current_debounce:
            if self._buffer.has_new_content() and not self._inference_in_progress:
                self._inference_thread = threading.Thread(
                    target=self._run_inference, daemon=True
                )
                self._inference_thread.start()
            elif self._inference_in_progress:
                self._queued_inference = True
        else:
            if self._inference_in_progress:
                self._queued_inference = True

    @property
    def is_active(self) -> bool:
        return self._active

    def get_coverage_summary(self) -> dict:
        """Return final MEDDPICC coverage for debrief integration."""
        return self._tracker.get_summary()

    def export_state(self) -> dict:
        """Export full MEDDPICC state as a serializable dict for persistence.

        Returns: {"coverage": {element: {"covered": bool, "evidence": str}},
                  "questions": [{"element", "question", "addressed", "timestamp"}]}
        """
        with self._history_lock:
            return {
                "coverage": self._tracker.get_summary(),
                "questions": list(self._question_history),
            }

    def load_state(self, data: dict) -> None:
        """Restore MEDDPICC state from a previously exported dict.

        Used when loading a historical session from the sidebar.
        """
        if not data:
            return
        self._tracker.reset()
        for element, info in data.get("coverage", {}).items():
            if info.get("covered"):
                self._tracker.update(element, True, info.get("evidence", ""))
        with self._history_lock:
            self._question_history = data.get("questions", [])
        # Dispatch UI updates
        self._root.after(0, self._on_coverage, self._tracker.get_state())

    def seed_from_previous(self, data: dict) -> None:
        """Seed coverage from a previous session's MEDDPICC data.

        Carries forward covered elements so MEDDPICC builds cumulatively
        across calls with the same customer. Questions are NOT carried forward
        since they're contextual to each call.
        """
        if not data:
            return
        for element, info in data.get("coverage", {}).items():
            if info.get("covered"):
                self._tracker.update(element, True, info.get("evidence", ""))
        # Dispatch initial coverage to UI
        self._root.after(0, self._on_coverage, self._tracker.get_state())

    def get_question_history(self, element: str | None = None) -> list[dict]:
        """Return accumulated question history, optionally filtered by element.

        Each entry: {"element": str, "question": str, "addressed": bool, "timestamp": float}
        """
        with self._history_lock:
            if element:
                return [q for q in self._question_history if q["element"] == element]
            return list(self._question_history)

    def _record_questions(self, suggestions: list[dict]) -> None:
        """Append new suggestions to question history. Mark questions as addressed
        when their element becomes covered."""
        coverage = self._tracker.get_state()
        now = time.time()
        with self._history_lock:
            # Mark previously recorded questions as addressed if element is now covered
            for q in self._question_history:
                if not q["addressed"] and coverage.get(q["element"], False):
                    q["addressed"] = True
            # Add new questions (skip duplicates by question text)
            existing_texts = {q["question"] for q in self._question_history}
            for s in suggestions:
                if s.get("question") and s["question"] not in existing_texts:
                    self._question_history.append({
                        "element": s.get("element", ""),
                        "question": s["question"],
                        "addressed": coverage.get(s.get("element", ""), False),
                        "timestamp": now,
                    })
                    existing_texts.add(s["question"])

    def _build_prompt(self, context: str, coverage: dict) -> str:
        """Build the user message combining transcript context and coverage state."""
        coverage_lines = []
        summary = self._tracker.get_summary()
        for element in MEDDPICC_ELEMENTS:
            info = summary.get(element, {})
            if info.get("covered"):
                evidence = info.get("evidence", "")
                coverage_lines.append(f"- {element}: covered (evidence: {evidence})")
            else:
                coverage_lines.append(f"- {element}: uncovered")

        return (
            f"Recent transcript:\n{context}\n\n"
            f"Current MEDDPICC coverage:\n"
            + "\n".join(coverage_lines)
            + "\n\nAnalyze the conversation and suggest 3-5 questions "
            "relevant to the current topic. Prioritize uncovered elements, "
            "but also suggest deepening questions for covered elements "
            "when the conversation naturally touches on them."
        )

    def _run_inference(self) -> None:
        """Background thread: send transcript + coverage to Haiku, parse streaming JSON."""
        self._inference_in_progress = True
        self._last_inference_time = time.time()

        try:
            context = self._buffer.get_context()
            coverage = self._tracker.get_state()
            prompt = self._build_prompt(context, coverage)

            client = boto3.client(
                "bedrock-runtime",
                region_name=AWS_REGION,
                config=Config(
                    read_timeout=MEDDPICC_INFERENCE_TIMEOUT,
                    connect_timeout=10,
                    retries={"max_attempts": 1},
                ),
            )

            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2048,
                "system": MEDDPICC_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            })

            response = client.invoke_model_with_response_stream(
                modelId=HAIKU_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=body,
            )

            full_text = []
            for event in response["body"]:
                chunk = json.loads(event["chunk"]["bytes"])
                if chunk.get("type") == "content_block_delta":
                    text = chunk["delta"].get("text", "")
                    if text:
                        full_text.append(text)

            raw = "".join(full_text)

            # Strip markdown code fences if the model wraps its JSON response
            stripped = raw.strip()
            if stripped.startswith("```"):
                # Remove opening fence (```json or ```)
                stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped[3:]
            if stripped.endswith("```"):
                stripped = stripped[:-3]
            stripped = stripped.strip()

            try:
                result = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("[MEDDPICC] Malformed JSON response: %s", stripped[:200])
                raise

            # Process coverage updates
            for update in result.get("coverage_updates", []):
                # Handle both "covered": true and "status": "partially_covered" / "covered"
                covered = update.get("covered", False)
                if not covered and "status" in update:
                    covered = update["status"] in ("covered", "partially_covered")
                self._tracker.update(
                    update.get("element", ""),
                    covered,
                    update.get("evidence", ""),
                )

            suggestions = result.get("suggestions", [])
            all_covered = result.get("all_covered", False)

            # Record questions in history and update addressed status
            self._record_questions(suggestions)

            # Dispatch UI updates on the main thread
            self._root.after(0, self._on_coverage, self._tracker.get_state())
            self._root.after(0, self._on_suggestions, suggestions)

            # Reset failure state on success
            self._consecutive_failures = 0
            self._current_debounce = MEDDPICC_DEBOUNCE_SECONDS

        except Exception as e:
            logger.error("[MEDDPICC] Inference error (%s): %s", type(e).__name__, e)
            self._consecutive_failures += 1

            if self._consecutive_failures >= MEDDPICC_MAX_CONSECUTIVE_FAILURES:
                self._current_debounce = min(
                    self._current_debounce * 2, MEDDPICC_MAX_DEBOUNCE_SECONDS
                )
                # Reset counter so we get another window of attempts at the new debounce
                self._consecutive_failures = 0
                self._root.after(
                    0,
                    self._on_status,
                    f"⚠️ MEDDPICC coach recovering — next attempt in {int(self._current_debounce)}s",
                )
            else:
                self._root.after(0, self._on_status, "⚠️ Analysis retry pending...")

        finally:
            self._inference_in_progress = False
            if self._queued_inference and self._active:
                self._queued_inference = False
                self._inference_thread = threading.Thread(
                    target=self._run_inference, daemon=True
                )
                self._inference_thread.start()

    def _generate_post_call_summary(self) -> None:
        """Generate a text summary of MEDDPICC coverage after the call ends."""
        try:
            summary = self._tracker.get_summary()
            lines = ["MEDDPICC Coverage Summary", "=" * 30, ""]

            for element in MEDDPICC_ELEMENTS:
                info = summary[element]
                if info["covered"]:
                    evidence = info["evidence"] or "No evidence recorded"
                    lines.append(f"✅ {element}: Covered — {evidence}")
                else:
                    lines.append(f"❌ {element}: Not covered")
                    lines.append(f"   ↳ Follow-up: Ask about {element.lower()} in your next interaction")

            lines.append("")
            covered_count = sum(1 for e in MEDDPICC_ELEMENTS if summary[e]["covered"])
            lines.append(f"Coverage: {covered_count}/8 elements addressed")

            summary_text = "\n".join(lines)
            self._root.after(0, self._on_summary, summary_text)
            self._root.after(0, self._on_status, "")

        except Exception as e:
            logger.error("[MEDDPICC] Post-call summary error: %s", e)
            self._root.after(0, self._on_summary, "MEDDPICC summary unavailable")
            self._root.after(0, self._on_status, "")
