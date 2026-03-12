"""Retrieval agent client for historical call notes.

Sends a file index + question to the deployed AgentCore retrieval agent.
Falls back to direct Bedrock tool-use if the ARN is not configured.

Scan / index logic lives here; the actual LLM reasoning runs in the agent.
"""
import json
import os
import re
import threading
import boto3
from botocore.config import Config
from config import AWS_REGION, NOTES_BASE_DIR, SANGHWA_NOTES_DIR, AYMAN_NOTES_DIR, RETRIEVAL_AGENT_ARN, RESEARCH_AGENT_ARN

OPUS_MODEL_ID   = "us.anthropic.claude-opus-4-6-v1"
SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

NOTE_SOURCES = [
    (NOTES_BASE_DIR,    "My Notes"),
    (SANGHWA_NOTES_DIR, "Sanghwa"),
    (AYMAN_NOTES_DIR,   "Ayman"),
]

# ── Filename helpers ───────────────────────────────────────────────────────────

_SA_FILENAME_RE = re.compile(
    r"^\[[\d_]+\](?:\[.*?\])?\s*(.+?)\s*(?:-\s*.+)?\.(?:docx|md)$",
    re.IGNORECASE,
)


def _customer_from_filename(fname: str) -> str | None:
    m = _SA_FILENAME_RE.match(fname)
    if not m:
        return None
    return m.group(1).split(" - ")[0].strip() or None


def _date_from_sa_filename(fname: str) -> str:
    m = re.match(r"^\[(\d{2})_(\d{2})\]", fname)
    return f"{m.group(1)}-{m.group(2)}" if m else ""


# ── Customer deduplication ─────────────────────────────────────────────────────

def _normalize_customer(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"es$", "", s)
    s = re.sub(r"s$", "", s)
    return s


def dedupe_customers(names: list[str]) -> dict[str, str]:
    """Return {original_name: canonical_name} merging near-duplicates."""
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(_normalize_customer(name), []).append(name)

    keys = sorted(groups.keys())
    merged: dict[str, str] = {}
    for i, k in enumerate(keys):
        if k in merged:
            continue
        for k2 in keys[i + 1:]:
            if k2 in merged:
                continue
            if k2.startswith(k) and len(k2) - len(k) <= 3:
                merged[k2] = k
                groups[k].extend(groups.pop(k2, []))

    canonical_map: dict[str, str] = {}
    for key, orig_names in groups.items():
        canonical = min(orig_names, key=lambda n: (len(n), n.lower()))
        for orig in orig_names:
            canonical_map[orig] = canonical
    return canonical_map


# ── Directory scanning ─────────────────────────────────────────────────────────

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
                    for p in re.sub(r"\.(docx|md)$", "", fname, flags=re.I).split("_"):
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


# ── Index helpers ──────────────────────────────────────────────────────────────

def _build_file_index(notes_meta: list[dict], question: str = "") -> list[dict]:
    """Sort by relevance and return a serialisable index list (no filepath for remote)."""
    q_lower = question.lower()
    q_words = set(w for w in q_lower.split() if len(w) > 3)

    def _relevance(n):
        cust = n["customer"].lower()
        if cust in q_lower:
            return 0
        if any(w in cust for w in q_words):
            return 1
        return 2

    sorted_notes = sorted(notes_meta, key=_relevance)[:200]
    return [
        {
            "file_id":  f"file_{i}",
            "customer": n["customer"],
            "source":   n.get("source", "?"),
            "filename": n["filename"],
            "date":     n.get("date", ""),
            "filepath": n["filepath"],   # needed by local fallback & agent on same machine
        }
        for i, n in enumerate(sorted_notes)
    ]


# ── AgentCore invocation ───────────────────────────────────────────────────────

def _invoke_agentcore(runtime_arn: str, payload: dict) -> str:
    """Call a deployed AgentCore agent via boto3 invoke_agent_runtime (HTTP)."""
    client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier="DEFAULT",
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp.get("response", b"")
    if hasattr(body, "read"):
        body = body.read()
    raw = body.decode("utf-8") if isinstance(body, bytes) else body
    try:
        data = json.loads(raw)
        return data.get("answer", data.get("result", data.get("text", raw)))
    except json.JSONDecodeError:
        return raw


