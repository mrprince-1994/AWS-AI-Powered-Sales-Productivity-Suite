# Call Notes — AI-Powered Sales Productivity Suite

A Windows desktop application for customer-facing professionals that live-transcribes calls, generates structured notes, drafts follow-up emails directly into Outlook, and provides AI-powered research and retrieval across all your historical call notes.

Built on AWS services: Amazon Transcribe, Amazon Bedrock (Claude), and DynamoDB.

---

## Features

### Tab 1: Live Transcription
- Real-time transcription of both sides of any call (Teams, Zoom, etc.) using Amazon Transcribe with speaker diarization
- AI-generated comprehensive meeting notes via Claude on Bedrock (streamed in real-time)
- AI-generated follow-up email draft (runs in parallel with notes)
- One-click Outlook draft creation with your signature preserved
- Personalized email tone using your writing style guide
- Copy transcript to clipboard
- Export notes as DOCX or PDF
- Auto-detect and answer AWS-related questions during calls via AI agent
- Session history stored in DynamoDB with full transcript, notes, and email

### Tab 2: Notes Retrieval
- Multi-turn chat interface to query across all your historical call notes
- Indexes notes from multiple directories (your notes + teammate folders)
- Searchable customer filter with type-to-search popup
- Source filter (All Sources, My Notes, teammate folders)
- Suggested prompts for common queries
- Session history with save/restore/delete
- Powered by Claude Opus 4.6 with file reading tool-use

### Tab 3: Customer Research
- Web-powered research chat for any customer or topic
- Customer Brief Generator — enter a company name and domain to produce a formatted DOCX brief with:
  - Company overview, key facts, financial snapshot
  - Leadership team bios
  - Technology & AI/ML landscape analysis
  - Tiered AWS customer references (Tier 1: highly relevant, Tier 2: adjacent)
  - AWS solutions alignment table
  - Discovery questions organized by theme
  - Recommended meeting agenda
- Briefs saved to the same customer folder as call notes
- Session history with save/restore/delete

### Tab 4: Insights
- Call Analytics: total sessions, this week/month counts, top customers with visual bar charts
- Competitive Intelligence: auto-extracted competitor mentions with frequency ranking and sentiment
- Recent competitor mentions with customer context
- Refresh button to update on demand

### Automated Features (run in background after each call)
- Pre-Call Prep: click "📋 Pre-Call Prep" before a call to get a brief from DynamoDB history + local note files
- Smart Follow-Up Reminders: auto-creates Outlook To Do tasks from action items with due dates and priorities
- Competitive Intel Extraction: auto-detects competitor mentions and stores in DynamoDB
- Weekly Digest: sends a summary email every Monday at 8 AM via Windows Task Scheduler
- Email Style Guide: analyzes your sent emails to personalize follow-up tone

---

## Prerequisites

- **Python 3.10+** (tested with 3.12)
- **Windows** (required for VB-CABLE audio routing and Outlook integration)
- **AWS Account** with:
  - Amazon Transcribe (streaming access)
  - Amazon Bedrock with Claude model access (Sonnet 4.6 and Opus 4.6)
  - DynamoDB (for session history — tables auto-created on first run)
