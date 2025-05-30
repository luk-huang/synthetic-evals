import aiohttp
import asyncio
import os
import json
import time
import argparse
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv
import json

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
BASE_URL = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN_ALT")

def format_pr_intent(pr_data: Dict) -> str:
    pr_number = pr_data.get("number", "")
    title = pr_data.get("title", "")
    body = pr_data.get("body", "") or "No description provided."

    all_commits = pr_data.get("all_commits", [])
    if all_commits:
        commit_blocks = "\n\n".join(
            f"- Commit [{c['sha'][:7]}]: {c['message'].strip()}\n  Diff:\n{c.get('diff', '').strip()}"
            for c in all_commits
        )
    else:
        commit_blocks = pr_data.get("diff", "")

    review_comments = pr_data.get("review_comments", [])
    if review_comments:
        review_lines = "\n".join(
            f"- {c['user']['login']}: {c['body'].strip()}" for c in review_comments
        )
    else:
        review_lines = "No reviewer comments."

    return f"""\
        Pull Request #{pr_number}: {title}

        PR Description:
        > {body}

        Commits and Diffs:
        {commit_blocks}

        Reviewer Comments:
        {review_lines}
    """.strip()


async def fetch_review_comments(session, review_comments_url):
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    async with session.get(review_comments_url, headers=headers) as resp:
        if resp.status == 200:
            return await resp.json()
        else:
            return []
        
async def fetch_diff(session, diff_url):
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff"
    }
    async with session.get(diff_url, headers=headers) as resp:
        if resp.status == 200:
                try:
                    raw_diff = await resp.read()
                    diff_text = raw_diff.decode('utf-8', errors='replace')
                except UnicodeDecodeError:
                    diff_text = "Error: Could not decode diff due to encoding issues."
                except Exception as e:
                    diff_text = f"Error reading/decoding diff: {str(e)}"
        else:
            error_message = await resp.text() # Try to get error message from API
            diff_text = f"Error: GitHub API responded with status {resp.status}. Message: {error_message}"

    return diff_text

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
    
async def fetch_commit_diff_details(owner: str, repo: str, commit_sha: str, commit_message: str, session, BASE_URL: str, GITHUB_TOKEN: str) -> Dict:
    """
    Fetches the diff for a single commit.
    """
    commit_url = f"{BASE_URL}/repos/{owner}/{repo}/commits/{commit_sha}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff"  # Request diff format
    }
    diff_text = ""

    try:
        async with session.get(commit_url, headers=headers) as diff_resp:
            if diff_resp.status == 200:
                try:
                    raw_diff = await diff_resp.read()
                    diff_text = raw_diff.decode('utf-8', errors='replace')
                except UnicodeDecodeError:
                    diff_text = "Error: Could not decode diff due to encoding issues."
                except Exception as e:
                    diff_text = f"Error reading/decoding diff: {str(e)}"
            else:
                error_message = await diff_resp.text() # Try to get error message from API
                print(error_message)
                diff_text = f"Error: GitHub API responded with status {diff_resp.status} for commit {commit_sha}. Message: {error_message}"
    except Exception as e:
        # Handle potential exceptions from the request itself (e.g., network issues)
        diff_text = f"Error fetching diff for commit {commit_sha}: {str(e)}"

    return {
        "sha": commit_sha,
        "message": commit_message,
        "diff": diff_text
    }

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
        commit_list = await commit_resp.json() if commit_resp.status == 200 else []

    commits: List[Dict] = []
    for c in commit_list:
        sha = c["sha"]
        message = c["commit"]["message"]

        commit_details = await fetch_commit_diff_details(owner, repo, sha, message, session, BASE_URL, GITHUB_TOKEN)
        commits.append(commit_details)


        
    review_comments_url = pr_data.get("review_comments_url")
    if review_comments_url:
        review_comments = await fetch_review_comments(session, review_comments_url)
    else:
        review_comments = []

    diff_url = pr_data.get("diff_url")
    if diff_url:
        diff = await fetch_diff(session, diff_url)
    else:
        diff = ""

    base_commit = commit_list[0]["sha"] if commit_list else None

    return {
        "base_commit": base_commit,
        "all_commits": commits,
        "review_comments": review_comments,
        "full_diff": diff,
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
            f.write(json.dumps(item) + "\n")

def extract_changed_files(diff_text: str) -> list[str]:
    changed_files = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git a/"):
            parts = line.split()
            if len(parts) >= 3:
                # Strip off the `a/` and `b/` prefixes, they should be the same
                a_path = parts[2][2:]  # Remove 'b/' prefix
                changed_files.append(a_path)
    return changed_files

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--output")
    parser.add_argument("--format_path", type=str, default=None)
    parser.add_argument("--num_to_format", type=int, default=None)
    args = parser.parse_args()

    if args.output is None:
        args.output = f"logs/{args.owner}_{args.repo}_{args.pages}pages_date{datetime.now().isoformat()}/merged_prs.jsonl"

    if args.format_path is None:
        start = time.time()
        print(f"Fetching merged PRs from last {args.pages} pages...")

        prs = asyncio.run(gather_merged_prs(args.owner, args.repo, args.pages))
        print(f"Fetched {len(prs)} merged PRs")

        save_jsonl(prs, args.output)
        print(f"Saved to {args.output}")
        print(f"Done in {time.time() - start:.2f} seconds")

    else:
        prs = []
        with open(args.format_path) as f:
            for line in f:
                prs.append(json.loads(line.strip()))

    formatted_prs = [
        {
            "pr_number": pr["number"],
            "base_commit": pr["base_commit"],
            "diff": pr["full_diff"],
            "changed_files": extract_changed_files(pr["full_diff"]),
            "summary": format_pr_intent(pr)
        } 
    for pr in prs[:args.num_to_format]
    ]

    save_jsonl(formatted_prs, args.output.replace(".jsonl", "_formatted.jsonl"))
    print(f"Saved formatted PRs to {args.output.replace('.jsonl', '_formatted.jsonl')}")

'''

PYTHONPATH=$(pwd) python codebase_qna/construct/get_merged_prs.py \
    --owner calcom \
    --repo cal.com \
    --pages 100 


PYTHONPATH=$(pwd) python codebase_qna/construct/get_merged_prs.py \
    --owner calcom \
    --repo cal.com \
    --pages 100 \
    --format_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs.jsonl \
    --num_to_format 500
'''