# ── Local fallback (direct Bedrock tool-use) ───────────────────────────────────

def _local_retrieval(question: str, file_index: list[dict], conversation_history: list[dict],
                     on_chunk=None) -> str:
    """Direct Bedrock tool-use loop — used when RETRIEVAL_AGENT_ARN is not set."""
    from docx import Document as _DocxDocument

    READ_TOOL = {
        "name": "read_note_file",
        "description": "Read the full text of a call note file by file_id.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    }

    file_map = {e["file_id"]: e for e in file_index}

    def _read(entry):
        fp = entry.get("filepath", "")
        if not fp or not os.path.isfile(fp):
            return f"File not found: {fp}"
        try:
            if fp.lower().endswith(".docx"):
                doc = _DocxDocument(fp)
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            return f"Error: {e}"

    index_lines = "\n".join(
        f"  {e['file_id']}: [{e.get('source','?')}] customer={e['customer']} "
        f"date={e.get('date') or 'unknown'} filename={e['filename']}"
        for e in file_index
    )

    if not conversation_history:
        user_content = f"Available files ({len(file_index)}):\n{index_lines}\n\n---\n\n{question}"
    else:
        user_content = question

    conversation_history.append({"role": "user", "content": user_content})

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION,
                          config=Config(read_timeout=300))
    answer_parts = []

    for _ in range(10):
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "system": (
                "You are a retrieval assistant for historical call notes. "
                "Use read_note_file to fetch files, then answer the question. "
                "Cite customer, source, and filename in your answer."
            ),
            "messages": conversation_history,
            "tools": [READ_TOOL],
        }
        resp = client.invoke_model_with_response_stream(
            modelId=OPUS_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload),
        )

        blocks, cur = [], None
        for event in resp["body"]:
            chunk = json.loads(event["chunk"]["bytes"])
            t = chunk.get("type")
            if t == "content_block_start":
                cur = {**chunk.get("content_block", {}), "_tp": [], "_ip": []}
            elif t == "content_block_delta":
                d = chunk.get("delta", {})
                if d.get("type") == "text_delta":
                    txt = d.get("text", "")
                    if txt:
                        cur["_tp"].append(txt)
                        answer_parts.append(txt)
                        if on_chunk:
                            on_chunk(txt)
                elif d.get("type") == "input_json_delta":
                    cur["_ip"].append(d.get("partial_json", ""))
            elif t == "content_block_stop" and cur:
                if cur.get("type") == "text":
                    cur["text"] = "".join(cur["_tp"])
                elif cur.get("type") == "tool_use":
                    raw = "".join(cur["_ip"])
                    cur["input"] = json.loads(raw) if raw.strip() else {}
                cur.pop("_tp", None); cur.pop("_ip", None)
                blocks.append(cur)
                cur = None
            elif t == "error":
                raise RuntimeError(f"API error: {chunk}")

        conversation_history.append({"role": "assistant", "content": blocks})

        tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
        if not tool_blocks:
            break

        tool_results = []
        for tb in tool_blocks:
            fid = tb.get("input", {}).get("file_id", "")
            entry = file_map.get(fid)
            content = (
                f"=== {entry['customer']} | {entry['source']} | "
                f"{entry['filename']} | {entry.get('date') or 'no date'} ===\n\n"
                + _read(entry)
            ) if entry else f"file_id '{fid}' not found."
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tb["id"],
                "content": content,
            })
            if on_chunk:
                on_chunk(f"📂 Reading: {entry['filename'] if entry else fid}\n\n")

        conversation_history.append({"role": "user", "content": tool_results})

    return "".join(answer_parts)


# ── Public API ─────────────────────────────────────────────────────────────────

