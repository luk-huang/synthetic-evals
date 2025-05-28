import aiohttp
import asyncio
import os
import json
import time
import argparse
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
BASE_URL = "https://api.github.com"

async def fetch_pr_page(session: aiohttp.ClientSession, owner: str, repo: str, page: int) -> List[Dict]:
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
    params = {
        "state": "closed",
        "sort": "updated",
        "direction": "desc",
        "per_page": 100,
        "page": page
    }
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    async with session.get(url, headers=headers, params=params) as response:
        if response.status != 200:
            print(f"Failed to fetch page {page}: {response.status}")
            return []
        return await response.json()

async def fetch_pr_details(session: aiohttp.ClientSession, owner: str, repo: str, pr_number: int) -> Dict:
    pr_url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}"
    commits_url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/commits"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    async with session.get(pr_url, headers=headers) as pr_resp:
        pr_data = await pr_resp.json() if pr_resp.status == 200 else {}

    async with session.get(commits_url, headers=headers) as commit_resp:
        commit_data = await commit_resp.json() if commit_resp.status == 200 else []

    base_commit = commit_data[0]["sha"] if commit_data else None

    return {
        "base_commit": base_commit,
        "diff_url": pr_data.get("diff_url"),
        "additions": pr_data.get("additions"),
        "deletions": pr_data.get("deletions"),
        "changed_files": pr_data.get("changed_files")
    }

async def gather_merged_prs(owner: str, repo: str, num_pages: int) -> List[Dict]:
    async with aiohttp.ClientSession() as session:
        # Step 1: fetch all closed PRs from recent pages
        page_tasks = [fetch_pr_page(session, owner, repo, page) for page in range(1, num_pages + 1)]
        all_pages = await asyncio.gather(*page_tasks)
        all_prs = [pr for page in all_pages for pr in page if pr.get("merged_at")]

        # Step 2: fetch metadata for each merged PR
        detail_tasks = [
            fetch_pr_details(session, owner, repo, pr["number"])
            for pr in all_prs
        ]
        details = await asyncio.gather(*detail_tasks)

        final = []
        for pr, detail in zip(all_prs, details):
            final.append({
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["html_url"],
                "body": pr.get("body", ""),
                "merged_at": pr["merged_at"],
                "merge_date": pr["merged_at"][:10],
                "user": pr["user"]["login"],
                "baseRef": pr["base"]["ref"],
                "headRef": pr["head"]["ref"],
                **detail
            })
        return final

def save_jsonl(data: List[Dict], output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        for item in data:
            f.write(json.dumps(item) + "\\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--output")
    args = parser.parse_args()

    if args.output is None:
        args.output = f"logs/{args.owner}_{args.repo}_{args.pages}pages_date{datetime.now().isoformat()}/merged_prs.jsonl"

    start = time.time()
    print(f"Fetching merged PRs from last {args.pages} pages...")

    prs = asyncio.run(gather_merged_prs(args.owner, args.repo, args.pages))
    print(f"Fetched {len(prs)} merged PRs")

    save_jsonl(prs, args.output)
    print(f"Saved to {args.output}")
    print(f"Done in {time.time() - start:.2f} seconds")
