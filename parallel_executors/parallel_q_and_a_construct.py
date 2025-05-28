import asyncio, json, os, uuid
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict
import argparse

from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.exceptions import OutputParserException
from langchain_core.rate_limiters import InMemoryRateLimiter

from utils.codebase_utils import WorktreeManager          # your wrapper
from QandA_evaluation.construct_q_and_a import (                                # your helpers
    create_question_agent,
    create_answer_agent,
    question_parser,
    answer_parser,
    create_list_files_tool,
    create_read_file_tool,
    create_read_diff_from_link_tool,
    Question,
    Answer
)
from utils.clean_json import repair_json_output
from pathlib import Path
import traceback, time, threading


# ---------- config ---------- #
MAX_PARALLEL_PR   = 10        # adjust to your CPU/network/RL budget
ANTHROPIC_QPS     = 1        # QPS/org from your dashboard
MAX_EXAMPLES      = 60
OUT_DIR           = Path("logs/out") ; OUT_DIR.mkdir(exist_ok=True, parents=True)
# ---------------------------- #

load_dotenv()
llm = ChatAnthropic(
    model_name="claude-3-5-sonnet-20240620",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    rate_limiter=InMemoryRateLimiter(requests_per_second=ANTHROPIC_QPS),
    timeout=None,
    stop=None
)

_ERR_LOCK = threading.Lock()
ERR_LOG   = Path("logs/worktree_errors.log")
ERR_LOG.parent.mkdir(parents=True, exist_ok=True)

def log_error(msg: str, exc: Exception | None = None):
    """Append a message + optional traceback to a shared log file."""
    with _ERR_LOCK, ERR_LOG.open("a") as fh:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        fh.write(f"[{ts}] {msg}\n")
        if exc:
            fh.write("".join(traceback.format_exception(exc)) + "\n")
        fh.write("-" * 60 + "\n")
# ----------------------------------------------------------------




async def process_single_pr(
    pr: Dict, repo_path: str, sem: asyncio.Semaphore
) -> Dict | None:
    """One PR → question + answer  (returns dict with question/answer/sources)"""
    commit = pr["base_commit"]
    diff   = pr["diff_url"]
    wt_mgr = WorktreeManager(repo_path)

    async with sem:                       # obey global concurrency limit
        # --- 1. create worktree (runs in a thread) ---
        try:
            worktree_path = await asyncio.to_thread(wt_mgr.create, commit)
        except Exception as e:
            log_error(f"pr[{pr['number']}] | Failed to create worktree for {commit}", e)
            return None         # skip this PR gracefully
        
        try:
            codebase_files = await asyncio.to_thread(
                wt_mgr.get_worktree_file_hierarchy, commit
            )

            # --- 2. build tools that point *inside* this work-tree ---
            list_files   = create_list_files_tool(str(worktree_path))
            read_file    = create_read_file_tool(str(worktree_path))
            read_diff    = create_read_diff_from_link_tool(diff)
            tools        = [list_files, read_file, read_diff]

            # --- 3. question agent ---
            q_agent   = create_question_agent(llm, tools)
            q_text    = (await q_agent.ainvoke({
                "merged_pull_request": pr,
                "codebase_files": codebase_files
            }))["output"][0]["text"]

            try:
                parsed_q = question_parser.parse(q_text)
            except OutputParserException:
                parsed_q = repair_json_output(q_text, Question)

            # --- 4. answer agent (can run in parallel w/ others) ---
            a_agent = create_answer_agent(llm, tools)
            a_text  = (await a_agent.ainvoke({
                "question": parsed_q.question,
                "merged_pull_request": pr,
                "codebase_files": codebase_files
            }))["output"][0]["text"]

            try:
                parsed_a = answer_parser.parse(a_text)
            except OutputParserException:
                parsed_a = repair_json_output(a_text, Answer)

            return {
                "pr_number": pr["number"],
                "diff_url": pr["diff_url"],
                "question": parsed_q.question,
                "answer":   parsed_a.answer,
                "sources":  parsed_a.sources
            }
        
        except Exception as e:
            log_error(f"pr[{pr['number']}] | Agent failure for commit {commit}", e)
            return None

        finally:
            try:
                await asyncio.to_thread(wt_mgr.down, commit)
            except Exception as e:
                log_error(f"pr[{pr['number']}] |Worktree cleanup failed for {commit}", e)

# ---------------- main driver ---------------- #

async def main_async(repo_path: str, merged_prs_path: str):
    mutex = asyncio.Semaphore(MAX_PARALLEL_PR)
    questions, answers = [], []

    with open(merged_prs_path) as fh:
        prs = [json.loads(l) for _, l in zip(range(MAX_EXAMPLES), fh)]

    tasks = [asyncio.create_task(process_single_pr(pr, repo_path, mutex)) for pr in prs]
    for fut in asyncio.as_completed(tasks):
        result = await fut
        if result is not None:
            questions.append({
                "pr_number": result["pr_number"],
                "diff_url": result["diff_url"],
                "question": result["question"]
            })
            answers.append({
                "pr_number": result["pr_number"],
                "diff_url": result["diff_url"],
                "answer":  result["answer"],
                "sources": result["sources"]
            })
        else:
            log_error(f"Skipping PR due to failure")

    # --- write outputs ---
    (OUT_DIR / "sampled_questions.jsonl").write_text(
        "\n".join(json.dumps(q) for q in questions)
    )
    (OUT_DIR / "sampled_answers.jsonl").write_text(
        "\n".join(json.dumps(a) for a in answers)
    )

def main(repo_path: str, merged_prs_path: str):
    asyncio.run(main_async(repo_path, merged_prs_path))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", type=str, required=True)
    parser.add_argument("--merged_prs_path", type=str, required=True)
    args = parser.parse_args()
    main(args.repo_path, args.merged_prs_path)