- **AWS CLI** configured with valid credentials (`aws configure`)
- **VB-CABLE** virtual audio driver ([download](https://vb-audio.com/Cable/))
- **Microsoft Outlook** (desktop app, for email draft feature)
- **IAM Permissions**:
  - `transcribe:StartStreamTranscription`
  - `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`
  - `dynamodb:PutItem`, `dynamodb:Query`, `dynamodb:Scan`, `dynamodb:DeleteItem`, `dynamodb:CreateTable`, `dynamodb:DescribeTable`
  - `bedrock-agentcore:*` (optional, only if deploying the AI Q&A agent)

---

## Installation

```bash
cd call_notes_app
python -m pip install -r requirements.txt
```

### Key Dependencies

| Package | Purpose |
|---|---|
| `sounddevice` | Audio capture from mic and virtual cable |
| `numpy` | Audio buffer mixing and PCM conversion |
| `amazon-transcribe` | Amazon Transcribe Streaming SDK |
| `boto3` | AWS SDK for Bedrock and DynamoDB |
| `python-docx` | Generating formatted .docx files (notes + briefs) |
| `customtkinter` | Modern dark-themed UI framework |
| `pywin32` | Outlook COM integration for email drafts |
| `fpdf2` | PDF export (optional) |
| `bedrock-agentcore` | AgentCore Runtime SDK (optional) |
| `strands-agents`, `mcp` | Local AI agent with MCP tools (optional) |

---

## AWS Setup

### 1. Configure Credentials

```bash
aws configure
```

Enter your Access Key ID, Secret Access Key, region (`us-east-1`), and output format (`json`).

### 2. Verify Bedrock Model Access

1. Go to the [Bedrock console](https://console.aws.amazon.com/bedrock/)
2. Navigate to **Model access** → verify **Anthropic Claude** models are available

### 3. DynamoDB Tables

Two tables are used for session history. They are auto-created on first run:

| Table | Purpose | Keys |
|---|---|---|
| `CallNotesHistory` | Stores transcripts, notes, and emails from Tab 1 | `customer_name` (PK), `timestamp` (SK) |
| `ChatSessionHistory` | Stores chat sessions from Tabs 2 and 3 | `session_type` (PK), `timestamp` (SK) |
| `CompetitiveIntel` | Stores competitor mentions extracted from notes | `competitor` (PK), `timestamp` (SK) |

Both tables use 60-90 day TTL for automatic cleanup.

---

## Audio Setup

See **[AUDIO_SETUP_GUIDE.md](AUDIO_SETUP_GUIDE.md)** for detailed instructions.

**Quick version:**
1. Install VB-CABLE (run installer as admin, reboot)
2. Set Windows output to `CABLE Input (VB-Audio Virtual Cable)`
3. Set up listen-through via `mmsys.cpl` → Recording → CABLE Output → Listen tab
4. In the app: System Audio = `CABLE Output`, Microphone = your mic

---

## Running the App

```bash
cd call_notes_app
python app.py
```

The app opens with three tabs: Live Transcription, Notes Retrieval, and Customer Research.

---

## Tab 1: Live Transcription — Detailed Usage

1. Enter the customer name
2. Select audio devices (System Audio = CABLE Output, Microphone = your mic)
3. Click **Start Recording** — live transcript streams in real-time
4. Click **Stop & Generate** — two things happen in parallel:
   - Claude generates comprehensive structured notes
   - Claude generates a follow-up email draft
5. Click **📨 Outlook Draft** to create the email in your Outlook Drafts folder (with your signature)
6. Click **📋 Copy Transcript** to copy the raw transcript
7. Click **📄 Export DOCX** or **📑 Export PDF** to save notes
8. All sessions are saved to DynamoDB and appear in the History sidebar

### Follow-Up Email

The email generator produces a clean, professional follow-up with:
- Subject line auto-populated
- Section headers bolded
- Bullet-formatted action items
- Aptos font matching Outlook defaults
- Your Outlook signature preserved

### Email Style Guide

To personalize the email tone to match your writing style:

```bash
python build_style_guide.py
```

This reads your last 50 sent Outlook emails, analyzes your writing patterns with Claude, and saves `style_guide.txt`. The email generator automatically loads this file to match your greeting style, tone, vocabulary, and sign-off.

### Notes Structure

Claude generates notes with these sections:
- Meeting Context
- Detailed Discussion Notes (chronological, by topic)
- Decisions & Agreements
- Action Items & Owners
- Open Questions & Unresolved Items
- Follow-Up & Next Steps
- Key Quotes & Verbatim Notes
- Additional Context

---

## Tab 2: Notes Retrieval — Detailed Usage

A multi-turn chat interface for querying your historical call notes.

1. The app indexes all `.md` and `.docx` files from your configured note directories
2. Use the **Source** dropdown to filter by note source (your notes, teammate folders)
3. Use the **Customer** button to open a searchable picker and filter by customer
4. Type a question or click a suggested prompt
5. Claude reads the relevant note files and synthesizes an answer
6. Conversations are multi-turn — ask follow-ups in the same session
7. Sessions auto-save to DynamoDB and can be restored from the History sidebar

---

## Tab 3: Customer Research — Detailed Usage

### Research Chat
- Ask any question about a customer, industry, or topic
- Powered by Claude with web search capabilities
- Multi-turn conversations with session history

### Customer Brief Generator
The right-side panel lets you generate a formatted DOCX business brief:

1. Enter the company name and domain
2. Click **Create Customer Brief**
3. Claude researches the company across 7 dimensions:
   - Company profile, financials, leadership
   - Technology landscape, AI/ML use cases
   - AWS customer references (tiered), solutions alignment
   - Competitive context
4. A formatted DOCX is generated with:
   - Title page with confidentiality notice
   - Table of contents
   - Key facts and financial snapshot tables
   - Leadership bios
   - Tiered AWS customer references
   - Discovery questions (5 themes, 4-5 questions each)
   - Recommended meeting agenda
5. Saved to `{NOTES_BASE_DIR}/{Company Name}/{Company}_brief_{timestamp}.docx`

---

## Tab 4: Insights — Detailed Usage

A dashboard combining call analytics and competitive intelligence.

### Call Analytics (left panel)
- Total session count, this week, this month
- Unique customer count
- This week's calls listed
- Top customers ranked by call frequency with visual bar charts
- Monthly unique customer breakdown

### Competitive Intelligence (right panel)
- Competitor frequency ranking (how often each competitor is mentioned across all calls)
- Recent mentions with customer context and sentiment (👍 positive, 👎 negative, ➖ neutral)
- Auto-populated from notes — no manual entry needed

Click "⟳ Refresh" to update both panels.

### Backfilling Historical Data

To populate competitive intel from existing call notes:

```bash
python backfill_insights.py
```

This scans all DynamoDB sessions and extracts competitor mentions from each.

---

## Automated Features

### Pre-Call Prep
1. Enter a customer name on the Live Transcription tab
2. Click "📋 Pre-Call Prep" (right side of button row)
3. Pulls last 3 DynamoDB sessions + local note files for that customer
4. Claude generates: last meeting recap, outstanding action items, open questions, promises made, suggested talking points
5. Streams into the AI Answers panel

### Smart Follow-Up Reminders
After every "Stop & Generate", the app automatically:
1. Extracts action items from the notes (task, owner, deadline, priority)
2. Creates Outlook To Do tasks with due dates and reminders
3. Tasks appear in your To Do list under "Tasks" with "Call Notes" category
4. Reminders set 1 day before due date

### Weekly Digest
- Runs automatically every Monday at 8 AM via Windows Task Scheduler
- Scans all calls from the past 7 days
- Generates a summary: calls made, key decisions, open action items, follow-ups due
- Sends directly to your email
- Fallback: saves to `{NOTES_BASE_DIR}/_Weekly Digests/` if Outlook is unavailable
- Run manually anytime: `python weekly_digest.py`

### Competitive Intel Extraction
After every "Stop & Generate", the app automatically:
1. Sends notes to Claude to identify competitor mentions
2. Extracts company name, context, and sentiment
3. Stores in DynamoDB `CompetitiveIntel` table
4. Visible in the Insights tab

---

## Configuration

All settings are in `config.py`. Update these for your machine:

| Setting | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region for all services |
| `SAMPLE_RATE` | `16000` | Audio sample rate (16kHz for Transcribe) |
| `NOTES_BASE_DIR` | *(author's path)* | **Change this** — where notes and briefs are saved |
| `SANGHWA_NOTES_DIR` | *(author's path)* | Optional teammate notes folder. Set to `""` if not needed |
| `AYMAN_NOTES_DIR` | *(author's path)* | Optional teammate notes folder. Set to `""` if not needed |
| `CLAUDE_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | Bedrock model for notes and email generation |
| `AGENTCORE_RUNTIME_ARN` | *(author's ARN)* | Set to your ARN or `None` for local agent mode |
| `RETRIEVAL_AGENT_ARN` | *(author's ARN)* | Set to your ARN or `None` |
| `RESEARCH_AGENT_ARN` | *(author's ARN)* | Set to your ARN or `None` |

---

## Project Structure

```
call_notes_app/
├── app.py                        # Main entry point — all 3 tab UIs
├── config.py                     # All configurable settings
├── md_render.py                  # Markdown-to-Tkinter renderer
├── build_style_guide.py          # Generates email style guide from Outlook sent mail
├── weekly_digest.py              # Weekly summary email (runs via Task Scheduler)
├── backfill_insights.py          # One-time backfill of competitive intel from history
├── style_guide.txt               # Auto-generated writing style (used by email generator)
├── requirements.txt
├── README.md / SETUP.md / AUDIO_SETUP_GUIDE.md
│
├── transcription/                # Tab 1: Live Transcription
│   ├── transcriber.py            # Audio capture + Amazon Transcribe Streaming
│   ├── summarizer.py             # Notes + follow-up email + competitor/action extraction
│   ├── storage.py                # DOCX conversion and file saving
│   ├── history.py                # DynamoDB session persistence (CallNotesHistory)
│   ├── question_detector.py      # AWS question detection in transcript
│   ├── agent_client.py           # AI Q&A agent (AgentCore / local MCP / Bedrock)
│   ├── competitive_intel.py      # DynamoDB competitive intelligence tracker
│   └── outlook_tasks.py          # Outlook To Do task creation from action items
│
├── retrieval/                    # Tabs 2 & 3: Notes Retrieval + Customer Research
│   ├── notes_retriever.py        # Note indexing, retrieval agent, research agent
│   ├── chat_history.py           # DynamoDB chat session persistence (ChatSessionHistory)
│   └── customer_brief.py         # Customer brief generator (research + DOCX builder)
│
└── agentcore_agent/              # Deployable AgentCore agents
    ├── agent.py                  # Strands Agent with MCP tools
    ├── requirements.txt
    ├── README.md
    ├── research_agent/           # Customer research agent
    └── retrieval_agent/          # Notes retrieval agent
```

---

## AI Q&A Agent

Detects AWS-related questions during calls and answers them by searching live AWS documentation.

### Three Modes (automatic fallback)

1. **AgentCore Runtime** — deployed agent via WebSocket + SigV4 auth
2. **Local MCP** — in-process agent with MCP doc search tools
3. **Direct Bedrock** — simple Claude call without tools

See [`agentcore_agent/README.md`](agentcore_agent/README.md) for deployment instructions.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `ModuleNotFoundError` | Run `python -m pip install -r requirements.txt` |
| No transcript appearing | Check Windows output is set to CABLE Input; verify device selection |
| Bedrock `AccessDeniedException` | Verify Claude models in Bedrock console; check IAM permissions |
| Transcribe `InvalidClientTokenId` | Re-run `aws configure` with valid credentials |
| Outlook draft fails | Ensure Outlook desktop app is running; install `pywin32` |
| DynamoDB `ResourceNotFoundException` | Tables auto-create on first run; ensure DynamoDB permissions |
| Customer brief stuck on "Researching" | Normal — Claude research takes 30-60s; watch the animated progress |
| Style guide empty | Run `python build_style_guide.py` with Outlook open and sent emails available |
| App doesn't capture mic audio | Select correct mic in dropdown; both streams are mixed |
| Outlook tasks not in To Do | Tasks appear under "Tasks" list in To Do sidebar; check "Call Notes" category |
| Weekly digest not sending | Verify Task Scheduler task "CallNotes-WeeklyDigest" is enabled; Outlook must be running |
| No competitors in Insights | Run `python backfill_insights.py` to populate from historical notes |
