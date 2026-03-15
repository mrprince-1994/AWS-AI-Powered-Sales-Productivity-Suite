"""Backfill competitive intel from existing call notes in DynamoDB.

Run once to populate the CompetitiveIntel table from historical sessions:
    python backfill_insights.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from transcription.history import list_sessions
from transcription.summarizer import extract_competitors
from transcription.competitive_intel import save_competitor_mentions, _ensure_table

def main():
    print("Backfilling competitive intel from historical call notes...")
    _ensure_table()

    sessions = list_sessions()
    print(f"Found {len(sessions)} total sessions")

    total_mentions = 0
    for i, session in enumerate(sessions):
        customer = session.get("customer_name", "Unknown")
        notes = session.get("notes", "")
        ts = session.get("timestamp", "")[:16]

        if not notes or len(notes) < 100:
            continue

        print(f"  [{i+1}/{len(sessions)}] {customer} ({ts})...", end=" ")

        try:
            mentions = extract_competitors(notes, customer)
            if mentions:
                save_competitor_mentions(customer, mentions)
                print(f"{len(mentions)} competitor(s) found")
                total_mentions += len(mentions)
            else:
                print("no competitors")
        except Exception as e:
            print(f"error: {e}")

    print(f"\nDone. Extracted {total_mentions} total competitor mentions.")


if __name__ == "__main__":
    main()
