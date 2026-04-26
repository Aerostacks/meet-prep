#!/usr/bin/env python3
"""Fetch Notion Daily Reports and output outcomes + time spent per person."""

import argparse
import os
import sys
from datetime import date, timedelta

import httpx

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


def query_reports(client, token, since: str, people: list[str]):
    """Query the database for reports since `since`, filtered by people (substring match)."""
    body = {
        "filter": {
            "and": [
                {"property": "Date", "date": {"on_or_after": since}},
                {"property": "Work", "status": {"equals": "Did work"}},
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

    # Filter by people (case-insensitive substring match on Assign names)
    people_lower = [p.lower() for p in people]
    filtered = []
    for page in pages:
        assignees = page["properties"]["Assign"]["people"]
        names = [a["name"] for a in assignees]
        if any(pl in n.lower() for n in names for pl in people_lower):
            filtered.append(page)
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


def main():
    parser = argparse.ArgumentParser(description="Fetch Notion Daily Reports for meeting prep")
    parser.add_argument("-d", "--days", type=int, default=30, help="Number of past days to fetch (default: 30)")
    parser.add_argument("-p", "--people", nargs="+", default=["Teo", "Marián", "Lukas"],
                        help="People to filter by (substring match, default: Teo Marián Lukas)")
    args = parser.parse_args()

    token = get_token()
    since = (date.today() - timedelta(days=args.days)).isoformat()

    print(f"Fetching reports since {since} for: {', '.join(args.people)}")

    with httpx.Client(timeout=30) as client:
        reports = query_reports(client, token, since, args.people)
        if not reports:
            print("No reports found.")
            return
        print(f"Found {len(reports)} reports.")
        format_output(reports, client, token)


if __name__ == "__main__":
    main()
