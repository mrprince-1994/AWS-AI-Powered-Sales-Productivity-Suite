---
inclusion: manual
---

# Call Notes App — Setup Guide for New Users

This workspace contains a Call Notes desktop application that live-transcribes calls and generates organized notes using AWS services. When a new user opens this project and asks for help setting it up, follow these steps:

## 1. Prerequisites Check

Verify the user has:
- Python 3.10+ installed (`python --version`)
- AWS CLI installed (`aws --version`)
- VB-CABLE virtual audio driver installed (Windows only — download from https://vb-audio.com/Cable/)

## 2. Install Dependencies

Run from the `call_notes_app` directory:
```bash
python -m pip install -r requirements.txt
```
Use `python -m pip` (not just `pip`) to ensure packages install to the correct Python.

## 3. AWS Credentials Setup

The app needs AWS credentials with these permissions:
- `transcribe:StartStreamTranscription` (for live transcription)
- `bedrock:InvokeModel` (for note generation with Claude)

Help the user run `aws configure` in their terminal (not via the agent — it's interactive).
They need an IAM user with an access key. The "Local code" use case should be selected when creating the key in the IAM console.

Verify credentials work: `aws sts get-caller-identity`

They should verify Claude model access is available in the Bedrock console for their region.

## 4. Update config.py

The following values in `call_notes_app/config.py` MUST be personalized for each user:

- `AWS_REGION` — set to the user's preferred region (must have Bedrock + Transcribe access)
- `NOTES_BASE_DIR` — change to the user's preferred local folder path for storing notes
- `CLAUDE_MODEL_ID` — only change if using a different Claude model

## 5. Audio Setup

Refer the user to `call_notes_app/AUDIO_SETUP_GUIDE.md` for detailed instructions.

Quick summary:
1. Install VB-CABLE (run installer as admin, reboot)
2. Set Windows output device to "CABLE Input (VB-Audio Virtual Cable)"
3. Set up listen-through via `mmsys.cpl` → Recording → CABLE Output → Listen tab
4. In the app: select CABLE Output for system audio, their mic for microphone

## 6. Create Desktop Shortcut (Optional)

Create a Windows shortcut with target:
```
"<path-to-pythonw.exe>" "<path-to-call_notes_app\app.py>"
```

Find pythonw.exe path with: `python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"`

Set "Start in" to the `call_notes_app` directory. Pin to taskbar if desired.

## 7. Test

Have the user:
1. Run `python app.py` from the `call_notes_app` directory
2. Play a YouTube video with speech
3. Start recording, let it run for 15-20 seconds
4. Stop and verify transcript appears and notes are generated
