"""Read-only branch audit. Historical PR names never authorize deletion."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def candidate(branch, comparison, *, default, open_heads):
    if branch["name"] == default or branch["name"] in open_heads or branch.get("protected"):
        return False
    return comparison.get("status") in {"identical", "behind"} and comparison.get("ahead_by") == 0


def audit(request, repository, default="main"):
    base = f"https://api.github.com/repos/{repository}"

    def paged(path):
        rows = []
        page = 1
        while True:
            batch = request(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    open_heads = {
        row["head"]["ref"]
        for row in paged(base + "/pulls?state=open")
        if (row["head"].get("repo") or {}).get("full_name") == repository
    }
    rows = []
    for branch in paged(base + "/branches"):
        sha = branch["commit"]["sha"]
        comparison = request(base + "/compare/" + urllib.parse.quote(default, safe="") + "..." + sha)
        rows.append(
            {
                "branch": branch["name"],
                "observed_sha": sha,
                "contained_candidate": candidate(branch, comparison, default=default, open_heads=open_heads),
            }
        )
    return {"mode": "read_only", "deleted_count": 0, "branches": rows}


def main():
    repository = os.environ["GH_REPOSITORY"]

    def request(url):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + os.environ["GH_TOKEN"],
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    print(json.dumps(audit(request, repository, os.environ.get("DEFAULT_BRANCH", "main")), indent=2))


if __name__ == "__main__":
    main()
