"""Job search agent prototype.

Safe-by-default prototype that:
- loads a candidate profile
- ingests job links or board feeds
- scores jobs against your preferences
- generates drafts for resume bullets / cover letters
- queues applications for human approval

It does NOT auto-submit by default.

Usage examples:
  python job_agent_prototype.py init
  python job_agent_prototype.py ingest jobs.json
  python job_agent_prototype.py rank
  python job_agent_prototype.py draft
  python job_agent_prototype.py export queued_applications.csv

Inputs:
  profile.json      candidate profile
  jobs.json         job records
  applications.json tracked queue
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

BASE_DIR = Path.cwd()
PROFILE_PATH = BASE_DIR / "profile.json"
JOBS_PATH = BASE_DIR / "jobs.json"
APP_PATH = BASE_DIR / "applications.json"

DEFAULT_PROFILE = {
    "name": "Your Name",
    "title": "Software Engineer",
    "location": "New York, NY",
    "target_roles": ["software engineer", "backend engineer", "full stack engineer"],
    "must_haves": ["python", "api", "sql"],
    "nice_to_haves": ["aws", "docker", "fastapi", "react"],
    "avoid": ["senior staff", "leadership", "sales"],
    "work_authorization": "authorized",
    "salary_floor": 0,
    "remote_only": False,
    "hybrid_ok": True,
    "locations_ok": ["new york", "remote", "united states"],
    "resume_highlights": [
        "Built production APIs",
        "Improved performance and reliability",
        "Worked cross-functionally with product and design",
    ],
    "bio": "A concise 2-3 sentence summary of your background.",
}


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str = ""
    url: str = ""
    source: str = "manual"
    description: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    remote: Optional[bool] = None
    posted_at: str = ""
    status: str = "new"  # new, shortlisted, draft_ready, approved, applied, skipped
    score: float = 0.0
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class JobAgent:
    def __init__(self, profile_path: Path = PROFILE_PATH, jobs_path: Path = JOBS_PATH, app_path: Path = APP_PATH):
        self.profile_path = profile_path
        self.jobs_path = jobs_path
        self.app_path = app_path
        self.profile = self._load_json(profile_path, DEFAULT_PROFILE)
        self.jobs: List[Job] = [Job(**j) for j in self._load_json(jobs_path, [])]
        self.applications: List[Dict[str, Any]] = self._load_json(app_path, [])

    def _load_json(self, path: Path, default: Any):
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save_json(self, path: Path, data: Any):
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_all(self):
        self._save_json(self.profile_path, self.profile)
        self._save_json(self.jobs_path, [asdict(j) for j in self.jobs])
        self._save_json(self.app_path, self.applications)

    def init_files(self):
        if not self.profile_path.exists():
            self._save_json(self.profile_path, DEFAULT_PROFILE)
        if not self.jobs_path.exists():
            self._save_json(self.jobs_path, [])
        if not self.app_path.exists():
            self._save_json(self.app_path, [])

    def ingest_json(self, path: str):
        p = Path(path)
        raw = self._load_json(p, [])
        if isinstance(raw, dict):
            raw = raw.get("jobs", [raw])
        for item in raw:
            self.add_job(item)
        self.save_all()

    def ingest_csv(self, path: str):
        p = Path(path)
        with p.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.add_job(row)
        self.save_all()

    def add_job(self, raw: Dict[str, Any]):
        title = (raw.get("title") or raw.get("job_title") or "").strip()
        company = (raw.get("company") or raw.get("employer") or "").strip()
        url = (raw.get("url") or raw.get("job_url") or "").strip()
        if not title or not company:
            return
        job_id = raw.get("id") or self._make_job_id(company, title, url)
        if any(j.id == job_id for j in self.jobs):
            return
        job = Job(
            id=job_id,
            title=title,
            company=company,
            location=(raw.get("location") or "").strip(),
            url=url,
            source=(raw.get("source") or "manual").strip(),
            description=(raw.get("description") or raw.get("summary") or "").strip(),
            salary_min=self._to_float(raw.get("salary_min")),
            salary_max=self._to_float(raw.get("salary_max")),
            remote=self._to_bool(raw.get("remote")),
            posted_at=(raw.get("posted_at") or "").strip(),
        )
        self.jobs.append(job)

    def _make_job_id(self, company: str, title: str, url: str) -> str:
        base = f"{company}|{title}|{url}"
        return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:80]

    def _to_float(self, value: Any) -> Optional[float]:
        try:
            if value in (None, "", "null"):
                return None
            return float(str(value).replace(",", "").replace("$", ""))
        except Exception:
            return None

    def _to_bool(self, value: Any) -> Optional[bool]:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in {"true", "1", "yes", "y"}:
            return True
        if s in {"false", "0", "no", "n"}:
            return False
        return None

    def rank_jobs(self):
        for job in self.jobs:
            job.score, job.notes = self.score_job(job)
            if job.score >= 70 and job.status == "new":
                job.status = "shortlisted"
            job.updated_at = datetime.utcnow().isoformat() + "Z"
        self.jobs.sort(key=lambda j: j.score, reverse=True)
        self.save_all()

    def score_job(self, job: Job):
        profile = self.profile
        text = f"{job.title} {job.company} {job.location} {job.description}".lower()
        score = 0.0
        notes = []

        # Title fit
        title_bonus = 0
        for role in profile.get("target_roles", []):
            if role.lower() in job.title.lower():
                title_bonus += 18
        if title_bonus:
            score += min(title_bonus, 30)
            notes.append("title match")

        # Skills match
        must_haves = [s.lower() for s in profile.get("must_haves", [])]
        nice = [s.lower() for s in profile.get("nice_to_haves", [])]
        must_hits = sum(1 for s in must_haves if s in text)
        nice_hits = sum(1 for s in nice if s in text)
        if must_haves:
            score += 35 * (must_hits / len(must_haves))
        if nice:
            score += 15 * (nice_hits / len(nice))
        if must_hits:
            notes.append(f"{must_hits}/{len(must_haves)} must-have skills")

        # Location / remote
        if profile.get("remote_only"):
            if job.remote is True or "remote" in text:
                score += 15
            else:
                score -= 25
                notes.append("not remote")
        elif profile.get("hybrid_ok"):
            if job.remote is True or "remote" in text:
                score += 8

        # Location fit
        loc_ok = [s.lower() for s in profile.get("locations_ok", [])]
        if job.location:
            loc_l = job.location.lower()
            if any(loc in loc_l for loc in loc_ok):
                score += 10
            else:
                score -= 10
                notes.append("location mismatch")

        # Exclusions
        avoid = [s.lower() for s in profile.get("avoid", [])]
        if any(x in text for x in avoid):
            score -= 35
            notes.append("avoid keywords matched")

        # Salary floor if available
        floor = profile.get("salary_floor") or 0
        if floor and job.salary_max:
            if job.salary_max >= floor:
                score += 10
            else:
                score -= 15
                notes.append("below salary floor")

        # Short descriptions get a slight penalty because they are hard to judge
        if len(job.description) < 120:
            score -= 5

        score = max(0.0, min(100.0, score))
        return round(score, 1), "; ".join(notes) if notes else "good fit"

    def shortlist(self, threshold: float = 70.0):
        self.rank_jobs()
        return [j for j in self.jobs if j.score >= threshold and j.status in {"new", "shortlisted", "draft_ready"}]

    def draft_application(self, job: Job) -> Dict[str, Any]:
        profile = self.profile
        bullets = profile.get("resume_highlights", [])[:3]
        cover = (
            f"Dear Hiring Team at {job.company},\n\n"
            f"I'm applying for the {job.title} role. My background aligns well with the position because "
            f"I have experience in {', '.join(profile.get('must_haves', [])[:3])}. "
            f"Recent highlights include: {bullets[0] if bullets else 'delivering production software'}.\n\n"
            f"I'd welcome the chance to discuss how I can contribute.\n\n"
            f"Best,\n{profile.get('name', 'Your Name')}\n"
        )
        tailored_summary = (
            f"{profile.get('name', 'Your Name')} — {profile.get('title', '')}. "
            f"Targeting {job.title} at {job.company}. "
            f"Emphasize: {', '.join(profile.get('must_haves', [])[:5])}."
        )
        return {
            "job_id": job.id,
            "company": job.company,
            "title": job.title,
            "resume_summary": tailored_summary,
            "cover_letter": cover,
            "status": "draft_ready",
        }

    def draft_all(self, threshold: float = 70.0):
        self.rank_jobs()
        drafts = []
        for job in self.shortlist(threshold):
            draft = self.draft_application(job)
            drafts.append(draft)
            job.status = "draft_ready"
            job.updated_at = datetime.utcnow().isoformat() + "Z"
        self.applications.extend(drafts)
        self.save_all()
        return drafts

    def approve(self, job_id: str):
        for job in self.jobs:
            if job.id == job_id:
                job.status = "approved"
                job.updated_at = datetime.utcnow().isoformat() + "Z"
                self.save_all()
                return job
        raise KeyError(f"Job not found: {job_id}")

    def mark_applied(self, job_id: str, method: str = "manual"):
        for job in self.jobs:
            if job.id == job_id:
                job.status = "applied"
                job.updated_at = datetime.utcnow().isoformat() + "Z"
                self.applications.append(
                    {
                        "job_id": job.id,
                        "company": job.company,
                        "title": job.title,
                        "status": "applied",
                        "method": method,
                        "applied_at": datetime.utcnow().isoformat() + "Z",
                    }
                )
                self.save_all()
                return job
        raise KeyError(f"Job not found: {job_id}")

    def export_csv(self, out_path: str):
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["id", "company", "title", "location", "url", "score", "status", "notes"],
            )
            writer.writeheader()
            for j in self.jobs:
                writer.writerow(
                    {
                        "id": j.id,
                        "company": j.company,
                        "title": j.title,
                        "location": j.location,
                        "url": j.url,
                        "score": j.score,
                        "status": j.status,
                        "notes": j.notes,
                    }
                )

    def print_top(self, n: int = 10):
        self.rank_jobs()
        for i, j in enumerate(self.jobs[:n], start=1):
            print(f"{i:>2}. [{j.score:>5}] {j.company} — {j.title} ({j.location}) [{j.status}]")
            if j.url:
                print(f"    {j.url}")
            if j.notes:
                print(f"    notes: {j.notes}")


# --- Optional live fetchers ---

def fetch_greenhouse_board(board_url: str) -> List[Dict[str, Any]]:
    """Fetch jobs from a Greenhouse board page.

    board_url example:
      https://boards.greenhouse.io/company
    """
    if requests is None:
        raise RuntimeError("requests is required for live fetching")
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required for live fetching")

    html = requests.get(board_url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for a in soup.select("a[href*='/jobs/']"):
        href = a.get("href") or ""
        title = " ".join(a.get_text(" ", strip=True).split())
        if not title:
            continue
        url = href if href.startswith("http") else f"{urlparse(board_url).scheme}://{urlparse(board_url).netloc}{href}"
        jobs.append({"title": title, "company": urlparse(board_url).netloc, "url": url, "source": "greenhouse"})
    return dedupe_jobs(jobs)


def fetch_lever_jobs(company_slug: str) -> List[Dict[str, Any]]:
    """Fetch jobs from Lever public JSON endpoint.

    company_slug example:
      https://jobs.lever.co/companyname
    """
    if requests is None:
        raise RuntimeError("requests is required for live fetching")
    slug = company_slug.rstrip("/").split("/")[-1]
    url = f"https://jobs.lever.co/v0/postings/{slug}?mode=json"
    data = requests.get(url, timeout=30).json()
    jobs = []
    for row in data:
        jobs.append(
            {
                "title": row.get("text", ""),
                "company": row.get("categories", {}).get("team", slug),
                "location": row.get("categories", {}).get("location", ""),
                "url": row.get("hostedUrl", ""),
                "source": "lever",
                "description": row.get("descriptionPlain", ""),
            }
        )
    return dedupe_jobs(jobs)


def dedupe_jobs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        key = (r.get("company", "").lower(), r.get("title", "").lower(), r.get("url", "").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# --- CLI ---

def main(argv: List[str]):
    agent = JobAgent()
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1].lower()

    if cmd == "init":
        agent.init_files()
        print(f"Created {PROFILE_PATH.name}, {JOBS_PATH.name}, {APP_PATH.name}")
        return 0

    if cmd == "ingest":
        if len(argv) < 3:
            print("Usage: ingest <jobs.json|jobs.csv>")
            return 1
        path = argv[2]
        if path.lower().endswith(".csv"):
            agent.ingest_csv(path)
        else:
            agent.ingest_json(path)
        print(f"Ingested jobs from {path}")
        return 0

    if cmd == "rank":
        agent.rank_jobs()
        agent.print_top(20)
        return 0

    if cmd == "draft":
        threshold = float(argv[2]) if len(argv) > 2 else 70.0
        drafts = agent.draft_all(threshold=threshold)
        print(f"Created {len(drafts)} drafts (threshold {threshold})")
        for d in drafts[:10]:
            print(f"- {d['company']} — {d['title']}")
        return 0

    if cmd == "export":
        if len(argv) < 3:
            print("Usage: export <output.csv>")
            return 1
        agent.export_csv(argv[2])
        print(f"Exported to {argv[2]}")
        return 0

    if cmd == "approve":
        if len(argv) < 3:
            print("Usage: approve <job_id>")
            return 1
        job = agent.approve(argv[2])
        print(f"Approved: {job.company} — {job.title}")
        return 0

    if cmd == "applied":
        if len(argv) < 3:
            print("Usage: applied <job_id> [method]")
            return 1
        method = argv[3] if len(argv) > 3 else "manual"
        job = agent.mark_applied(argv[2], method=method)
        print(f"Marked applied: {job.company} — {job.title}")
        return 0

    if cmd == "fetch_greenhouse":
        if len(argv) < 3:
            print("Usage: fetch_greenhouse <board_url>")
            return 1
        rows = fetch_greenhouse_board(argv[2])
        out = argv[3] if len(argv) > 3 else "greenhouse_jobs.json"
        Path(out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Saved {len(rows)} jobs to {out}")
        return 0

    if cmd == "fetch_lever":
        if len(argv) < 3:
            print("Usage: fetch_lever <company_slug_or_url>")
            return 1
        rows = fetch_lever_jobs(argv[2])
        out = argv[3] if len(argv) > 3 else "lever_jobs.json"
        Path(out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Saved {len(rows)} jobs to {out}")
        return 0

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