def ask_notes_agent(
    question: str,
    notes_meta: list[dict],
    conversation_history: list[dict],
    on_chunk=None,
    callback=None,
):
    """Ask the retrieval agent about historical notes.

    Uses the deployed AgentCore agent if RETRIEVAL_AGENT_ARN is set,
    otherwise falls back to direct Bedrock tool-use.
    """
    def _run():
        try:
            if not notes_meta:
                msg = (
                    "No call notes found.\n\n"
                    f"- My Notes: `{NOTES_BASE_DIR}`\n"
                    f"- Sanghwa: `{SANGHWA_NOTES_DIR}`\n"
                    f"- Ayman: `{AYMAN_NOTES_DIR}`"
                )
                if on_chunk: on_chunk(msg)
                if callback: callback(msg, None)
                return

            file_index = _build_file_index(notes_meta, question)

            if RETRIEVAL_AGENT_ARN:
                if on_chunk:
                    on_chunk("🔍 Querying retrieval agent...")
                payload = {"prompt": question, "file_index": file_index}
                if on_chunk:
                    on_chunk(f"📂 Sending {len(file_index)} files to agent...")
                answer = _invoke_agentcore(RETRIEVAL_AGENT_ARN, payload)
                if on_chunk:
                    on_chunk(answer)
                # Keep history consistent for multi-turn
                conversation_history.append({"role": "user", "content": question})
                conversation_history.append({"role": "assistant", "content": answer})
            else:
                answer = _local_retrieval(question, file_index, conversation_history,
                                          on_chunk=on_chunk)

            if not answer.strip():
                answer = "⚠️ No response generated. Try rephrasing your question."
                if on_chunk: on_chunk(answer)

            if callback: callback(answer, None)

        except Exception as e:
            if conversation_history and conversation_history[-1]["role"] == "user":
                conversation_history.pop()
            import traceback
            err = f"Error: {e}\n\n```\n{traceback.format_exc()}\n```"
            if on_chunk: on_chunk(err)
            if callback: callback(None, str(e))

    threading.Thread(target=_run, daemon=True).start()


def ask_research_agent(
    question: str,
    customer: str,
    conversation_history: list[dict],
    on_chunk=None,
    callback=None,
):
    """Ask the customer research agent (web search).

    Uses the deployed AgentCore research agent if RESEARCH_AGENT_ARN is set,
    otherwise falls back to direct Bedrock (no web search tools).
    """
    def _run():
        try:
            if RESEARCH_AGENT_ARN:
                if on_chunk:
                    on_chunk("🌐 Querying research agent...\n\n")
                payload = {"prompt": question, "customer": customer}
                answer = _invoke_agentcore(RESEARCH_AGENT_ARN, payload)
                if on_chunk:
                    on_chunk(answer)
                conversation_history.append({"role": "user", "content": question})
                conversation_history.append({"role": "assistant", "content": answer})
            else:
                # Fallback: Bedrock without web search
                if not conversation_history:
                    user_content = (
                        f"You are a customer research assistant. The customer is: {customer}\n\n"
                        f"{question}\n\n"
                        "(Note: web search tools are not available in this mode. "
                        "Answer from your training knowledge and note any limitations.)"
                    )
                else:
                    user_content = question

                conversation_history.append({"role": "user", "content": user_content})

                client = boto3.client("bedrock-runtime", region_name=AWS_REGION,
                                      config=Config(read_timeout=120))
                payload_body = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4096,
                    "system": (
                        "You are an expert customer research analyst helping an AWS account manager. "
                        "Provide structured research briefs covering company overview, recent news, "
                        "tech stack, AWS relevance, and talking points."
                    ),
                    "messages": conversation_history,
                }
                resp = client.invoke_model_with_response_stream(
                    modelId=SONNET_MODEL_ID,
                    contentType="application/json",
                    accept="application/json",
                    body=json.dumps(payload_body),
                )
                parts = []
                for event in resp["body"]:
                    chunk = json.loads(event["chunk"]["bytes"])
                    if chunk.get("type") == "content_block_delta":
                        txt = chunk["delta"].get("text", "")
                        if txt:
                            parts.append(txt)
                            if on_chunk: on_chunk(txt)
                answer = "".join(parts)
                conversation_history.append({"role": "assistant", "content": answer})

            if not answer.strip():
                answer = "⚠️ No response generated."
                if on_chunk: on_chunk(answer)

            if callback: callback(answer, None)

        except Exception as e:
            if conversation_history and conversation_history[-1]["role"] == "user":
                conversation_history.pop()
            import traceback
            err = f"Error: {e}\n\n```\n{traceback.format_exc()}\n```"
            if on_chunk: on_chunk(err)
            if callback: callback(None, str(e))

    threading.Thread(target=_run, daemon=True).start()
