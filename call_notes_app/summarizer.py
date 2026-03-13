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
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(read_timeout=300),
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": EMAIL_SYSTEM_PROMPT,
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
