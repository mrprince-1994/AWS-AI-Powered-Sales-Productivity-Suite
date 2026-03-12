"""AgentCore Runtime — Historical Notes Retrieval Agent (streaming).

Built with Strands. Receives a file index + question, calls read_note_file
tool to fetch relevant files, then streams the answer back via SSE.

Payload schema:
  {
    "prompt": "<user question>",
    "file_index": [
      {"file_id": "file_0", "customer": "BQE", "source": "Sanghwa",
       "filename": "[03_03] BQE - Discovery.docx", "date": "03-03", "filepath": "/abs/path"}
    ]
  }
"""
import json
import os

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp

OPUS_MODEL_ID = "us.anthropic.claude-opus-4-6-v1"

SYSTEM_PROMPT = """You are an expert assistant that retrieves and synthesizes information \
from historical customer call notes. Your purpose is to help an AWS account manager \
quickly get up to speed on any customer by reading through their call notes.

You have a `read_note_file` tool. Use it to read specific note files by file_id.

Workflow:
1. You receive an index of available files (file_id, customer, source, filename, date)
2. Based on the user's question, identify which files are relevant
3. Call `read_note_file` for each relevant file to get its content
4. Synthesize the content into a comprehensive answer

When asked for "recent context" or a general overview of a customer, read ALL \
available notes for that customer (most recent first) and produce:

## Customer Overview
Brief summary of who they are and the relationship status.

## Recent Discussions
For each call/meeting (most recent first), summarize:
- Date and participants (if known)
- Primary topics discussed
- Key details, decisions, and outcomes

## Outstanding Action Items
All open action items across all notes, with owners and deadlines if mentioned.

## Key Discussion Themes
Recurring topics, concerns, or opportunities that come up across multiple calls.

## Current Status & Next Steps
Where things stand today based on the most recent notes.

Guidelines:
- Always cite the source file (customer, filename, source team, date) for each piece of info
- Be thorough — read all relevant files, don't stop at just one or two
- Preserve specifics: names, numbers, dates, technical details, product names
- If the user asks a specific question, answer it directly rather than using the full template
- Format responses in clean markdown"""

app = BedrockAgentCoreApp()


@app.entrypoint
async def retrieve_notes(payload, context):
    question = payload.get("prompt", "")
    file_index = payload.get("file_index", [])

    if not question:
        yield {"text": "No question provided.", "type": "error"}
        return

    # Build file_id → metadata + content lookup
    file_map: dict[str, dict] = {entry["file_id"]: entry for entry in file_index}

    @tool
    def read_note_file(file_id: str) -> str:
        """Read the full text content of a call note file by its file_id.

        Args:
            file_id: The file_id from the index (e.g. 'file_0', 'file_1')
        """
        entry = file_map.get(file_id)
        if not entry:
            return f"Error: file_id '{file_id}' not found in index."

        filepath = entry.get("filepath", "")
        if not filepath or not os.path.isfile(filepath):
            return f"Error: file not found at path '{filepath}'."

        try:
            if filepath.lower().endswith(".docx"):
                from docx import Document
                doc = Document(filepath)
                text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            else:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
        except Exception as e:
            return f"Error reading file: {e}"

        return (
            f"=== {entry['customer']} | {entry['source']} | "
            f"{entry['filename']} | {entry.get('date') or 'no date'} ===\n\n{text}"
        )

    # Build index text for the first message
    index_lines = "\n".join(
        f"  {e['file_id']}: [{e.get('source','?')}] customer={e['customer']} "
        f"date={e.get('date') or 'unknown'} filename={e['filename']}"
        for e in file_index
    )
    index_text = f"Available note files ({len(file_index)} total):\n{index_lines}"

    try:
        agent = Agent(
            system_prompt=SYSTEM_PROMPT,
            tools=[read_note_file],
            model=OPUS_MODEL_ID,
            callback_handler=None,
        )

        stream = agent.stream_async(f"{index_text}\n\n---\n\n{question}")
        async for event in stream:
            yield event

    except Exception as e:
        yield {"text": f"Error: {e}", "type": "error"}


if __name__ == "__main__":
    app.run()
