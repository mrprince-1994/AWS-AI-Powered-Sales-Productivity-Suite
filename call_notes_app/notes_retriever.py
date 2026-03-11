"""Retrieval agent for historical call notes (.docx and .md files).

Uses an agentic tool-use loop:
  1. Build a lightweight index of all files (filename, customer, date, source, path)
  2. Send the index + user question to Claude Opus 4.6 with a `read_note_file` tool
  3. Claude calls the tool for whichever files it needs — we read and return them
  4. Loop until Claude stops calling tools and produces a final answer

This avoids dumping all file content upfront and scales to large note collections.
The 1M context window beta header is included so Claude can handle large reads.
"""
import json
import os
import threading
import boto3
from botocore.config import Config
from docx import Document
import re
from config import AWS_REGION, NOTES_BASE_DIR, SANGHWA_NOTES_DIR, AYMAN_NOTES_DIR

OPUS_MODEL_ID = "us.anthropic.claude-opus-4-6-v1"

NOTE_SOURCES = [
    (NOTES_BASE_DIR,    "My Notes"),
    (SANGHWA_NOTES_DIR, "Sanghwa"),
    (AYMAN_NOTES_DIR,   "Ayman"),
]

# Beta flag to unlock the 1M token context window
CONTEXT_1M_BETA = "context-1m-2025-08-07"

RETRIEVAL_SYSTEM_PROMPT = """You are an expert assistant that retrieves and synthesizes \
information from historical customer call notes.

You have access to a `read_note_file` tool. Use it to read the content of specific note files.

Workflow:
1. You will receive an index of all available note files (filename, customer, source, date, file_id)
2. Based on the user's question, identify which files are relevant by their filename/customer/date
3. Call `read_note_file` with the file_id(s) of the relevant files to read their content
4. Synthesize the content and answer the user's question

Guidelines:
- Always cite the customer name, filename, and source (My Notes / Sanghwa / Ayman)
- Be specific: include names, dates, numbers, action items, and commitments from the notes
- If multiple files are relevant, read all of them before answering
- Format responses in clean markdown with clear sections
- Remember context from earlier in the conversation for follow-up questions
- If no files seem relevant to the question, say so and list what customers ARE available"""

# Tool definition for Claude
READ_NOTE_TOOL = {
    "name": "read_note_file",
    "description": (
        "Read the full text content of a specific call note file. "
        "Use the file_id from the index provided in the conversation. "
        "Call this for each file you need to read before answering."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "The file_id from the note index (e.g. 'file_0', 'file_1', ...)"
            }
        },
        "required": ["file_id"]
    }
}


# ── Filename patterns ──────────────────────────────────────────────────────────

_SA_FILENAME_RE = re.compile(
    r"^\[[\d_]+\](?:\[.*?\])?\s*(.+?)\s*(?:-\s*.+)?\.(?:docx|md)$",
    re.IGNORECASE,
)


def _customer_from_filename(fname: str) -> str | None:
    m = _SA_FILENAME_RE.match(fname)
    if not m:
        return None
    raw = m.group(1)
    customer = raw.split(" - ")[0].strip()
    return customer if customer else None


def _date_from_sa_filename(fname: str) -> str:
    m = re.match(r"^\[(\d{2})_(\d{2})\]", fname)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


# ── File reading ───────────────────────────────────────────────────────────────

def _read_docx_text(filepath: str) -> str:
    try:
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        return f"[Error reading .docx: {e}]"


def _read_md_text(filepath: str) -> str:
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"[Error reading .md: {e}]"


def _read_file(filepath: str) -> str:
    if filepath.lower().endswith(".docx"):
        return _read_docx_text(filepath)
    return _read_md_text(filepath)


# ── Index scanning ─────────────────────────────────────────────────────────────

def scan_notes(sources: list[tuple[str, str]] | None = None) -> list[dict]:
    """Scan directories recursively for .docx and .md note files."""
    if sources is None:
        sources = NOTE_SOURCES

    notes = []
    for base_dir, source_label in sources:
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if not (fname.endswith(".docx") or fname.endswith(".md")):
                    continue
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(root, base_dir)

                customer = _customer_from_filename(fname)
                date_str = _date_from_sa_filename(fname) if customer else ""

                if not customer:
                    parts = rel.replace("\\", "/").split("/")
                    customer = parts[0] if parts[0] != "." else os.path.splitext(fname)[0]
                    for p in fname.replace(".docx", "").replace(".md", "").split("_"):
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


def _build_index_text(notes_meta: list[dict], max_entries: int = 200) -> str:
    """Build a compact index string listing available files.

    Caps at max_entries to keep the index prompt manageable.
    Files are assumed to be pre-sorted by relevance by the caller.
    """
    total = len(notes_meta)
    shown = notes_meta[:max_entries]
    lines = [f"Available note files ({total} total, showing {len(shown)}):\n"]
    for i, note in enumerate(shown):
        lines.append(
            f"  file_{i}: [{note.get('source','?')}] customer={note['customer']} "
            f"date={note['date'] or 'unknown'} filename={note['filename']}"
        )
    if total > max_entries:
        lines.append(f"\n  ... and {total - max_entries} more files not shown. "
                     f"Use the Customer filter in the UI to narrow results.")
    return "\n".join(lines)


# ── Agentic retrieval loop ─────────────────────────────────────────────────────

