#!/usr/bin/env python3
"""Fetch Notion Daily Reports and output outcomes + time spent per person."""

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

GROUPS_FILE = Path.home() / ".config" / "meet-prep" / "groups.json"

DB_ID = "26434e98-90da-813a-8f35-fe7c43ef7157"
NOTION_VERSION = "2022-06-28"
SECTIONS_TO_SHOW = {"Outcomes (1–2 max)", "Time Spent"}


def get_token():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        env_path = os.path.join(os.path.dirname(__file__), "..", "notion.env")
        try:
            token = open(env_path).read().strip()
        except FileNotFoundError:
            pass
    if not token:
        print("Error: Set NOTION_TOKEN env var or place notion.env next to the repo", file=sys.stderr)
        sys.exit(1)
    return token


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def query_reports(client, token, since: str, people: list[str] | None):
    """Query the database for reports since `since`, filtered by people (substring match)."""
    body = {
        "filter": {
            "and": [
                {"property": "Date", "date": {"on_or_after": since}},
                {"or": [
                    {"property": "Work", "status": {"equals": "Did work"}},
                    {"property": "Work", "status": {"equals": "Didn't work"}},
                ]},
            ]
        },
        "sorts": [{"property": "Date", "direction": "ascending"}],
        "page_size": 100,
    }
    pages = []
    while True:
        resp = client.post(f"https://api.notion.com/v1/databases/{DB_ID}/query", headers=headers(token), json=body)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["results"])
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]

    if people is None:
        return pages

    # Filter by people: 2-letter values match initials, others do substring match
    filtered = []
    for page in pages:
        assignees = page["properties"]["Assign"]["people"]
        names = [a["name"] for a in assignees]
        for p in people:
            if len(p) == 2 and p.isalpha():
                if any("".join(w[0] for w in n.split()).upper() == p.upper() for n in names):
                    filtered.append(page)
                    break
            elif any(p.lower() in n.lower() for n in names):
                filtered.append(page)
                break
    return filtered


def rich_text_plain(rich_text_list):
    return "".join(rt.get("plain_text", "") for rt in rich_text_list)


def get_page_sections(client, token, page_id) -> dict[str, list[str]]:
    """Return {section_heading: [bullet texts]} for the sections we care about."""
    resp = client.get(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=headers(token))
    resp.raise_for_status()
    blocks = resp.json()["results"]

    sections: dict[str, list[str]] = {}
    current = None
    for block in blocks:
        btype = block["type"]
        if btype in ("heading_1", "heading_2", "heading_3"):
            heading = rich_text_plain(block[btype]["rich_text"])
            current = next((s for s in SECTIONS_TO_SHOW if s.lower() in heading.lower() or heading.lower() in s.lower()), None)
            if current:
                sections.setdefault(current, [])
        elif current and btype == "bulleted_list_item":
            text = rich_text_plain(block["bulleted_list_item"]["rich_text"])
            if text:
                sections[current].append(text)
    return sections


def count_days(since: str) -> int:
    """Count calendar days from `since` to today inclusive."""
    return (date.today() - date.fromisoformat(since)).days + 1


def print_stats(reports: list[dict], since: str):
    """Print per-person report count and percentage of days covered."""
    workdays = count_days(since)
    by_person: dict[str, set[str]] = {}
    for page in reports:
        date_val = page["properties"]["Date"]["date"]["start"]
        for a in page["properties"]["Assign"]["people"]:
            by_person.setdefault(a["name"], set()).add(date_val)

    print(f"\n{'='*50}")
    print(f"  📊 Stats  ({since} → {date.today().isoformat()}, {workdays} days)")
    print(f"{'='*50}")
    for person, dates in sorted(by_person.items(), key=lambda x: -len(x[1])):
        count = len(dates)
        pct = count / workdays * 100 if workdays else 0
        bar = "█" * round(pct / 5) + "░" * (20 - round(pct / 5))
        print(f"  {person:<25} {count:>3}/{workdays}  {bar}  {pct:.0f}%")
    print()


def format_output(reports: list[dict], client, token):
    # Group by person
    by_person: dict[str, list[tuple[str, dict]]] = {}
    for page in reports:
        assignees = page["properties"]["Assign"]["people"]
        date_val = page["properties"]["Date"]["date"]["start"]
        for a in assignees:
            by_person.setdefault(a["name"], []).append((date_val, page))

    for person, entries in sorted(by_person.items()):
        print(f"\n{'='*60}")
        print(f"  {person}")
        print(f"{'='*60}")
        for date_val, page in entries:
            sections = get_page_sections(client, token, page["id"])
            if not any(sections.values()):
                continue
            print(f"\n  📅 {date_val}")
            for section, items in sections.items():
                if items:
                    label = "Outcomes" if "Outcome" in section else "Time Spent"
                    print(f"    {label}:")
                    for item in items:
                        print(f"      • {item}")


