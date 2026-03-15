import json
import boto3
from botocore.config import Config
from config import AWS_REGION, CLAUDE_MODEL_ID

SYSTEM_PROMPT = """You are an expert meeting note-taker. Given a raw transcript of a call,
produce comprehensive, detailed, and well-organized notes. Capture EVERYTHING of substance
from the conversation — do not summarize or condense. Your goal is to create a complete
written record that someone who missed the call could read and be fully up to speed.

Structure the notes as follows:

## Meeting Context
Who was on the call, the purpose, and any relevant background mentioned.

## Detailed Discussion Notes
This is the most important section. Write a thorough, chronological account organized
by topic. For EACH topic discussed:

1. Use a numbered heading for each major topic (e.g. "1. GitHub Repository Access")
2. Under each topic, write FULL PARAGRAPHS — not just terse bullets. Explain:
   - What was said and WHO said it (attribute statements to speakers)
   - The CONTEXT and REASONING behind statements — why something was brought up,
     what problem it solves, what the background is
   - The back-and-forth of the conversation — if someone asked a question and
     another person responded, capture both sides with the nuance
   - Specific examples, demos, or walkthroughs that were shown
   - Technical details: system names, workflows, architectures, data flows,
     numbers, metrics, timelines, versions, URLs, file names
   - Concerns, caveats, or edge cases that were raised
   - How the discussion evolved — if the group changed direction or refined
     their understanding, capture that progression

3. Use sub-bullets only for lists of discrete items (e.g. a list of features,
   a set of requirements). For narrative discussion, use full sentences and paragraphs.

4. Include direct quotes for important commitments, opinions, or colorful phrasing
   (e.g. James said: "This is really a chain of title workflow, not just Q&A")

5. Do NOT over-compress. If someone spent 3 minutes explaining how a workflow operates,
   that should be a full paragraph in the notes, not a single bullet point.
   A 30-minute call should produce at least 2-3 pages of detailed discussion notes.

## Decisions & Agreements
Every decision made or agreement reached, with the reasoning behind it if discussed.

## Action Items & Owners
A numbered list of every action item, who is responsible, and any deadlines mentioned.

## Open Questions & Unresolved Items
Anything that was raised but not resolved, or that needs further investigation.

## Follow-Up & Next Steps
Planned follow-ups, next meetings, or milestones discussed.

## Key Quotes & Verbatim Notes
Any particularly important statements, commitments, or notable quotes worth preserving.

## Additional Context
Background information, references to documents/tools/systems, or anything else
that provides useful context for future reference.

Be exhaustive. It is better to include too much detail than too little. Preserve specifics,
names, numbers, and technical details exactly as stated."""


