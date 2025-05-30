import json, asyncio, os, aiofiles
from pathlib import Path
from codebase_qna.async_executors.dataset_stages import generate_qna, generate_rubric
from utils.codebase_utils import WorktreeManager
from langchain_anthropic import ChatAnthropic
from dotenv import load_dotenv
from codebase_qna.construct.construct_qna import Question, Answer
from codebase_qna.construct.construct_rubric import Rubric
from utils.agent_tools import create_list_files_tool, create_read_file_tool, create_read_diff_tool
from codebase_qna.construct.construct_qna import question_prompt, answer_prompt, question_parser, answer_parser
from codebase_qna.construct.construct_rubric import rubric_prompt, rubric_parser
import shutil
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from typing import List

STAGES = [generate_qna, generate_rubric]

async def filter_and_clean_prs(merged_prs_path, qna_path, rubric_path):
    qna_seen = set()
    rubric_seen = set()
    failed = set()
    qna_lines = []
    rubric_lines = []
    to_run = []

    # Load existing QnA
    if os.path.exists(qna_path):
        async with aiofiles.open(qna_path) as f:
            async for line in f:
                data = json.loads(line)
                pr_number = data.get("pr_number")
                question = str(data.get('question')).lower()
                answer = data.get("answer", "").lower()
                if "failed to generate" in answer:
                    failed.add(pr_number)
                elif "worktree creation failed" in answer:
                    failed.add(pr_number)
                elif "failed to generate question" in question:
                    failed.add(pr_number)
                else:
                    qna_seen.add(pr_number)
                qna_lines.append(data)

    # Load existing Rubrics
    if os.path.exists(rubric_path):
        async with aiofiles.open(rubric_path) as f:
            async for line in f:
                data = json.loads(line)
                pr_number = data.get("pr_number")
                rubric = str(data.get('rubric')).lower()
                if "failed to generate" in rubric or "worktree creation failed" in rubric:
                    failed.add(pr_number)
                else:
                    rubric_seen.add(pr_number)
                rubric_lines.append(data)

    print(f"Failed Rubrics: {len(rubric_lines) - len(rubric_seen)}")
    print(f"Failed QnAs: {len(qna_lines) - len(qna_seen)}")


    # Read merged PRs and filter
    async with aiofiles.open(merged_prs_path) as f:
        async for line in f:
            pr = json.loads(line.strip())
            pr_number = pr["pr_number"]
            if pr_number not in qna_seen or pr_number not in rubric_seen or pr_number in failed:
                to_run.append(pr)

    # Remove entries for failed/incomplete PRs
    remaining_prs = {pr["pr_number"] for pr in to_run}

    async with aiofiles.open(qna_path, "w") as f:
        for item in qna_lines:
            if item["pr_number"] not in remaining_prs:
                await f.write(json.dumps(item) + "\n")

    async with aiofiles.open(rubric_path, "w") as f:
        for item in rubric_lines:
            if item["pr_number"] not in remaining_prs:
                await f.write(json.dumps(item) + "\n")

    
    print(f"Remaining PRs: {len(remaining_prs)} | To Run: {len(to_run)}")
    if len(to_run) == 0:
        print("No PRs to run")
        exit(0)
    
    return to_run


async def worker(pr, cfg, sem):
    async with sem:
        ctx = {
            "pr": pr,
            "error_log": [],
            **cfg,
        }
        # create & tear down worktree per PR
        try:
            commit = pr["base_commit"]
            wt_path = await cfg["worktree"].acquire(commit)
            ctx["codebase_files"] = cfg["worktree"].get_worktree_file_hierarchy(commit, max_depth = 3)

            ctx["tools"] = cfg["tool_factory"](str(wt_path), pr)
        
        except Exception as e:
            print(f"Error creating worktree: {e}")
            ctx["error_log"].append(
                {"stage": "create_worktree", "pr_number": pr["pr_number"], "error": str(e)}
            )
            async with aiofiles.open(ctx["qna_path"], "a") as f:
                await f.write(
                    json.dumps(
                        {
                            "pr_number": ctx["pr"]["pr_number"],
                            "commit_hash": ctx["pr"]["base_commit"],
                            "question": "Failed to generate question: Worktree creation failed",
                            "answer": "Failed to generate answer: Worktree creation failed",
                            "sources": "Failed to generate sources: Worktree creation failed",
                            "errors": ctx["error_log"],
                        }
                    )
                    + "\n"
                )
            async with aiofiles.open(ctx["rubric_path"], "a") as f:
                await f.write(
                    json.dumps(
                        {
                            "pr_number": ctx["pr"]["pr_number"],
                            "rubric": "Worktree creation failed",
                            "errors": ctx["error_log"],
                        }
                    )
                    + "\n"
                )

            return ctx

        for stage in STAGES:
            ctx = await stage(ctx)

        try:
            await cfg["worktree"].release(commit)   # cleanup
        except Exception as e:
            print(f"Error cleaning up worktree in {pr['pr_number']}: {ctx['error_log']}")
            ctx["error_log"].append(
                {"stage": "create_worktree", "pr_number": pr["pr_number"], "error": str(e)}
            )
            return ctx
        
        return ctx