def load_groups() -> dict[str, list[str]]:
    if GROUPS_FILE.exists():
        return json.loads(GROUPS_FILE.read_text())
    return {}


def save_groups(groups: dict[str, list[str]]):
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(json.dumps(groups, indent=2))


def get_all_people(token) -> list[str]:
    """Fetch all unique people names from recent reports."""
    with httpx.Client(timeout=30) as client:
        body = {"page_size": 100, "sorts": [{"property": "Date", "direction": "descending"}]}
        resp = client.post(f"https://api.notion.com/v1/databases/{DB_ID}/query", headers=headers(token), json=body)
        resp.raise_for_status()
        names = set()
        for page in resp.json()["results"]:
            for a in page["properties"]["Assign"]["people"]:
                names.add(a["name"])
    return sorted(names)


def cmd_create_group(args):
    token = get_token()
    people = get_all_people(token)
    if not people:
        print("No people found in recent reports.", file=sys.stderr)
        sys.exit(1)

    # Use fzf for multi-select (needs tty for interactive UI)
    try:
        proc = subprocess.Popen(
            ["fzf", "--multi", "--bind", "tab:toggle+clear-query", "--prompt", f"Select members for '{args.name}': "],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        stdout, _ = proc.communicate(input="\n".join(people))
    except FileNotFoundError:
        print("Error: fzf is required for interactive selection. Install it with your package manager.", file=sys.stderr)
        sys.exit(1)

    if proc.returncode != 0 or not stdout.strip():
        print("No selection made, group not created.")
        return

    selected = [s.strip() for s in stdout.strip().split("\n")]
    groups = load_groups()
    groups[args.name] = selected
    save_groups(groups)
    print(f"Group '{args.name}' created with: {', '.join(selected)}")


def cmd_list_groups(_args):
    groups = load_groups()
    if not groups:
        print("No groups defined.")
        return
    for name, members in groups.items():
        print(f"  {name}: {', '.join(members)}")


def cmd_delete_group(args):
    groups = load_groups()
    if args.name not in groups:
        print(f"Group '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)
    del groups[args.name]
    save_groups(groups)
    print(f"Group '{args.name}' deleted.")


def resolve_people(people: list[str]) -> list[str]:
    """Resolve group names in the people list to their members."""
    groups = load_groups()
    resolved = []
    for p in people:
        if p in groups:
            resolved.extend(groups[p])
        else:
            resolved.append(p)
    return resolved


def main():
    parser = argparse.ArgumentParser(description="Fetch Notion Daily Reports for meeting prep")
    sub = parser.add_subparsers(dest="command")

    # create-group
    cg = sub.add_parser("create-group", help="Create a people group (interactive)")
    cg.add_argument("name", help="Group name")

    # list-groups
    sub.add_parser("list-groups", help="List all groups")

    # delete-group
    dg = sub.add_parser("delete-group", help="Delete a group")
    dg.add_argument("name", help="Group name to delete")

    # stat
    st = sub.add_parser("stat", help="Show report stats")
    st.add_argument("-p", "--people", nargs="+", default=None,
                    help="People or group names (default: everyone)")
    st.add_argument("-d", "--days", type=int, default=30, help="Number of past days (default: 30)")

    # Default report flags
    parser.add_argument("-d", "--days", type=int, default=30, help="Number of past days to fetch (default: 30)")
    parser.add_argument("-p", "--people", nargs="+", default=["Teo", "Marián", "Lukas"],
                        help="People or group names to filter by (default: Teo Marián Lukas)")
    args = parser.parse_args()

    if args.command == "create-group":
        cmd_create_group(args)
        return
    if args.command == "list-groups":
        cmd_list_groups(args)
        return
    if args.command == "delete-group":
        cmd_delete_group(args)
        return

    token = get_token()
    since = (date.today() - timedelta(days=args.days)).isoformat()

    if args.command == "stat":
        people = resolve_people(args.people) if args.people else None
        label = "everyone" if people is None else ", ".join(people)
        print(f"Fetching reports since {since} for: {label}")
        with httpx.Client(timeout=30) as client:
            reports = query_reports(client, token, since, people)
            if not reports:
                print("No reports found.")
                return
            print(f"Found {len(reports)} reports.")
            print_stats(reports, since)
        return

    people = None if any(p.lower() == "all" for p in args.people) else resolve_people(args.people)
    label = "everyone" if people is None else ", ".join(people)
    print(f"Fetching reports since {since} for: {label}")

    with httpx.Client(timeout=30) as client:
        reports = query_reports(client, token, since, people)
        if not reports:
            print("No reports found.")
            return
        print(f"Found {len(reports)} reports.")
        format_output(reports, client, token)


if __name__ == "__main__":
    main()
