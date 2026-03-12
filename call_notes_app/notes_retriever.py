"""Retrieval agent client for historical call notes.

Sends a file index + question to the deployed AgentCore retrieval agent.
Falls back to direct Bedrock tool-use if the ARN is not configured.

Scan / index logic lives here; the actual LLM reasoning runs in the agent.
"""
import json
import os
import re
import threading
from datetime import datetime
import boto3
from botocore.config import Config
from config import AWS_REGION, NOTES_BASE_DIR, SANGHWA_NOTES_DIR, AYMAN_NOTES_DIR, RETRIEVAL_AGENT_ARN

OPUS_MODEL_ID   = "us.anthropic.claude-opus-4-6-v1"
SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"   # Sonnet 4 — faster, no thinking overhead

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
    # Strip common domain suffixes
    s = re.sub(r"\.(com|io|ai|co|org|net)$", "", s)
    # Replace & with 'and', underscores with spaces
    s = s.replace("&", "and").replace("_", " ")
    # Remove all non-alphanumeric except spaces
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip trailing plurals
    s = re.sub(r"es$", "", s)
    s = re.sub(r"s$", "", s)
    return s


# Words that indicate a filename/topic leaked through as a customer name
_NOISE_WORDS = {
    "discussion", "call", "discovery", "alignment", "poc", "demo", "meeting",
    "review", "sync", "followup", "follow-up", "notes", "pipeline", "bedrock",
    "sagemaker", "claude", "ai", "ml", "p4", "p5", "ack", "bi", "s3",
    "vectors", "with", "rapid",
}


def _is_likely_customer(name: str) -> bool:
    """Filter out names that are clearly not customer names."""
    s = name.strip()
    if not s or len(s) < 2:
        return False
    # Pure numbers (e.g. "2025")
    if re.match(r"^\d+$", s):
        return False
    # Very long names are usually filenames/topics, not customers
    if len(s) > 40:
        return False
    # Contains multiple underscores — likely a filename stem
    if s.count("_") >= 2:
        return False
    # If more than half the words are noise, it's probably not a customer
    words = re.split(r"[\s_]+", s.lower())
    if len(words) > 2:
        noise_count = sum(1 for w in words if w in _NOISE_WORDS)
        if noise_count >= len(words) / 2:
            return False
    return True


