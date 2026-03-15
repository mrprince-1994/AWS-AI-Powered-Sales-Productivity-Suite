# Call Notes App — Complete Setup Guide

This guide walks you through every step to get the Call Notes app running on your Windows machine, from downloading the code to making your first test recording.

---

## Step 1: Install Python

If you don't already have Python installed:

1. Go to https://www.python.org/downloads/
2. Download Python 3.12 or later
3. Run the installer — **check "Add Python to PATH"** at the bottom of the first screen
4. Click "Install Now"

Verify it's installed by opening a terminal (Command Prompt or PowerShell) and running:

```bash
python --version
```

You should see something like `Python 3.12.x`.

---

## Step 2: Install Git (if not already installed)

1. Go to https://git-scm.com/download/win
2. Download and run the installer (default settings are fine)

Verify:

```bash
git --version
```

---

## Step 3: Clone the Repository

Open a terminal and navigate to where you want the project:

```bash
cd C:\Users\YourName\Desktop
git clone <repository-url>
cd <repository-folder>\call_notes_app
```

Replace `<repository-url>` with the actual GitHub URL you were given.

---

## Step 4: Install Python Dependencies

From inside the `call_notes_app` folder:

```bash
python -m pip install -r requirements.txt
```

**Important:** Use `python -m pip` (not just `pip`) to make sure packages install to the correct Python version.

If you get an error about `setuptools`, run this first:

```bash
python -m pip install setuptools
```

Then retry the requirements install.

---

## Step 5: Install the AWS CLI

1. Go to https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
2. Download and run the Windows MSI installer
3. Restart your terminal after installation

Verify:

```bash
aws --version
```

---

## Step 6: Create an AWS IAM User and Access Key

You need an AWS account with access to Amazon Transcribe and Amazon Bedrock.

1. Sign in to the AWS Console: https://console.aws.amazon.com/
2. Go to **IAM** → **Users** → **Create user**
3. Give it a name (e.g., `call-notes-app`)
4. Attach these managed policies:
   - `AmazonTranscribeFullAccess`
   - `AmazonBedrockFullAccess`
   - `AmazonDynamoDBFullAccess`
   - `AmazonBedrockAgentCoreFullAccess` (only needed if deploying the AI Q&A agent to AgentCore Runtime)
5. Go to the user → **Security credentials** tab → **Create access key**
6. Select **"Local code"** as the use case
7. Copy the **Access Key ID** and **Secret Access Key** — you'll need them in the next step

---

## Step 7: Configure AWS Credentials

Open a terminal and run:

```bash
aws configure
```

It will prompt you for four values:

| Prompt | What to enter |
|---|---|
| AWS Access Key ID | Paste your access key from Step 6 |
| AWS Secret Access Key | Paste your secret key from Step 6 |
| Default region name | `us-east-1` (or your preferred region) |
| Default output format | `json` |

Verify your credentials are working:

```bash
aws sts get-caller-identity
```

You should see your account ID and user ARN.

---

## Step 8: Verify Bedrock Model Access

Claude models on Bedrock are available by default — no access request needed.

1. Go to the Bedrock console: https://console.aws.amazon.com/bedrock/
2. Make sure you're in the same region you configured (e.g., `us-east-1`)
3. Click **Model access** in the left sidebar
4. Verify that **Anthropic Claude** models show as available

If you run into an access error when generating notes, check that your IAM user has the `AmazonBedrockFullAccess` policy attached.

---

## Step 9: Update config.py for Your Machine

Open `call_notes_app/config.py` in any text editor. You need to update several values:

### Required changes

```python
# Change this to wherever you want notes saved on YOUR machine.
# The app will create the folder automatically if it doesn't exist.
NOTES_BASE_DIR = r"C:\Users\YourName\Documents\Call Notes"
```

### Optional: Teammate note directories

The Notes Retrieval tab can index call notes from shared team folders. If you don't have these, set them to empty strings:

```python
SANGHWA_NOTES_DIR = ""
AYMAN_NOTES_DIR   = ""
```

If you do have shared note folders, set them to the full path:

```python
SANGHWA_NOTES_DIR = r"C:\Users\YourName\path\to\shared\notes"
AYMAN_NOTES_DIR   = r"C:\Users\YourName\path\to\other\notes"
```

