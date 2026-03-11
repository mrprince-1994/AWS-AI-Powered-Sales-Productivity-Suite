import os

# AWS settings — uses your default AWS credentials (env vars, ~/.aws/credentials, or IAM role)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Audio settings
SAMPLE_RATE = 16000  # Amazon Transcribe expects 16kHz for best results
CHANNELS = 1

# Notes output directory — your personal call notes
NOTES_BASE_DIR = r"C:\Users\mrprince\OneDrive - amazon.com\mrprince SMB WE - Documents\Call Notes"

# SA team shared folder (Ayman & Sanghwa use-case notes)
SA_NOTES_DIR = r"C:\Users\mrprince\amazon.com\Reddy, Chandra - SMB Team Folder"

# Claude model ID on Bedrock
CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
