# meet-prep

CLI tool that fetches Notion Daily Reports and outputs outcomes and time spent per person — useful for meeting preparation.

## Setup

```bash
pip install -e .
```

Set your Notion integration token via env var or place it in `notion.env` (one directory up from the repo):

```bash
export NOTION_TOKEN=ntn_...
```

## Usage

```bash
# Default: last 30 days for Lukas, Marián, Martin
meet-prep

# Custom days and people
meet-prep -d 14 -p Lukas Dominik

# Just one person, last week
meet-prep -d 7 -p Michal
```

People are matched by case-insensitive substring against Notion assignee names.
