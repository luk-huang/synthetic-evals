import asyncio, json, os, time, traceback, threading
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.exceptions import OutputParserException

from utils.agent_tools          import create_read_file_tool, create_list_files_tool
from utils.codebase_utils import WorktreeManager, get_file_hierarchy
from utils.json_repair     import JSONRepairAgent

json_repair_agent = JSONRepairAgent()

# ---------- prompt / parser you already have -------------
from codebase_qna.evaluate.qna_agent import prompt, parser, QandAResponse   # adjust import path
# ----------------------------------------------------------

MAX_PARALLEL  = 10                     # concurrent workers
OUT_FILE      = Path("logs/generated_q_and_a_agent_answers.jsonl")
ERR_FILE      = Path("logs/q_and_a_agent_answer_errors.log")
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
ERR_FILE.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
def log_err(msg: str, exc: Exception | None = None):
    with _lock, ERR_FILE.open("a") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        if exc:
            fh.write("".join(traceback.format_exception(exc)))
        fh.write("-"*60 + "\n")

# -------- shared LLM instance (async-ready) ---------------
load_dotenv()
llm = ChatAnthropic(
    model_name="claude-3-5-sonnet-20240620",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    timeout=None,
    stop=None,
)
# ----------------------------------------------------------

async def answer_worker(row: Dict[str, str],
                        repo_root: str,
                        sem: asyncio.Semaphore,
                        out_file) -> None:
    """
    row = {question, base_commit, diff_url, ...}
    Writes result directly to out_file when done.
    """
    commit = row["base_commit"]
    wt_mgr = WorktreeManager(repo_root)

    async with sem:
        # --- create work-tree ---
        try:
            worktree_path = await asyncio.to_thread(wt_mgr.create, commit)
        except Exception as e:
            log_err(f"worktree create failed for {commit}", e)
            return

        try:
            codebase_hierarchy = await asyncio.to_thread(get_file_hierarchy,
                                                         str(worktree_path))

            # tools bound to THIS work-tree
            read_file   = create_read_file_tool(str(worktree_path))
            list_files  = create_list_files_tool(str(worktree_path))
            tools       = [read_file, list_files]

            agent  = create_tool_calling_agent(llm, tools=tools, prompt=prompt)
            exe    = AgentExecutor.from_agent_and_tools(agent=agent,
                                                        tools=tools,
                                                        verbose=False)

            # --- invoke LLM (with single retry) ---
            try:
                raw = await exe.ainvoke({
                    "query":             row["question"],
                    "codebase_hierarchy": codebase_hierarchy
                })
            except Exception as e:
                await asyncio.sleep(4)
                try:
                    raw = await exe.ainvoke({
                        "query": row["question"],
                        "codebase_hierarchy": codebase_hierarchy
                    })
                except Exception as e:
                    log_err("LLM retry failed", e)
                    return

            text = raw["output"][0]["text"]
            try:
                parsed = parser.parse(text)
            except OutputParserException:
                parsed = json_repair_agent.repair_json_output(text, QandAResponse)

            result = {
                "question": row["question"],
                "answer":   parsed.answer,
                "sources":  parsed.sources
            }
            
            # Write result directly to file
            with _lock:
                out_file.write(json.dumps(result) + "\n")
                out_file.flush()
                print("✔︎ answered:", result["question"][:60])

        except Exception as e:
            log_err(f"worker crashed for {commit}", e)
            print(f"❌ failed to answer {row["question"][:60]}")
            result = {
                "question": row["question"],
                "answer":   "Failed to answer",
                "sources":  "Failed to answer"
            }
            with _lock:
                out_file.write(json.dumps(result) + "\n")
                out_file.flush()
        finally:
            try:
                await asyncio.to_thread(wt_mgr.down, commit)
            except Exception as e:
                log_err(f"cleanup failed for {commit}", e)

async def run_parallel(q_path: Path, out_path: Path, repo_root: str, resume: bool = False):
    sem = asyncio.Semaphore(MAX_PARALLEL)

    questions = [json.loads(l) for l in q_path.open()]

    if resume:
        if out_path.exists():
            with out_path.open() as f:
                existing_pr_numbers = [json.loads(line)["pr_number"] for line in f]
            questions = [q for q in questions if q["pr_number"] not in existing_pr_numbers]
            print(f"Resuming from {len(existing_pr_numbers)} existing quetions already answered")
        else:
            print("Output file does not exist. Please run without --resume.")
            return

    # join rows by order
    rows = [
        {
            "pr_number":    q["pr_number"],
            "question":     q["question"],
            "base_commit":  q["base_commit"]
        }
        for q in questions
    ]

    # Create output file and keep it open
    with out_path.open("w") as out_file:
        tasks = [
            asyncio.create_task(answer_worker(r, repo_root, sem, out_file))
            for r in rows
        ]
        # Wait for all tasks to complete
        await asyncio.gather(*tasks)

def main(args):
    asyncio.run(run_parallel(args.question_path,
                             args.output_path,
                             args.repo_root,
                             args.resume))

    print("✅ Answers written to", args.output_path)
    print("⚠️ Errors (if any) logged to", ERR_FILE)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--question_path", required=True, type=Path)
    p.add_argument("--output_path",   required=True, type=Path)
    p.add_argument("--resume",        required=False, action="store_true", default=False)
    p.add_argument("--repo_root",     required=False,
                   default=os.getenv("CAL_COM_REPO_PATH"))
    args = p.parse_args()
    main(args)

'''
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_generate_answers.py \
    --question_path data/questions.jsonl \
    --output_path   logs/answers.jsonl \
    --resume        true \
    --repo_root     cal.com/

'''
