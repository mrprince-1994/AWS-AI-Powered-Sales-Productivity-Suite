"""DynamoDB-backed session history for call notes."""
import boto3
import time
from datetime import datetime
from config import AWS_REGION

TTL_DAYS = 60

TABLE_NAME = "CallNotesHistory"

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table = _dynamodb.Table(TABLE_NAME)


def save_session(customer_name: str, transcript: str, notes: str, docx_path: str, followup_email: str = ""):
    """Store a completed session in DynamoDB."""
    _table.put_item(Item={
        "customer_name": customer_name,
        "timestamp": datetime.now().isoformat(),
        "transcript": transcript,
        "notes": notes,
        "docx_path": docx_path,
        "followup_email": followup_email,
        "expiry_ttl": int(time.time()) + (TTL_DAYS * 86400),
    })


def list_sessions(customer_name: str = None) -> list:
    """Return sessions, optionally filtered by customer name.

    Returns list of dicts sorted by timestamp descending.
    """
    if customer_name:
        resp = _table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("customer_name").eq(customer_name),
            ScanIndexForward=False,
        )
    else:
        resp = _table.scan()
    items = resp.get("Items", [])
    # For scan results, sort by timestamp descending
    if not customer_name:
        items.sort(key=lambda x: x["timestamp"], reverse=True)
    return items


def get_all_customers() -> list:
    """Return a sorted list of unique customer names."""
    resp = _table.scan(ProjectionExpression="customer_name")
    names = sorted(set(item["customer_name"] for item in resp.get("Items", [])))
    return names
