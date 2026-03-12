"""DynamoDB-backed chat session history for the Notes Retrieval / Research agents."""
import json
import time
import boto3
from datetime import datetime
from botocore.exceptions import ClientError
from config import AWS_REGION

TABLE_NAME = "ChatSessionHistory"
TTL_DAYS = 90

_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table = _dynamodb.Table(TABLE_NAME)


def _ensure_table():
    """Create the DynamoDB table if it doesn't exist yet."""
    client = boto3.client("dynamodb", region_name=AWS_REGION)
    try:
        client.describe_table(TableName=TABLE_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "session_type", "KeyType": "HASH"},
                {"AttributeName": "timestamp",    "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "session_type", "AttributeType": "S"},
                {"AttributeName": "timestamp",    "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expiry_ttl"},
        )
        # Wait until active
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=TABLE_NAME)


def save_chat_session(
    session_type: str,          # "retrieval" | "research"
    title: str,                 # short human label, e.g. customer name or first question
    conversation_history: list, # list of {"role": ..., "content": ...}
    customer: str = "",
    source_filter: str = "",
) -> str:
    """Persist a chat session. Returns the timestamp key."""
    ts = datetime.now().isoformat()
    _table.put_item(Item={
        "session_type":   session_type,
        "timestamp":      ts,
        "title":          title[:120],
        "customer":       customer,
        "source_filter":  source_filter,
        "history_json":   json.dumps(conversation_history, ensure_ascii=False),
        "turn_count":     len(conversation_history) // 2,
        "expiry_ttl":     int(time.time()) + (TTL_DAYS * 86400),
    })
    return ts


def list_chat_sessions(session_type: str | None = None, limit: int = 50) -> list[dict]:
    """Return sessions sorted by timestamp descending.

    If session_type is None, returns all types merged and sorted.
    """
    results = []
    types = [session_type] if session_type else ["retrieval", "research"]
    for st in types:
        resp = _table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("session_type").eq(st),
            ScanIndexForward=False,
            Limit=limit,
        )
        results.extend(resp.get("Items", []))

    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results[:limit]


def load_chat_session(session_type: str, timestamp: str) -> dict | None:
    """Load a single session by its primary key. Returns None if not found."""
    resp = _table.get_item(Key={"session_type": session_type, "timestamp": timestamp})
    item = resp.get("Item")
    if item and "history_json" in item:
        item["conversation_history"] = json.loads(item["history_json"])
    return item


def delete_chat_session(session_type: str, timestamp: str):
    _table.delete_item(Key={"session_type": session_type, "timestamp": timestamp})
