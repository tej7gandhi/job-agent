# Job Search Agent Prototype

A safe-by-default Python CLI for organizing a job search.

It can:
- load a candidate profile
- ingest jobs from JSON or CSV
- score jobs against your preferences
- generate basic tailored application drafts
- track approvals and applied jobs
- fetch public job postings from Greenhouse and Lever boards

It does **not** auto-submit applications.

## Files

- `job_agent_prototype.py` — main CLI prototype
- `profile.example.json` — sample profile you can copy to `profile.json`
- `requirements.txt` — Python dependencies
- `.gitignore` — ignores local/runtime files and secrets

## Quick start

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows
# .venv\Scripts\activate

pip install -r requirements.txt
python job_agent_prototype.py init
```

That creates:
- `profile.json`
- `jobs.json`
- `applications.json`

## Common commands

```bash
python job_agent_prototype.py ingest jobs.json
python job_agent_prototype.py rank
python job_agent_prototype.py draft
python job_agent_prototype.py draft 75
python job_agent_prototype.py approve <job_id>
python job_agent_prototype.py applied <job_id> manual
python job_agent_prototype.py export pipeline.csv
```

## Public job-board fetchers

```bash
python job_agent_prototype.py fetch_greenhouse https://boards.greenhouse.io/company
python job_agent_prototype.py fetch_lever company-slug
```

These commands save results to JSON files that you can ingest afterward.

## Notes

- Keep the repo private if you store your real profile or application history.
- Do not commit real secrets, passwords, or personal data you do not want in Git.
- The prototype is meant to assist you, not submit applications unattended.

## Suggested workflow

1. Update `profile.json` with your preferences.
2. Pull jobs from public sources or create your own `jobs.json`.
3. Run `rank` to shortlist matches.
4. Run `draft` to generate application drafts.
5. Review, approve, and mark applications manually.
