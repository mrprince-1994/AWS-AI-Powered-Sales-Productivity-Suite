"""Queue MEDDPICC coverage data for end-of-day AWSentral submission.

Writes a JSON file to meddpicc_queue/ that the end-of-day hook picks up
and uses to update Salesforce opportunity MEDDPICC fields.
"""
import json
import os
from datetime import datetime

MEDDPICC_QUEUE_DIR = os.path.join(os.path.dirname(__file__), "..", "meddpicc_queue")


def queue_meddpicc(customer_name: str, meddpicc_state: dict) -> str | None:
    """Write MEDDPICC coverage to the queue for AWSentral submission.

    Args:
        customer_name: Customer name for opportunity lookup
        meddpicc_state: Output of MeetingAssistant.export_state()

    Returns:
        Path to queued JSON file, or None on error.
    """
    try:
        coverage = meddpicc_state.get("coverage", {})
        # Only queue if at least one element is covered
        if not any(info.get("covered") for info in coverage.values()):
            return None

        # Build a clean payload for the hook
        fields = {}
        for element, info in coverage.items():
            if info.get("covered") and info.get("evidence"):
                fields[element] = info["evidence"]

        if not fields:
            return None

        payload = {
            "customer_name": customer_name,
            "call_date": datetime.now().strftime("%Y-%m-%d"),
            "created_at": datetime.now().isoformat(),
            "coverage": fields,
            "coverage_count": sum(1 for i in coverage.values() if i.get("covered")),
            "total_elements": 8,
        }

        os.makedirs(MEDDPICC_QUEUE_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_customer = customer_name.replace(" ", "_").replace("/", "_")[:50]
        filename = f"meddpicc_{safe_customer}_{timestamp}.json"
        filepath = os.path.join(MEDDPICC_QUEUE_DIR, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        print(f"[meddpicc] Queued MEDDPICC data: {filepath}")
        return filepath

    except Exception as e:
        print(f"[meddpicc] Error queuing MEDDPICC data: {e}")
        return None