### Optional: AgentCore Runtime ARNs

If you're not deploying the AI Q&A agent to AgentCore (see Step 14), set these to `None`:

```python
AGENTCORE_RUNTIME_ARN = None
RETRIEVAL_AGENT_ARN   = None
RESEARCH_AGENT_ARN    = None
```

The app will fall back to local agent mode or direct Bedrock calls automatically.

### AWS Region

If you're using a region other than `us-east-1`, update:

```python
AWS_REGION = os.environ.get("AWS_REGION", "your-region-here")
```

---

## Step 10: Install VB-CABLE (Virtual Audio Driver)

VB-CABLE lets the app capture audio from calls (Teams, Zoom, etc.) — not just your microphone.

1. Download VB-CABLE from https://vb-audio.com/Cable/
2. Extract the zip file
3. Right-click `VBCABLE_Setup_x64.exe` → **Run as administrator**
4. Click **Install Driver**
5. **Restart your computer**

---

## Step 11: Configure Windows Audio Routing

### Set system audio to go through VB-CABLE

1. Right-click the speaker icon in your taskbar → **Sound settings**
2. Set **Output device** to: `CABLE Input (VB-Audio Virtual Cable)`

### Set up listen-through (so you can still hear audio)

Without this, you won't hear anything because all audio is going to the virtual cable.

1. Press `Win + R`, type `mmsys.cpl`, press Enter
2. Go to the **Recording** tab
3. Right-click **CABLE Output (VB-Audio Virtual Cable)** → **Properties**
4. Go to the **Listen** tab
5. Check **"Listen to this device"**
6. Set **"Playback through this device"** to your actual speakers or headset
7. Click **OK**

For detailed instructions on switching between headset and laptop speakers, see **AUDIO_SETUP_GUIDE.md**.

---

## Step 12: Test the App

1. Open a terminal and navigate to the app folder:

```bash
cd <path-to-repo>\call_notes_app
python app.py
```

2. The app window will open with two device dropdowns:
   - **System Audio (CABLE Output):** Select `CABLE Output (VB-Audio Virtual Cable)`
   - **Microphone:** Select your headset mic or built-in mic

3. Enter any name in the Customer Name field (e.g., "Test")

4. Play a YouTube video with someone talking

5. Click **Start Recording** — you should see the live transcript appear within a few seconds

6. After 20-30 seconds, click **Stop & Generate Notes**

7. Wait for Claude to generate the notes (status bar will update)

8. Check your notes folder — a `.docx` file should be there

---

## Step 13: Create a Desktop Shortcut (Optional)

So you can launch the app without opening a terminal:

1. Find your `pythonw.exe` path by running:

```bash
python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"
```

2. Right-click Desktop → **New** → **Shortcut**

3. For the location, enter (with your actual paths):

```
"C:\Users\YourName\AppData\Local\Programs\Python\Python312\pythonw.exe" "C:\path\to\call_notes_app\app.py"
```

Both paths must be in their own quotes.

4. Name it "Call Notes"

5. Right-click the shortcut → **Properties** → set **"Start in"** to:

```
C:\path\to\call_notes_app
```

6. Click OK. Right-click the shortcut → **Pin to taskbar** if you want quick access.

---

## Quick Setup with Kiro

If you're using Kiro as your IDE, you can get guided setup assistance:

1. Open the project folder in Kiro
2. In the Kiro chat, type `#call-notes-setup` and ask "help me set up this app"
3. Kiro will walk you through each step, verify your configuration, and help troubleshoot any issues

---

## Step 14: Set Up the AI Q&A Agent (Optional)

The app includes an AI agent that detects AWS-related questions during calls and answers them by searching live AWS documentation. It has three modes that fall back automatically:

1. **AgentCore Runtime** (deployed) — best performance, managed infrastructure
2. **Local MCP** — runs on your machine, no deployment needed
3. **Direct Bedrock** — simplest fallback, no doc search, just Claude's knowledge

### Option A: Local mode (recommended to start)

Install the agent dependencies:
```bash
python -m pip install strands-agents mcp uv
```

