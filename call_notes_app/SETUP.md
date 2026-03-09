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

Open `call_notes_app/config.py` in any text editor and update these values:

```python
# Set to your AWS region (must match where you enabled Bedrock)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Change this to wherever you want notes saved on YOUR machine
NOTES_BASE_DIR = r"C:\Users\YourName\Documents\Call Notes"
```

**You must change `NOTES_BASE_DIR`** to a folder path on your computer. The app will create it automatically if it doesn't exist.

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
