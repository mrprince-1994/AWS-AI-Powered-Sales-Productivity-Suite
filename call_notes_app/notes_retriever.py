"""Retrieval agent for historical call notes stored as .docx files.

Scans NOTES_BASE_DIR recursively for .docx files, extracts their text,
and uses Claude Opus 4 on Bedrock to answer questions about the content.
"""
import json
import os
import threading
import boto3
from botocore.config import Config
from docx import Document
from config import AWS_REGION, NOTES_BASE_DIR

# Claude Opus 4.5 for deep retrieval reasoning
OPUS_MODEL_ID = "us.anthropic.claude-opus-4-5-20251101-v1:0"

RETRIEVAL_SYSTEM_PROMPT = """You are an expert assistant that helps retrieve and synthesize \
information from historical customer call notes.

You will be given a collection of call notes from past customer meetings, each labeled with \
the customer name, filename, and date. Your job is to answer questions about these notes \
accurately and helpfully.

Guidelines:
- Always cite which customer and which note file your answer comes from
- If multiple notes are relevant, synthesize across them
- If you cannot find relevant information in the provided notes, say so clearly
- Be specific: include names, dates, numbers, and commitments mentioned in the notes
- Format your response in clean markdown with clear sections
- If asked about a specific customer, focus on their notes
- Highlight action items, decisions, and follow-ups when relevant"""


def _read_docx_text(filepath: str) -> str:
    """Extract plain text from a .docx file."""
    try:
        doc = Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception as e:
        return f"[Error reading file: {e}]"


def scan_notes(base_dir: str = NOTES_BASE_DIR) -> list[dict]:
    """Scan the notes directory and return metadata for all .docx files."""
    notes = []
    if not os.path.isdir(base_dir):
        return notes

    for root, dirs, files in os.walk(base_dir):
        # Skip hidden/system dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in sorted(files):
            if not fname.endswith(".docx"):
                continue
            full_path = os.path.join(root, fname)
            # Customer name is the subfolder name relative to base_dir
            rel = os.path.relpath(root, base_dir)
            customer = rel if rel != "." else os.path.splitext(fname)[0]
            # Try to extract date from filename (format: name_notes_N_YYYY-MM-DD_HH-MM.docx)
            date_str = ""
            parts = fname.replace(".docx", "").split("_")
            for i, p in enumerate(parts):
                if len(p) == 10 and p.count("-") == 2:
                    date_str = p
                    break
            notes.append({
                "customer": customer,
                "filename": fname,
                "filepath": full_path,
                "date": date_str,
            })

    return notes


def build_context(notes_meta: list[dict], max_chars: int = 180_000) -> str:
    """Read note files and build a context string for the LLM."""
    parts = []
    total = 0
    for note in notes_meta:
        text = _read_docx_text(note["filepath"])
        header = (
            f"=== CUSTOMER: {note['customer']} | "
            f"FILE: {note['filename']} | "
            f"DATE: {note['date'] or 'unknown'} ===\n"
        )
        entry = header + text + "\n\n"
        if total + len(entry) > max_chars:
            # Truncate this entry to fit
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
    on_chunk=None,
    callback=None,
):
    """Ask a question about the historical notes. Streams response via on_chunk."""

    def _run():
        try:
            if not notes_meta:
                answer = (
                    "No call notes found in the configured directory.\n\n"
                    f"Expected location: `{NOTES_BASE_DIR}`\n\n"
                    "Make sure you have saved at least one call session first."
                )
                if on_chunk:
                    on_chunk(answer)
                if callback:
                    callback(answer, None)
                return

            context = build_context(notes_meta)

            client = boto3.client(
                "bedrock-runtime",
                region_name=AWS_REGION,
                config=Config(read_timeout=300),
            )

            user_message = (
                f"Here are the historical call notes:\n\n{context}\n\n"
                f"---\n\nQuestion: {question}"
            )

            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "system": RETRIEVAL_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
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
            if callback:
                callback(answer, None)

        except Exception as e:
            err = f"Error querying notes: {e}"
            if on_chunk:
                on_chunk(err)
            if callback:
                callback(None, err)

    threading.Thread(target=_run, daemon=True).start()