Then clear the runtime ARN in `call_notes_app/agent_client.py` so it uses local mode:
```python
AGENTCORE_RUNTIME_ARN = None
```

The app will spawn MCP doc search servers locally when you run it. First question takes ~15s (server startup), follow-ups are fast.

### Option B: Deploy to AgentCore Runtime

For a managed, always-on agent with faster response times:

1. Install the AgentCore CLI:
```bash
pip install bedrock-agentcore-starter-toolkit
```

2. Make sure your IAM user has `AmazonBedrockAgentCoreFullAccess` (see Step 6).

3. Configure and deploy:
```bash
cd call_notes_app/agentcore_agent
agentcore configure --entrypoint agent.py --non-interactive --region us-east-1
agentcore launch
```

4. Get your runtime ARN:
```bash
agentcore status --verbose
```
Look for `agentRuntimeArn` in the output.

5. Set it in `call_notes_app/agent_client.py`:
```python
AGENTCORE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:YOUR_ACCOUNT_ID:runtime/YOUR_AGENT_ID"
```

6. Install the WebSocket client:
```bash
python -m pip install bedrock-agentcore websocket-client
```

### Option C: No agent (just transcription and notes)

If you don't want the AI Q&A feature at all, just leave `AGENTCORE_RUNTIME_ARN = None` and don't install `strands-agents` or `mcp`. The app will fall back to direct Bedrock calls for any detected questions, or you can disable the AI toggle in the app UI.

See [`agentcore_agent/README.md`](agentcore_agent/README.md) for more details on the agent architecture.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `python` not recognized | Reinstall Python with "Add to PATH" checked, or use the full path to python.exe |
| `ModuleNotFoundError` | Run `python -m pip install -r requirements.txt` from the `call_notes_app` folder |
| `InvalidClientTokenId` from AWS | Re-run `aws configure` with valid credentials |
| Bedrock `AccessDeniedException` | Verify Claude models are available in the Bedrock console (Step 8) |
| No transcript appearing | Verify Windows output is set to CABLE Input (Step 11) |
| Can't hear audio after setting up VB-CABLE | Set up listen-through in mmsys.cpl (Step 11) |
| Audio stopped after plugging in headset | Windows auto-switched output — set it back to CABLE Input |
| App window doesn't open from shortcut | Check that the "Start in" path is set correctly in shortcut properties |
| `No module named 'bedrock_agentcore'` | Run `pip install bedrock-agentcore websocket-client`, or set `AGENTCORE_RUNTIME_ARN = None` to use local mode |
| `No module named 'strands'` | Run `pip install strands-agents mcp uv` for local agent mode |
| Agent answers say "answering from general knowledge" | MCP servers failed to start — check that `uv` is installed (`pip install uv`) |
| `agentcore invoke` quoting errors on Windows | Use no spaces in JSON: `agentcore invoke '{"prompt":"hello"}'` or assign to a PowerShell variable first |
| AgentCore `AccessDeniedException` | Attach `AmazonBedrockAgentCoreFullAccess` policy to your IAM user (Step 6) |
| Outlook draft fails | Ensure Outlook desktop app is running; verify `pywin32` with `python -c "import win32com.client"` |
| DynamoDB `ResourceNotFoundException` | Tables auto-create on first run; ensure DynamoDB permissions (Step 17) |
| Customer brief stuck on "Researching" | Normal — Claude research takes 30-60s; watch the animated progress indicator |
| Style guide empty or missing | Run `python build_style_guide.py` with Outlook open (Step 15) |
| Email doesn't match my style | Re-run `python build_style_guide.py` to regenerate `style_guide.txt` |

---

## Step 15: Set Up the Email Style Guide (Optional)

The app can personalize follow-up emails to match your writing style. To set this up:

1. Make sure Outlook is open with sent emails in your Sent Items folder
2. Run the style guide generator:

```bash
cd call_notes_app
python build_style_guide.py
```

3. This reads your last 50 sent emails, analyzes your writing patterns with Claude, and saves `style_guide.txt`
4. The email generator automatically loads this file — no further configuration needed
5. Re-run anytime to update as your style evolves

---

## Step 16: Verify Outlook Integration (Optional)

The app can create follow-up email drafts directly in your Outlook Drafts folder.