def generate_notes(transcript: str, customer_name: str, on_chunk=None) -> str:
    """Send transcript to Claude on Bedrock and stream back comprehensive notes."""
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(read_timeout=300),
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 64000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Customer/Meeting: {customer_name}\n\nRaw Transcript:\n{transcript}",
            }
        ],
    }
    response = client.invoke_model_with_response_stream(
        modelId=CLAUDE_MODEL_ID,
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

    return "".join(full_text)

EMAIL_SYSTEM_PROMPT = """You are a professional follow-up email writer for business meetings.
Given a raw transcript of a call, generate a polished follow-up email that can be sent to the
attendees. The email should:

1. Start with a warm greeting and thank them for their time
2. Briefly recap the key topics discussed (2-3 sentences max per topic — this is a summary, not the full notes)
3. Clearly list any action items with owners and deadlines
4. Mention agreed-upon next steps and any follow-up meetings
5. Close professionally with an offer to clarify anything

Keep the tone professional but warm — like a Solutions Architect following up with a customer.

CRITICAL FORMATTING RULES — this email will be pasted directly into Outlook:
- Do NOT use any markdown formatting whatsoever. No **, no *, no ##, no __, no backticks.
- For section headers, just write them on their own line in plain text (e.g. "Action Items")
- For bullet lists, use a simple dash followed by a space (e.g. "- Item here")
- For emphasis, rely on word choice and sentence structure, NOT formatting characters
- The output must be 100% plain text with zero special formatting syntax

Keep it concise — ideally under 300 words. Do NOT include a subject line in the
body — just the email body starting with the greeting.

Also generate a suggested subject line on the very first line in this format:
Subject: <your suggested subject line>

Then a blank line, then the email body."""


def generate_followup_email(transcript: str, customer_name: str, on_chunk=None) -> str:
    """Generate a follow-up email from a call transcript using Claude on Bedrock."""
    import os

    # Load personal style guide if available
    style_guide = ""
    style_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "style_guide.txt")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            style_guide = f.read().strip()

    system = EMAIL_SYSTEM_PROMPT
    if style_guide:
        system += "\n\nIMPORTANT — Write in the author's personal style described below:\n\n" + style_guide

    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(read_timeout=300),
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": system,
        "messages": [
            {
                "role": "user",
                "content": f"Customer: {customer_name}\n\nCall Transcript:\n{transcript}",
            }
        ],
    }
    response = client.invoke_model_with_response_stream(
        modelId=CLAUDE_MODEL_ID,
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

    return "".join(full_text)

PREP_SYSTEM_PROMPT = """You are a sales productivity assistant preparing a pre-call brief.
Given notes from previous calls with a customer, produce a concise prep summary that helps
the salesperson walk into their next call fully prepared.

Structure the brief as follows:

LAST MEETING RECAP
A 2-3 sentence summary of the most recent call — what was discussed and the overall status.

WHERE WE LEFT OFF
The key topics, decisions, and direction from the last interaction. What was the customer's
state of mind? What were they excited about or concerned about?

OUTSTANDING ACTION ITEMS
List every action item that was committed to (by either side) that may still be open.
Include the owner and any deadlines mentioned. Flag items that are overdue.

OPEN QUESTIONS
Anything that was raised but not resolved, or that needs follow-up.

PROMISES MADE
Anything you or your team committed to delivering — demos, documents, introductions,
follow-up meetings, etc.

SUGGESTED TALKING POINTS
Based on the history, suggest 3-5 things to bring up or ask about in the upcoming call.

Keep it scannable — use short bullets, not paragraphs. This should be a 60-second read
that gets someone fully up to speed. Do NOT use markdown formatting (no **, ##, etc.).
Use plain text with dashes for bullets."""


def generate_prep_summary(notes_list: list, customer_name: str, on_chunk=None) -> str:
    """Generate a pre-call prep summary from recent session notes."""
    combined = "\n\n---\n\n".join(
        f"Session from {n.get('timestamp', 'unknown')[:16]}:\n{n.get('notes', '')}"
        for n in notes_list
    )

    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(read_timeout=300),
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": PREP_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Customer: {customer_name}\n\nPrevious call notes (most recent first):\n\n{combined}",
            }
        ],
    }
    response = client.invoke_model_with_response_stream(
        modelId=CLAUDE_MODEL_ID,
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

    return "".join(full_text)

COMPETITOR_EXTRACT_PROMPT = """You are a competitive intelligence analyst. Given meeting notes from a customer call,
extract any mentions of competitors, competing products, or alternative solutions the customer is evaluating or using.

Return ONLY valid JSON — an array of objects. If no competitors are mentioned, return an empty array [].

Each object should have:
{
  "competitor": "Company or product name",
  "context": "What was said about them — 1-2 sentences capturing the key point",
  "sentiment": "positive | negative | neutral — how the customer feels about this competitor"
}

Examples of what to look for:
- "They're currently using Snowflake for their data warehouse"
- "They evaluated Databricks but found it too expensive"
- "Their team prefers Azure over AWS for this workload"
- "They mentioned Google's Vertex AI as an alternative"
- "They use Tableau for dashboards but want something more interactive"

Do NOT include AWS services as competitors. Only extract non-AWS companies/products."""


def extract_competitors(notes: str, customer_name: str) -> list:
    """Extract competitor mentions from call notes. Returns list of dicts."""
    client = boto3.client(
        "bedrock-runtime", region_name=AWS_REGION,
        config=Config(read_timeout=120),
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "system": COMPETITOR_EXTRACT_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Customer: {customer_name}\n\nMeeting Notes:\n{notes}",
        }],
    }

    try:
        response = client.invoke_model(
            modelId=CLAUDE_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload),
        )
        result = json.loads(response["body"].read())
        text = result["content"][0]["text"]

        # Extract JSON array
        import re
        start = text.find('[')
        end = text.rfind(']') + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        print(f"[competitor extract] Error: {e}")

    return []

ACTION_ITEMS_PROMPT = """You are a task extraction assistant. Given meeting notes, extract all action items,
follow-ups, and commitments that were made.

Return ONLY valid JSON — an array of objects. If no action items found, return [].

Each object should have:
{
  "task": "Clear description of what needs to be done",
  "owner": "Person responsible (use the name mentioned, or 'Me' if it's the note-taker's task)",
  "due": "Due date or timeframe if mentioned (e.g. 'by Friday', 'next week', '2 weeks'), or 'No deadline' if none specified",
  "priority": "high | medium | low — based on urgency language used"
}

Look for:
- Explicit action items ("I'll send you...", "Can you follow up on...")
- Commitments ("We'll schedule a demo", "I'll loop in our SA")
- Follow-ups ("Let's reconnect next week", "I'll share the doc by Friday")
- Requests ("Can you send me the pricing?", "We need to get approval from...")

Be thorough — capture every commitment from both sides."""


def extract_action_items(notes: str, customer_name: str) -> list:
    """Extract action items from call notes. Returns list of dicts."""
    client = boto3.client(
        "bedrock-runtime", region_name=AWS_REGION,
        config=Config(read_timeout=120),
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "system": ACTION_ITEMS_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Customer: {customer_name}\n\nMeeting Notes:\n{notes}",
        }],
    }

    try:
        response = client.invoke_model(
            modelId=CLAUDE_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload),
        )
        result = json.loads(response["body"].read())
        text = result["content"][0]["text"]

        import re
        start = text.find('[')
        end = text.rfind(']') + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        print(f"[action items extract] Error: {e}")

    return []
