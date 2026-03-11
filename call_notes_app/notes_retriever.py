"""Retrieval agent for historical call notes stored as .docx files.

Scans one or more directories recursively for .docx files, extracts their text,
and uses Claude Opus 4.6 on Bedrock for multi-turn conversation about the content.

The notes context is injected once at the start of the conversation (first user turn).
Subsequent turns pass only the growing message history, keeping latency low.
"""
import json
import os
import threading
import boto3
from botocore.config import Config
from docx import Document
import re
from config import AWS_REGION, NOTES_BASE_DIR, SANGHWA_NOTES_DIR, AYMAN_NOTES_DIR

# Claude Opus 4.6 for deep retrieval reasoning
OPUS_MODEL_ID = "us.anthropic.claude-opus-4-6-v1"

# All indexed sources: (directory_path, display_label)
NOTE_SOURCES = [
    (NOTES_BASE_DIR,      "My Notes"),
    (SANGHWA_NOTES_DIR,   "Sanghwa"),
    (AYMAN_NOTES_DIR,     "Ayman"),
]

RETRIEVAL_SYSTEM_PROMPT = """You are an expert assistant that helps retrieve and synthesize \
information from historical customer call notes.

At the start of each conversation you are given:
1. An explicit index listing every note file you have received (customer, source, filename, date)
2. The full text content of each of those files

CRITICAL RULES:
- You MUST search the full content of every file listed in the index before saying a customer is not found
- If a customer appears in the index, their notes ARE in the context — search carefully
- Customer names may appear in filenames like "[03_03] BQE - Discovery.docx" — the customer is "BQE"
- Never say you cannot find a customer if they appear in the index you were given
- If you genuinely cannot find relevant content after searching, quote the exact filename(s) you checked

Guidelines:
- Always cite which customer, which note file, and which source (My Notes / Sanghwa / Ayman) your answer comes from
- If multiple notes are relevant, synthesize across them
- Be specific: include names, dates, numbers, and commitments mentioned in the notes
- Format your response in clean markdown with clear sections
- Highlight action items, decisions, and follow-ups when relevant
- Remember context from earlier in the conversation — the user may ask follow-up questions \
  that refer back to previous answers"""


def _read_docx_text(filepath: str) -> str:
    """Extract plain text from a .docx file."""
    try:
        doc = Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception as e:
        return f"[Error reading file: {e}]"


# Matches SA-style filenames: [MM_DD] CustomerName - Topic.docx
# Also handles [MM_DD][tag] CustomerName - Topic.docx
_SA_FILENAME_RE = re.compile(
    r"^\[[\d_]+\](?:\[.*?\])?\s*(.+?)\s*(?:-\s*.+)?\.docx$",
    re.IGNORECASE,
)


def _customer_from_filename(fname: str) -> str | None:
    """Extract customer name from SA-style filename, e.g. '[03_07] Classmates - Topic.docx'."""
    m = _SA_FILENAME_RE.match(fname)
    if not m:
        return None
    # The captured group may still have " - Topic" if the regex didn't split it
    # Split on first " - " to isolate just the customer name
    raw = m.group(1)
    customer = raw.split(" - ")[0].strip()
    return customer if customer else None


def _date_from_sa_filename(fname: str) -> str:
    """Extract date hint from SA-style filename bracket, e.g. '[03_07]' -> '03-07'."""
    m = re.match(r"^\[(\d{2})_(\d{2})\]", fname)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


def scan_notes(sources: list[tuple[str, str]] | None = None) -> list[dict]:
    """Scan one or more directories recursively for .docx files.

    Handles two naming conventions:
    - My Notes style: files live in per-customer subfolders
      e.g. Call Notes/RapidAI/RapidAI_notes_1_2025-03-01.docx
    - SA Team style: files named with customer in the filename
      e.g. Sanghwa Customer Docs/2025/[03_07] Classmates - Topic.docx

    Args:
        sources: List of (directory_path, source_label) tuples.
                 Defaults to NOTE_SOURCES (all configured sources).
    """
    if sources is None:
        sources = NOTE_SOURCES

    notes = []
    for base_dir, source_label in sources:
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if not fname.endswith(".docx"):
                    continue
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(root, base_dir)

                # Try SA-style: customer name embedded in filename
                customer = _customer_from_filename(fname)
                date_str = _date_from_sa_filename(fname) if customer else ""

                if not customer:
                    # My Notes style: customer is the immediate subfolder name
                    parts = rel.replace("\\", "/").split("/")
                    customer = parts[0] if parts[0] != "." else os.path.splitext(fname)[0]
                    # Extract date from filename (YYYY-MM-DD pattern)
                    for p in fname.replace(".docx", "").split("_"):
                        if len(p) == 10 and p.count("-") == 2:
                            date_str = p
                            break

                notes.append({
                    "customer": customer,
                    "filename": fname,
                    "filepath": full_path,
                    "date": date_str,
                    "source": source_label,
                })

    return notes