1. Make sure the Outlook desktop app is installed and running
2. Verify `pywin32` is installed (included in `requirements.txt`):

```bash
python -c "import win32com.client; print('pywin32 OK')"
```

3. After generating notes from a call, click **📨 Outlook Draft** — the email will appear in your Drafts with your signature

**Note:** This uses the Outlook COM interface and only works with the desktop Outlook app (not Outlook web).

---

## Step 17: Verify DynamoDB Access

The app uses two DynamoDB tables for session history. They are auto-created on first run.

1. Verify your IAM user has DynamoDB permissions (see Step 6 — `AmazonDynamoDBFullAccess` or the specific permissions listed in the README)
2. The tables created are:
   - `CallNotesHistory` — stores transcripts, notes, and emails from the Live Transcription tab
   - `ChatSessionHistory` — stores chat sessions from the Notes Retrieval and Customer Research tabs
3. Both tables have TTL enabled for automatic cleanup (60-90 days)

If you see `ResourceNotFoundException` errors, the tables may not have finished creating. Wait a few seconds and try again.

---

## Step 18: Generate a Customer Brief (Optional)

The Customer Research tab includes a brief generator that produces formatted DOCX business briefs.

1. Go to the **Customer Research** tab
2. In the right-side panel, enter a company name and domain
3. Click **Create Customer Brief**
4. Wait 30-60 seconds (animated progress shows research steps)
5. The brief is saved to `{NOTES_BASE_DIR}/{Company Name}/{Company}_brief_{timestamp}.docx`

The brief includes: company overview, financial snapshot, leadership bios, technology landscape, AI/ML use cases, tiered AWS customer references, solutions alignment, discovery questions, and a recommended meeting agenda.

---

## Quick Reference: All Features by Tab

| Tab | Feature | Description |
|---|---|---|
| Live Transcription | Real-time transcript | Both sides of the call via VB-CABLE + mic |
| Live Transcription | AI notes generation | Comprehensive structured notes via Claude |
| Live Transcription | Follow-up email | Parallel email generation with style matching |
| Live Transcription | Outlook draft | One-click email creation in Outlook Drafts |
| Live Transcription | Copy transcript | Copy raw transcript to clipboard |
| Live Transcription | Export DOCX/PDF | Save notes in document format |
| Live Transcription | AI Q&A | Auto-detect and answer AWS questions during calls |
| Live Transcription | Session history | All sessions saved to DynamoDB |
| Notes Retrieval | Multi-turn chat | Query across all historical call notes |
| Notes Retrieval | Multi-source indexing | Your notes + teammate folders |
| Notes Retrieval | Customer filter | Searchable type-to-filter popup |
| Notes Retrieval | Session history | Save/restore/delete chat sessions |
| Customer Research | Research chat | Web-powered customer research via Claude |
| Customer Research | Customer brief | Formatted DOCX business brief generator |
| Customer Research | Session history | Save/restore/delete research sessions |
| Insights | Call Analytics | Session counts, top customers, weekly/monthly breakdowns |
| Insights | Competitive Intel | Competitor frequency, recent mentions with sentiment |
| Automated | Pre-Call Prep | One-click brief from customer history before calls |
| Automated | Follow-Up Reminders | Auto-creates Outlook To Do tasks from action items |
| Automated | Competitive Extraction | Auto-detects competitor mentions in notes |
| Automated | Weekly Digest | Monday 8 AM email summary of past week's calls |
| Automated | Email Style Guide | Personalizes follow-up emails to your writing style |

---

## Step 19: Backfill Competitive Intel (Optional)

If you have existing call notes in DynamoDB, you can populate the Insights tab with historical competitor data:

```bash
cd call_notes_app
python backfill_insights.py
```

This scans all existing sessions, extracts competitor mentions using Claude, and stores them in the `CompetitiveIntel` DynamoDB table. Run once after initial setup.

---

## Step 20: Verify Insights Tab

1. Open the app and go to the **Insights** tab
2. The left panel shows Call Analytics (session counts, top customers)
3. The right panel shows Competitive Intelligence (competitor frequency, recent mentions)
4. Click **⟳ Refresh** to update
5. Data populates automatically as you record more calls
