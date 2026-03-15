"""Create Outlook tasks/reminders from extracted action items."""
from datetime import datetime, timedelta
import re


def _parse_due_date(due_text: str) -> datetime | None:
    """Try to parse a due date from natural language."""
    if not due_text or due_text.lower() in ("no deadline", "none", "tbd", ""):
        return None

    now = datetime.now()
    text = due_text.lower().strip()

    # Relative dates
    if "today" in text:
        return now
    if "tomorrow" in text:
        return now + timedelta(days=1)
    if "next week" in text:
        return now + timedelta(weeks=1)
    if "2 weeks" in text or "two weeks" in text:
        return now + timedelta(weeks=2)
    if "next month" in text:
        return now + timedelta(days=30)
    if "end of week" in text or "by friday" in text or "this friday" in text:
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        return now + timedelta(days=days_until_friday)
    if "by monday" in text or "this monday" in text:
        days_until_monday = (0 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        return now + timedelta(days=days_until_monday)

    # Try to find a number of days/weeks
    m = re.search(r'(\d+)\s*day', text)
    if m:
        return now + timedelta(days=int(m.group(1)))
    m = re.search(r'(\d+)\s*week', text)
    if m:
        return now + timedelta(weeks=int(m.group(1)))

    # Default: 1 week from now if something was mentioned but unparseable
    return now + timedelta(weeks=1)


def create_outlook_tasks(action_items: list, customer_name: str) -> int:
    """Create Outlook tasks from extracted action items. Returns count created."""
    try:
        import win32com.client
    except ImportError:
        print("[outlook_tasks] pywin32 not installed, skipping task creation")
        return 0

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
    except Exception as e:
        print(f"[outlook_tasks] Outlook not available: {e}")
        return 0

    created = 0
    for item in action_items:
        task_text = item.get("task", "").strip()
        if not task_text:
            continue

        owner = item.get("owner", "")
        priority_text = item.get("priority", "medium").lower()
        due_text = item.get("due", "")

        try:
            # 3 = olTaskItem
            task = outlook.CreateItem(3)
            task.Subject = f"[{customer_name}] {task_text}"
            task.Body = (
                f"Customer: {customer_name}\n"
                f"Owner: {owner}\n"
                f"Due: {due_text}\n"
                f"Priority: {priority_text}\n\n"
                f"Auto-generated from call notes on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

            # Set priority (0=low, 1=normal, 2=high)
            if priority_text == "high":
                task.Importance = 2
            elif priority_text == "low":
                task.Importance = 0
            else:
                task.Importance = 1

            # Set due date if parseable
            due_date = _parse_due_date(due_text)
            if due_date:
                task.DueDate = due_date.strftime("%m/%d/%Y")
                # Set reminder 1 day before
                task.ReminderSet = True
                reminder_date = due_date - timedelta(days=1)
                task.ReminderTime = reminder_date.strftime("%m/%d/%Y 9:00 AM")

            task.Save()
            created += 1
            print(f"[outlook_tasks] Created: [{customer_name}] {task_text[:50]}...")
        except Exception as e:
            print(f"[outlook_tasks] Failed to create task: {e}")

    return created