def _edit_distance(a: str, b: str) -> int:
    """Simple Levenshtein distance for short strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def dedupe_customers(names: list[str]) -> dict[str, str]:
    """Return {original_name: canonical_name} merging near-duplicates.

    Strategy:
    1. Normalize names (lowercase, strip punctuation/domains/plurals)
    2. Group exact normalized matches
    3. Merge groups where one normalized form is a prefix of another
       (e.g. "bqe" matches "bqe eba", "boku" matches "boku discovery call")
    4. Pick the shortest original name as canonical
    5. Filter out non-customer names (years, meeting topics, etc.)
    """
    # Filter out obvious non-customers first
    filtered = [n for n in names if _is_likely_customer(n)]

    groups: dict[str, list[str]] = {}
    for name in filtered:
        groups.setdefault(_normalize_customer(name), []).append(name)

    keys = sorted(groups.keys())
    merged: dict[str, str] = {}

    for i, k in enumerate(keys):
        if k in merged:
            continue
        # Split into words for smarter matching
        k_words = k.split()
        k_first = k_words[0] if k_words else k

        for k2 in keys[i + 1:]:
            if k2 in merged:
                continue
            k2_words = k2.split()
            k2_first = k2_words[0] if k2_words else k2

            # Merge if:
            # 1. One is a prefix of the other (any length diff)
            # 2. First word matches and the shorter name is ≤2 words
            #    (catches "BQE" → "BQE EBA", "Boku" → "Boku Discovery Call")
            # 3. Names differ only by spaces/no-spaces
            #    (catches "ClearCaptions" vs "Clear Captions", "Cast and Crew" vs "CastAndCrew")
            should_merge = False

            if k2.startswith(k) and len(k_words) <= 2:
                should_merge = True
            elif k_first == k2_first and len(k_words) == 1:
                should_merge = True
            elif k.replace(" ", "") == k2.replace(" ", ""):
                should_merge = True
            # Catch typos: single-word names differing by ≤2 edits
            elif (len(k_words) == 1 and len(k2_words) == 1
                  and abs(len(k) - len(k2)) <= 2
                  and len(k) >= 4
                  and _edit_distance(k, k2) <= 2):
                should_merge = True
            # Catch typos in multi-word names: same word count, total edit distance ≤2
            elif (len(k_words) == len(k2_words) and len(k_words) >= 2
                  and k_first == k2_first
                  and _edit_distance(k, k2) <= 2):
                should_merge = True

            if should_merge:
                merged[k2] = k
                groups[k].extend(groups.pop(k2, []))

    canonical_map: dict[str, str] = {}
    for key, orig_names in groups.items():
        canonical = min(orig_names, key=lambda n: (len(n), n.lower()))
        for orig in orig_names:
            canonical_map[orig] = canonical

    # Also map filtered-out names to themselves so _get_active_notes still works
    for name in names:
        if name not in canonical_map:
            canonical_map[name] = name

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


# ── AgentCore invocation (streaming SSE) ───────────────────────────────────────

def _invoke_agentcore(runtime_arn: str, payload: dict, on_chunk=None) -> str:
    """Call a deployed AgentCore agent via boto3 invoke_agent_runtime (HTTP).

    The agent streams SSE events. The format alternates between:
      1. Bedrock events:  data: {"event": {"contentBlockDelta": {"delta": {"text": "..."}}}}
      2. Strands repr:    data: "{'data': '...', 'agent': <strands...>}"  (skip these)

    We extract text from the Bedrock contentBlockDelta events and tool names
    from contentBlockStart events.
    """
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

    answer_parts = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        json_str = line[5:].strip()
        if not json_str:
            continue

        # Skip Strands repr strings (start with " and contain Python objects)
        if json_str.startswith('"'):
            continue

        try:
            event = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        # Extract from Bedrock-style events: {"event": {...}}
        inner = event.get("event")
        if isinstance(inner, dict):
            # Text delta: {"contentBlockDelta": {"delta": {"text": "..."}}}
            cbd = inner.get("contentBlockDelta")
            if cbd:
                delta = cbd.get("delta", {})
                text = delta.get("text", "")
                if text:
                    answer_parts.append(text)
                    if on_chunk:
                        on_chunk(text)
                continue

            # Tool start: {"contentBlockStart": {"start": {"toolUse": {"name": "..."}}}}
            cbs = inner.get("contentBlockStart")
            if cbs:
                tool_info = cbs.get("start", {}).get("toolUse", {})
                tool_name = tool_info.get("name", "")
                if tool_name and on_chunk:
                    on_chunk(f"🔧 Using tool: {tool_name}\n")
                continue

            # Skip messageStart, messageStop, contentBlockStop, metadata
            continue

        # Non-event top-level keys (init_event_loop, start, message, etc.) — skip
        # But handle legacy non-streaming fallback: {"answer": "...", "status": "..."}
        if "answer" in event:
            text = event["answer"]
            answer_parts.append(text)
            if on_chunk:
                on_chunk(text)
        elif "result" in event:
            text = str(event["result"])
            # Skip AgentResult repr strings
            if "AgentResult" not in text:
                answer_parts.append(text)
                if on_chunk:
                    on_chunk(text)

    # If no events were parsed, treat the whole response as plain text/JSON
    if not answer_parts:
        try:
            data = json.loads(raw)
            fallback = data.get("answer", data.get("result", data.get("text", raw)))
        except json.JSONDecodeError:
            fallback = raw
        if on_chunk:
            on_chunk(fallback)
        return fallback

    return "".join(answer_parts)


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
            "max_tokens": 64000,
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
                    on_chunk("🔍 Querying retrieval agent...\n")
                payload = {"prompt": question, "file_index": file_index}
                answer = _invoke_agentcore(RETRIEVAL_AGENT_ARN, payload, on_chunk=on_chunk)
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


RESEARCH_SYSTEM_PROMPT = (
    "You are an expert customer research analyst helping an AWS account manager "
    "prepare for customer calls. Today's date is {today}. You have a `web_search` "
    "tool — use it to find current information. Run 2-3 targeted searches, then "
    "synthesize a brief.\n\n"
    "IMPORTANT: When searching, always include the current year ({year}) in your "
    "queries to get the most recent results. For example, search for "
    "'Pramata company news {year}' not just 'Pramata company news'.\n\n"
    "Structure your response with these sections:\n"
    "## 1. Business Overview\n"
    "Company description, products, industry, size, recent news.\n\n"
    "## 2. AI/ML Solutions in Production\n"
    "Any AI/ML capabilities the company has shipped or announced. "
    "If none found, state that clearly.\n\n"
    "## 3. AI/ML Use Cases & Industry Success Stories\n"
    "3-5 high-impact AI/ML use cases relevant to their industry, "
    "referencing AWS services (Bedrock, SageMaker, Textract, etc.) "
    "and real customer success stories where possible.\n\n"
    "## 4. Recommended Talking Points\n"
    "4-6 actionable conversation starters tied to business outcomes "
    "and AWS capabilities. Include at least one about generative AI.\n\n"
    "Cite sources with URLs. Be concise but thorough."
)

# ── Local web search (for research agent) ──────────────────────────────────────

_WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web using DuckDuckGo for current information about a company or topic.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query string"},
        },
        "required": ["query"],
    },
}


def _execute_web_search(query: str) -> str:
    """Run a DuckDuckGo search and return formatted results."""
    import urllib.request
    import urllib.parse

    results = []

    # Primary: ddgs package (formerly duckduckgo-search)
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
    except ImportError:
        # Try old package name as fallback
        try:
            from duckduckgo_search import DDGS as DDGS_Old
            with DDGS_Old() as ddgs:
                results = list(ddgs.text(query, max_results=5))
        except Exception:
            pass
    except Exception:
        pass

    # Fallback: HTML scraper if no results from package
    if not results:
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            blocks = re.findall(r"result__body.*?(?=result__body|$)", html, re.DOTALL)
            for block in blocks[:5]:
                title_m = re.search(r'result__a[^>]*>(.*?)</a>', block, re.DOTALL)
                url_m = re.search(r'result__url[^>]*>\s*(.*?)\s*</span>', block, re.DOTALL)
                snip_m = re.search(r'result__snippet[^>]*>(.*?)</span>', block, re.DOTALL)
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
                link = re.sub(r"<[^>]+>", "", url_m.group(1)).strip() if url_m else ""
                snip = re.sub(r"<[^>]+>", "", snip_m.group(1)).strip() if snip_m else ""
                if title or snip:
                    results.append({"title": title, "href": link, "body": snip})
        except Exception as e2:
            return f"Search error: {e2}"

    if not results:
        return f"No results found for: {query}"

    parts = []
    for r in results:
        title = r.get("title", "")
        link = r.get("href", r.get("url", ""))
        body = r.get("body", r.get("snippet", ""))
        parts.append(f"**{title}**\n{link}\n{body}")
    return f"Results for '{query}':\n\n" + "\n\n---\n\n".join(parts)


def ask_research_agent(
    question: str,
    customer: str,
    conversation_history: list[dict],
    on_chunk=None,
    callback=None,
):
    """Ask the customer research agent (web search).

    Always uses direct Bedrock streaming with local web_search tool
    for real-time token-by-token output and lower latency.
    """
    def _run():
        try:
            # Build the user message
            if not conversation_history:
                user_content = question
                if customer and customer.lower() not in question.lower():
                    user_content = f"Research customer: {customer}\n\n{question}"
            else:
                user_content = question

            conversation_history.append({"role": "user", "content": user_content})

            client = boto3.client("bedrock-runtime", region_name=AWS_REGION,
                                  config=Config(read_timeout=300))
            answer_parts = []

            for _ in range(6):  # max tool-use loops
                payload = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 64000,
                    "system": RESEARCH_SYSTEM_PROMPT.format(
                        today=datetime.now().strftime("%B %d, %Y"),
                        year=datetime.now().strftime("%Y"),
                    ),
                    "messages": conversation_history,
                    "tools": [_WEB_SEARCH_TOOL],
                }
                resp = client.invoke_model_with_response_stream(
                    modelId=SONNET_MODEL_ID,
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
                            raw_input = "".join(cur["_ip"])
                            cur["input"] = json.loads(raw_input) if raw_input.strip() else {}
                        cur.pop("_tp", None)
                        cur.pop("_ip", None)
                        blocks.append(cur)
                        cur = None

                conversation_history.append({"role": "assistant", "content": blocks})

                tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
                if not tool_blocks:
                    break

                tool_results = []
                for tb in tool_blocks:
                    query = tb.get("input", {}).get("query", "")
                    if on_chunk:
                        on_chunk(f"🔍 Searching: {query}\n")
                    result = _execute_web_search(query)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb["id"],
                        "content": result,
                    })

                conversation_history.append({"role": "user", "content": tool_results})

            answer = "".join(answer_parts)
            if not answer.strip():
                answer = "⚠️ No response generated."
                if on_chunk:
                    on_chunk(answer)

            if callback:
                callback(answer, None)

        except Exception as e:
            if conversation_history and conversation_history[-1]["role"] == "user":
                conversation_history.pop()
            import traceback
            err = f"Error: {e}\n\n```\n{traceback.format_exc()}\n```"
            if on_chunk:
                on_chunk(err)
            if callback:
                callback(None, str(e))

    threading.Thread(target=_run, daemon=True).start()