def build_context(notes_meta: list[dict], max_chars: int = 380_000,
                  max_chars_per_note: int = 12_000) -> str:
    """Read note files and build a context string for the first LLM turn.

    Notes are included in the order provided (caller should sort by relevance).
    Each note is capped at max_chars_per_note so no single file crowds out others.
    Total context is capped at max_chars (Claude Opus 4.6 supports ~200k tokens ≈ 800k chars,
    but we stay conservative to leave room for the conversation).
    """
    parts = []
    total = 0
    skipped = 0
    for note in notes_meta:
        text = _read_docx_text(note["filepath"])
        # Cap individual note size so one large file doesn't crowd out others
        if len(text) > max_chars_per_note:
            text = text[:max_chars_per_note] + "\n[...note truncated for length...]"
        header = (
            f"=== CUSTOMER: {note['customer']} | "
            f"SOURCE: {note.get('source', 'unknown')} | "
            f"FILE: {note['filename']} | "
            f"DATE: {note['date'] or 'unknown'} ===\n"
        )
        entry = header + text + "\n\n"
        if total + len(entry) > max_chars:
            skipped += 1
            continue  # skip rather than hard-stop so earlier notes aren't lost
        parts.append(entry)
        total += len(entry)

    if skipped:
        parts.append(f"\n[Note: {skipped} additional file(s) were omitted due to context size limits.]\n")

    return "".join(parts)


def ask_notes_agent(
    question: str,
    notes_meta: list[dict],
    conversation_history: list[dict],
    on_chunk=None,
    callback=None,
):
    """Send a message in a multi-turn conversation about the historical notes.

    Args:
        question: The user's current message.
        notes_meta: List of note file metadata (from scan_notes).
        conversation_history: Mutable list of {"role": ..., "content": ...} dicts.
            Pass an empty list for a new conversation. This list is updated in-place
            with the new user message and assistant reply after each turn.
        on_chunk: Called with each streamed text chunk.
        callback: Called with (full_answer, error) when done.
    """

    def _run():
        try:
            if not notes_meta:
                answer = (
                    "No call notes found in the configured directories.\n\n"
                    f"- My Notes: `{NOTES_BASE_DIR}`\n"
                    f"- Sanghwa: `{SANGHWA_NOTES_DIR}`\n"
                    f"- Ayman: `{AYMAN_NOTES_DIR}`\n\n"
                    "Make sure at least one directory exists and contains .docx files."
                )
                if on_chunk:
                    on_chunk(answer)
                if callback:
                    callback(answer, None)
                return

            # First turn: prepend the notes context to the user message
            if not conversation_history:
                # Sort: put notes whose customer name appears in the question first
                q_lower = question.lower()
                def relevance_key(n):
                    cust = n["customer"].lower()
                    # Exact or partial match in question → highest priority
                    if cust in q_lower or any(word in cust for word in q_lower.split()):
                        return 0
                    return 1

                sorted_notes = sorted(notes_meta, key=relevance_key)
                context = build_context(sorted_notes)

                # Build an index summary so the LLM knows exactly what files it received
                index_lines = "\n".join(
                    f"  - [{n.get('source','?')}] {n['customer']} | {n['filename']} | {n['date'] or 'no date'}"
                    for n in sorted_notes
                )
                user_content = (
                    f"You have been provided with {len(sorted_notes)} call note file(s) "
                    f"from the following sources:\n{index_lines}\n\n"
                    f"Here are the full contents of those notes:\n\n"
                    f"{context}\n\n---\n\n{question}"
                )
            else:
                user_content = question

            # Append the new user turn
            conversation_history.append({"role": "user", "content": user_content})

            client = boto3.client(
                "bedrock-runtime",
                region_name=AWS_REGION,
                config=Config(read_timeout=300),
            )

            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "system": RETRIEVAL_SYSTEM_PROMPT,
                "messages": conversation_history,
            }

            response = client.invoke_model_with_response_stream(
                modelId=OPUS_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(payload),
            )

            full_text = []
            for event in response["body"]:
                chunk = json.loads(event["chunk"]["bytes"])
                if chunk.get("type") == "content_block_delta":
                    text = chunk["delta"].get("text", "")
                    if text:
                        full_text.append(text)
                        if on_chunk:
                            on_chunk(text)

            answer = "".join(full_text)

            # Append the assistant reply to history for the next turn
            conversation_history.append({"role": "assistant", "content": answer})

            if callback:
                callback(answer, None)

        except Exception as e:
            # Remove the user message we just appended so history stays consistent
            if conversation_history and conversation_history[-1]["role"] == "user":
                conversation_history.pop()
            err = f"Error querying notes: {e}"
            if on_chunk:
                on_chunk(err)
            if callback:
                callback(None, err)

    threading.Thread(target=_run, daemon=True).start()