async def main(args):
    repo_path = args.repo_path
    merged_prs_path = args.merged_prs_path
    output_dir = args.output_dir
    resume = args.resume
    max_concurrency = args.max_concurrency

    load_dotenv()
    llm = ChatAnthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model_name=args.model,
        timeout=None,
        stop=None
    )

    if os.path.exists('worktrees'):
        print("Removing worktrees")
        shutil.rmtree('worktrees', ignore_errors=True)

    # --- create dirs / files
    os.makedirs(output_dir, exist_ok=True)
    log_dir = Path(output_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    qna_path    = log_dir / "qna.jsonl"
    rubric_path = log_dir / "rubrics.jsonl"

    cfg = dict(
        llm=llm,
        worktree=WorktreeManager(repo_path, task = "dataset_pipeline"),
        question_prompt=question_prompt,
        answer_prompt=answer_prompt,
        rubric_prompt=rubric_prompt,
        question_parser=question_parser,
        answer_parser=answer_parser,
        rubric_parser=rubric_parser,
        QuestionModel=Question,
        AnswerModel=Answer,
        RubricModel=Rubric,
        qna_path=str(qna_path),
        rubric_path=str(rubric_path),
        tool_factory=lambda wt, pr: [
            create_list_files_tool(wt),
            create_read_file_tool(wt),
            create_read_diff_tool(pr)
        ],
    )

    sem = asyncio.Semaphore(max_concurrency)

    if args.resume:
        sem = asyncio.Semaphore(max_concurrency)
        prs_to_run = await filter_and_clean_prs(merged_prs_path, qna_path, rubric_path)
        if args.num_to_run:
            prs_to_run = prs_to_run[:args.num_to_run]
        tasks = [asyncio.create_task(worker(pr, cfg, sem)) for pr in prs_to_run]
    else:
        tasks = []
        async with aiofiles.open(merged_prs_path) as f:
            if args.num_to_run:
                for i in range(args.num_to_run):
                    line = await f.readline()
                    if not line:
                        break
                    pr = json.loads(line.strip())
                    tasks.append(asyncio.create_task(worker(pr, cfg, sem)))

            else:
                async for line in f:
                    pr = json.loads(line.strip())
                    tasks.append(asyncio.create_task(worker(pr, cfg, sem)))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    import argparse, asyncio
    p = argparse.ArgumentParser()
    p.add_argument("--repo_path", required=True)
    p.add_argument("--merged_prs_path", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--num_to_run", type=int, default=None)
    p.add_argument("--max_concurrency", type=int, default=10)
    p.add_argument("--model", required=True)
    args = p.parse_args()
    asyncio.run(main(args))


'''
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_dataset_pipeline.py \
    --repo_path cal.com \
    --merged_prs_path logs/calcom_cal.com_10pages_2025-05-27/merged_prs.jsonl \
    --output_dir logs/calcom_cal.com_10pages_v2/

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_dataset_pipeline.py \
    --repo_path cal.com \
    --merged_prs_path logs/calcom_cal.com_100pages_date2025-05-28T23:32:59.840148/merged_prs_formatted.jsonl \
    --output_dir logs/calcom_cal.com_100pages_date2025-05-28T23:32:59.840148/

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_dataset_pipeline.py \
    --repo_path cal.com \
    --merged_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --output_dir logs/calcom_cal.com_100pages_date2025-05-28 --resume 



PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_dataset_pipeline.py \
    --repo_path cal.com \
    --merged_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --output_dir logs/calcom_cal.com_100pages_date2025-05-28/ \
    --num_to_run 1 --max_concurrency 1 --resume \
    --model claude-3-7-sonnet-20250219

'''