def ask_notes_agent(
    question: str,
    notes_meta: list[dict],
    conversation_history: list[dict],
    on_chunk=None,
    callback=None,
):
    """Multi-turn agentic retrieval using tool use.

    On the first turn, injects a file index. Claude then calls read_note_file
    for whichever files it needs. We execute the tool calls and loop until
    Claude produces a final text response.
    """

    def _run():
        try:
            if not notes_meta:
                msg = (
                    "No call notes found in the configured directories.\n\n"
                    f"- My Notes: `{NOTES_BASE_DIR}`\n"
                    f"- Sanghwa: `{SANGHWA_NOTES_DIR}`\n"
                    f"- Ayman: `{AYMAN_NOTES_DIR}`\n\n"
                    "Make sure at least one directory exists and contains .docx or .md files."
                )
                if on_chunk:
                    on_chunk(msg)
                if callback:
                    callback(msg, None)
                return

            # Build file_id → metadata map
            # Sort by relevance to the question so matching files get low IDs
            # and are within the index cap
            q_lower = question.lower()
            q_words = set(q_lower.split())

            def _relevance(n):
                cust = n["customer"].lower()
                if cust in q_lower:
                    return 0  # exact customer name in question
                if any(w in cust for w in q_words if len(w) > 3):
                    return 1  # partial word match
                return 2

            sorted_notes = sorted(notes_meta, key=_relevance)
            file_map = {f"file_{i}": note for i, note in enumerate(sorted_notes)}

            # First turn: inject the index
            if not conversation_history:
                index_text = _build_index_text(sorted_notes)
                user_content = f"{index_text}\n\n---\n\n{question}"
            else:
                user_content = question

            conversation_history.append({"role": "user", "content": user_content})

            client = boto3.client(
                "bedrock-runtime",
                region_name=AWS_REGION,
                config=Config(read_timeout=300),
            )

            # Agentic loop — keep going while Claude calls tools
            final_answer_parts = []
            MAX_TOOL_ROUNDS = 10

            for _round in range(MAX_TOOL_ROUNDS):
                payload = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 8192,
                    "system": RETRIEVAL_SYSTEM_PROMPT,
                    "messages": conversation_history,
                    "tools": [READ_NOTE_TOOL],
                }

                # Stream the response
                response = client.invoke_model_with_response_stream(
                    modelId=OPUS_MODEL_ID,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(payload),
                )

                # Collect the full response, streaming text chunks as they arrive
                content_blocks = []
                current_block = None
                stop_reason = None

                for event in response["body"]:
                    chunk = json.loads(event["chunk"]["bytes"])
                    etype = chunk.get("type")

                    if etype == "message_start":
                        pass

                    elif etype == "content_block_start":
                        current_block = chunk.get("content_block", {})
                        current_block["_text_parts"] = []
                        current_block["_input_parts"] = []

                    elif etype == "content_block_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                current_block["_text_parts"].append(text)
                                final_answer_parts.append(text)
                                if on_chunk:
                                    on_chunk(text)
                        elif delta.get("type") == "input_json_delta":
                            current_block["_input_parts"].append(
                                delta.get("partial_json", ""))

                    elif etype == "content_block_stop":
                        if current_block:
                            if current_block.get("type") == "text":
                                current_block["text"] = "".join(
                                    current_block["_text_parts"])
                            elif current_block.get("type") == "tool_use":
                                raw_input = "".join(current_block["_input_parts"])
                                current_block["input"] = json.loads(raw_input) if raw_input.strip() else {}
                            current_block.pop("_text_parts", None)
                            current_block.pop("_input_parts", None)
                            content_blocks.append(current_block)
                        current_block = None

                    elif etype == "message_delta":
                        stop_reason = chunk.get("delta", {}).get("stop_reason")

                    elif etype == "error":
                        # Surface API-level errors (e.g. unsupported beta flag)
                        raise RuntimeError(f"API error: {chunk}")

                # Append assistant turn to history
                conversation_history.append({
                    "role": "assistant",
                    "content": content_blocks,
                })

                # If no tool calls, we're done
                tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
                if not tool_use_blocks:
                    break

                # Notify user which files are being read
                file_names = []
                for tb in tool_use_blocks:
                    fid = tb.get("input", {}).get("file_id", "")
                    note = file_map.get(fid)
                    if note:
                        file_names.append(note["filename"])
                if file_names and on_chunk:
                    on_chunk(f"📂 Reading: {', '.join(file_names)}\n\n")

                # Execute tool calls and build tool_result turn
                tool_results = []
                for tb in tool_use_blocks:
                    file_id = tb.get("input", {}).get("file_id", "")
                    note = file_map.get(file_id)
                    if note:
                        content = _read_file(note["filepath"])
                        result_text = (
                            f"=== {note['customer']} | {note['source']} | "
                            f"{note['filename']} | {note['date'] or 'no date'} ===\n\n"
                            f"{content}"
                        )
                    else:
                        result_text = f"Error: file_id '{file_id}' not found in index."

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb["id"],
                        "content": result_text,
                    })

                conversation_history.append({
                    "role": "user",
                    "content": tool_results,
                })

            answer = "".join(final_answer_parts)
            if not answer.strip():
                answer = "⚠️ No response generated. The model may have only used tools without producing a final answer. Try rephrasing your question."
                if on_chunk:
                    on_chunk(answer)
            if callback:
                callback(answer, None)

        except Exception as e:
            if conversation_history and conversation_history[-1]["role"] == "user":
                conversation_history.pop()
            import traceback
            err = f"Error querying notes: {e}\n\n```\n{traceback.format_exc()}\n```"
            if on_chunk:
                on_chunk(err)
            if callback:
                callback(None, str(e))

    threading.Thread(target=_run, daemon=True).start()
