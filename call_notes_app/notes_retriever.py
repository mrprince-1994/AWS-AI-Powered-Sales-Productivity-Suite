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
from config import AWS_REGION, NOTES_BASE_DIR, SA_NOTES_DIR

# Claude Opus 4.6 for deep retrieval reasoning
OPUS_MODEL_ID = "us.anthropic.claude-opus-4-6-v1"

# All indexed sources: (directory_path, display_label)
NOTE_SOURCES = [
    (NOTES_BASE_DIR, "My Notes"),
    (SA_NOTES_DIR,   "SA Team"),
]

RETRIEVAL_SYSTEM_PROMPT = """You are an expert assistant that helps retrieve and synthesize \
information from historical customer call notes.

At the start of each conversation you are given a collection of call notes from past customer \
meetings, each labeled with the customer name, filename, date, and source (who wrote the notes). \
Use these notes as your primary source of truth throughout the conversation.

Guidelines:
- Always cite which customer, which note file, and which source (My Notes / SA Team) your answer comes from
- If multiple notes are relevant, synthesize across them
- If you cannot find relevant information in the provided notes, say so clearly
- Be specific: include names, dates, numbers, and commitments mentioned in the notes
- Format your response in clean markdown with clear sections
- If asked about a specific customer, focus on their notes
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


def scan_notes(sources: list[tuple[str, str]] | None = None) -> list[dict]:
    """Scan one or more directories and return metadata for all .docx files.

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
                customer = rel if rel != "." else os.path.splitext(fname)[0]
                date_str = ""
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


def build_context(notes_meta: list[dict], max_chars: int = 180_000) -> str:
    """Read note files and build a context string for the first LLM turn."""
    parts = []
    total = 0
    for note in notes_meta:
        text = _read_docx_text(note["filepath"])
        header = (
            f"=== CUSTOMER: {note['customer']} | "
            f"SOURCE: {note.get('source', 'unknown')} | "
            f"FILE: {note['filename']} | "
            f"DATE: {note['date'] or 'unknown'} ===\n"
        )
        entry = header + text + "\n\n"
        if total + len(entry) > max_chars:
            remaining = max_chars - total - len(header) - 100
            if remaining > 200:
                entry = header + text[:remaining] + "\n[...truncated...]\n\n"
            else:
                break
        parts.append(entry)
        total += len(entry)

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
                    f"- SA Team: `{SA_NOTES_DIR}`\n\n"
                    "Make sure at least one directory exists and contains .docx files."
                )
                if on_chunk:
                    on_chunk(answer)
                if callback:
                    callback(answer, None)
                return

            # First turn: prepend the notes context to the user message
            if not conversation_history:
                context = build_context(notes_meta)
                user_content = (
                    f"Here are the historical call notes for this conversation:\n\n"
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
