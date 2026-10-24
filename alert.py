#!/usr/bin/env python3
"""Opens a GitHub issue when a data source breaks, and closes it when the source works again.

Run by the build workflow after run.py. Reads data/health.json from this build and
data/health-last.json from the build before. A problem has to show up two builds in a row
before an issue is opened, so a one-off network hiccup doesn't send an email.

    python alert.py [--build-failed]
"""
import argparse
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = os.path.join(HERE, "data", "health.json")
LAST = os.path.join(HERE, "data", "health-last.json")
API = "https://api.github.com/repos/{}"

TITLES = {
    "nblr": "Data problem: nblR results and box scores",
    "pbp": "Data problem: nblR play-by-play",
    "feed": "Data problem: NBL match centre feed",
    "build": "Site build is failing",
}
WHAT_IT_MEANS = {
    "nblr": "nblR is the main data source. If the site build is also failing, the live site isn't updating; "
            "otherwise the NBL feed is filling in new games for now.",
    "pbp": "The site still builds, but play-by-play stats (lineups, on/off, shots, game flow) stop updating.",
    "feed": "The feed only matters when nblR is behind, so nothing is broken on the site yet, "
            "but new games won't show up quickly if nblR falls behind.",
    "build": "The build failed for a reason other than the data checks. The live site keeps its last version "
             "until a build succeeds.",
}


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class GitHub:
    def __init__(self, repo, token):
        self.base = API.format(repo)
        self.s = requests.Session()
        self.s.headers.update({"authorization": f"Bearer {token}", "accept": "application/vnd.github+json"})

    def call(self, method, path, **kw):
        r = self.s.request(method, self.base + path, timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    def open_alerts(self):
        """Open alert issues by source, found by the marker in their body."""
        found = {}
        for issue in self.call("GET", "/issues", params={"state": "open", "per_page": 100}):
            body = issue.get("body") or ""
            for source in TITLES:
                if f"<!-- source-alert: {source} -->" in body and "pull_request" not in issue:
                    found[source] = issue
        return found


def body(source, problems, run_url, owner):
    lines = [f"<!-- source-alert: {source} -->", f"@{owner} the last two builds found a problem:", ""]
    lines += [f"- {p}" for p in problems]
    lines += ["", WHAT_IT_MEANS[source], "", f"Latest build log: {run_url}", "",
              "This closes itself once a build finds the source working again."]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-failed", action="store_true")
    args = parser.parse_args()

    now, last = read(NOW), read(LAST)
    problems = dict(now.get("problems", {}))
    fine = set(now.get("ok", []))
    if args.build_failed and not problems:
        problems["build"] = ["run.py stopped with an error (see the log)."]
    elif not args.build_failed:
        fine.add("build")
    before = dict(last.get("problems", {}))
    if last.get("build_failed") and not before:
        before["build"] = True

    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or (repo or "/").split("/")[0]

    for source, texts in problems.items():
        print(f"{source}: {'; '.join(texts)}")
    if not repo or not token:
        print("No GITHUB_TOKEN; not touching issues.")
    else:
        gh = GitHub(repo, token)
        open_issues = gh.open_alerts()
        for source, texts in problems.items():
            text = body(source, texts, run_url, owner)
            issue = open_issues.get(source)
            if issue:
                if issue.get("body") != text:  # an edit doesn't send another email
                    gh.call("PATCH", f"/issues/{issue['number']}", json={"body": text})
            elif source in before:
                issue = gh.call("POST", "/issues", json={"title": TITLES[source], "body": text})
                print(f"Opened issue #{issue['number']} for {source}")
        for source, issue in open_issues.items():
            if source in fine and source not in problems:
                gh.call("POST", f"/issues/{issue['number']}/comments",
                        json={"body": f"Working again as of {now.get('checked', 'the latest build')}. Closing."})
                gh.call("PATCH", f"/issues/{issue['number']}", json={"state": "closed", "state_reason": "completed"})
                print(f"Closed issue #{issue['number']} ({source} is working again)")

    # Remember this build for next time.
    now["build_failed"] = args.build_failed
    os.makedirs(os.path.dirname(LAST), exist_ok=True)
    with open(LAST, "w", encoding="utf-8") as f:
        json.dump(now, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
