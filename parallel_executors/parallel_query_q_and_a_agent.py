import asyncio, json, os, time, traceback, threading
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.exceptions import OutputParserException

from utils.tools          import create_read_file_tool, create_list_files_tool
from utils.codebase_utils import WorktreeManager, get_file_hierarchy
from utils.clean_json     import repair_json_output

# ---------- prompt / parser you already have -------------
from q_and_a_evaluation.QandA_agent import prompt, parser, QandAResponse   # adjust import path
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
                        sem: asyncio.Semaphore) -> Dict[str, Any] | None:
    """
    row = {question, base_commit, diff_url, ...}
    Returns None on failure (already logged).
    """
    commit = row["base_commit"]
    wt_mgr = WorktreeManager(repo_root)

    async with sem:
        # --- create work-tree ---
        try:
            worktree_path = await asyncio.to_thread(wt_mgr.create, commit)
        except Exception as e:
            log_err(f"worktree create failed for {commit}", e)
            return None

        try:
            codebase_hierarchy = await asyncio.to_thread(get_file_hierarchy,
                                                         worktree_path)

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
                    return None

            text = raw["output"][0]["text"]
            try:
                parsed = parser.parse(text)
            except OutputParserException:
                parsed = repair_json_output(text, QandAResponse)

            return {
                "question": row["question"],
                "answer":   parsed.answer,
                "sources":  parsed.sources
            }

        except Exception as e:
            log_err(f"worker crashed for {commit}", e)
            print(f"❌ failed to answer {row["question"][:60]}")
            return {
                "question": row["question"],
                "answer":   "Failed to answer",
                "sources":  "Failed to answer"
            }
        finally:
            try:
                await asyncio.to_thread(wt_mgr.down, commit)
            except Exception as e:
                log_err(f"cleanup failed for {commit}", e)

async def run_parallel(q_path: Path, pr_path: Path, out_path: Path, repo_root: str):
    sem = asyncio.Semaphore(MAX_PARALLEL)

    with q_path.open() as qf, pr_path.open() as pf:
        questions = [json.loads(l) for l in qf]
        prs       = [json.loads(l) for l in pf]

    # join rows by order
    rows = [
        {
            "question":     q["question"],
            "base_commit":  pr["base_commit"],
            "diff_url":     pr.get("diff_url", "")
        }
        for q, pr in zip(questions, prs)
    ]

    tasks = [asyncio.create_task(answer_worker(r, repo_root, sem)) for r in rows]

    with out_path.open("w") as fh:
        for fut in asyncio.as_completed(tasks):
            res = await fut
            if res:
                fh.write(json.dumps(res) + "\n")
                print("✔︎ answered:", res["question"][:60])
            else:
                print(f"❌ failed to answer")

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--question_path", required=True, type=Path)
    p.add_argument("--pr_path",       required=True, type=Path)
    p.add_argument("--output_path",   required=True, type=Path)
    p.add_argument("--repo_root",     required=False,
                   default=os.getenv("CAL_COM_REPO_PATH"))
    args = p.parse_args()

    asyncio.run(run_parallel(args.question_path,
                             args.pr_path,
                             args.output_path,
                             args.repo_root))

    print("✅ Answers written to", args.output_path)
    print("⚠️ Errors (if any) logged to", ERR_FILE)

if __name__ == "__main__":
    main()