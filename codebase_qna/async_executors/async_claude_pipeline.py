import asyncio, json, os, argparse
from pathlib import Path
from dotenv import load_dotenv
from utils.codebase_utils import WorktreeManager          # your helper
from codebase_qna.prompt_templates.prompts import ANSWER_SYSTEM_PROMPT  # your prompt
import aiofiles

MAX_CONCURRENCY = 10

# ---------- per-question coroutine ------------------------------------------
async def run_question(item: dict, manager: WorktreeManager,
                       sem: asyncio.Semaphore, output_file: Path, args: argparse.Namespace) -> dict | None:
    """Process one {commit_hash, question} record and write its answer to file."""
    commit_hash = item["commit_hash"]
    question     = item["question"]

    # Compose full Claude prompt
    full_prompt = (
        f"{ANSWER_SYSTEM_PROMPT}\n"
        "Also for the purposes of the question, answer as thoroughly as possible "
        "and try to think of the true intent of the question. Therefore refrain "
        "from asking too many clarifications. The junior is trying to figure out "
        "how to implement or fix something. Therefore answer as thoroughly as possible.\n\n"
        f"{question}"
    )

    async with sem:                     # limit overall concurrency
        # 1) create work-tree
        try:
            wt_path = await manager.acquire(commit_hash)       
        except Exception as e:
            print(f"[{commit_hash}] work-tree error → {e}")
            return None

        # 2) fire off Claude Code
        for attempt in range(3):
            print(f"[{commit_hash}] running Claude Code …")
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", full_prompt, "--model", args.model,
                "--allowedTools", f"Read({wt_path})",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # 3) live-stream & capture 
        
            captured_lines: list[str] = []
            async for raw in proc.stdout:                # type: ignore[attr-defined]
                line = raw.decode()
                print(f"[{commit_hash}] {line}", end="")  # live echo
                captured_lines.append(line)

            await proc.wait()
            response = "".join(captured_lines).strip()
            if response != "(no content)":
                break
            else:
                print(f"[{commit_hash}] Claude Code returned (no content), retrying...")
                await asyncio.sleep(10)
            if attempt == 2:
                response = "(no content)"
                print(f"[{commit_hash}] Claude Code has failed 3 times, skipping...")
                

        # 4) clean up work-tree
        try:
            await manager.release(commit_hash)
        except Exception as e:
            print(f"[{commit_hash}] cleanup error → {e}")

        # 5) write result to file immediately
        result = {
            "pr_number": item["pr_number"],
            "commit_hash": commit_hash,
            "question": question,
            "answer": response
        }
        async with aiofiles.open(output_file, 'a') as f:
            await f.write(json.dumps(result) + "\n")
            await f.flush()
        print(f"✅ wrote answer for {commit_hash}")

        return result


# ---------- async driver -----------------------------------------------------
async def main_async(args):
    load_dotenv()

    questions_path = Path(args.questions_file)
    output_path = Path(
        args.output_file or questions_path.parent / "claude_code_answers.jsonl"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    
    pr_numbers_already_ran = []

    if args.resume:
        if output_path.exists():
            with output_path.open() as f:
                for line in f:
                    data = json.loads(line)
                    if data["answer"] != "(no content)":
                        pr_numbers_already_ran.append(data["pr_number"])
            print(f"✅ Found {len(pr_numbers_already_ran)} Questions Already Answered")
        else:
            print("Output file does not exist. Please run without --resume.")
            return
    else:
        if output_path.exists():
            # Ask user for permission to overwrite
            overwrite = input(f"Output file {output_path} already exists. Do you want to overwrite it? (y/n): ")
            if overwrite.lower() != "y":
                print("Exiting...")
                return
            else:
                output_path.unlink()

    questions = []
    with questions_path.open() as f:
        for line in f:
            data = json.loads(line)
            if data["pr_number"] not in pr_numbers_already_ran:
                questions.append(data)

    print(f"✅ Running {len(questions)} Questions")

    manager = WorktreeManager(args.repo_path, task = "claude_qna")
    sem     = asyncio.Semaphore(args.max_concurrency)

    tasks = [asyncio.create_task(run_question(q, manager, sem, output_path, args))
             for q in questions]
    results = await asyncio.gather(*tasks)

    print(f"✅ completed {sum(rec is not None for rec in results)} answers → {output_path}")

# ---------- CLI --------------------------------------------------------------
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--repo_path",      required=True)
    p.add_argument("--questions_file", required=True)
    p.add_argument("--model", default="claude-3-7-sonnet-20250219")
    p.add_argument("--output_file")
    p.add_argument("--resume", required=False, action="store_true", default=False)
    p.add_argument("--max_concurrency", type=int, default=MAX_CONCURRENCY,
                   help="simultaneous Claude invocations")
    
    asyncio.run(main_async(p.parse_args()))

'''
# default is claude-3-7-sonnet-20250219
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_claude_pipeline.py \
    --repo_path cal.com/ \
    --questions_file  logs/calcom_cal.com_10pages_2025-05-27/qna.jsonl 
    --model claude-3-7-sonnet-20250219

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_claude_pipeline.py \
    --repo_path cal.com/ \
    --questions_file logs/calcom_cal.com_10pages_v2/qna.jsonl \
    --output_file logs/calcom_cal.com_10pages_v2/claude_code/claude_code_answers.jsonl \
    --model claude-3-7-sonnet-20250219

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_claude_pipeline.py \
    --repo_path cal.com/ \
    --questions_file logs/calcom_cal.com_100pages_date2025-05-28/qna.jsonl \
    --output_file logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude_code_answers.jsonl \
    --model claude-3-7-sonnet-20250219


# Claude 3.7 Sonnet 20250219

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_claude_pipeline.py \
    --repo_path cal.com/ \
    --questions_file logs/calcom_cal.com_100pages_date2025-05-28/qna_v4.jsonl \
    --output_file logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.7_code_answers_v4.jsonl \
    --model claude-3-7-sonnet-20250219

# Claude 3.5 Sonnet 20240620

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_claude_pipeline.py \
    --repo_path cal.com/ \
    --questions_file logs/calcom_cal.com_100pages_date2025-05-28/qna_v4.jsonl \
    --output_file logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.5_sonnet_code_answers_v4.jsonl \
    --model claude-3-5-sonnet-20240620

# Claude 3.5 Haiku 20241022
    
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_claude_pipeline.py \
    --repo_path cal.com/ \
    --questions_file logs/calcom_cal.com_100pages_date2025-05-28/qna_v4.jsonl \
    --output_file logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.5_haiku_code_answers_v4.jsonl \
    --model claude-3-5-haiku-20241022


'''