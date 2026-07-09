# Linear Skill

This directory contains the skill for interacting with Linear.

## Files

- `SKILL.md` - Instructions for using this skill
- `linear.py` - Python script that implements the skill (if present)

## Usage

To use this skill, run:

```bash
python .agent-skills/linear/linear.py <command> [args]
```

Commands:
- `get-issue <issue-id>` - Get issue details
- `update-workpad <issue-id> <comment-body>` - Update or create workpad comment

## Authentication

The Linear GraphQL API key must be set in the `LINEAR_API_KEY` environment variable.
