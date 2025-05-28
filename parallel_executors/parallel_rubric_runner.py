import asyncio, json, os
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.exceptions import OutputParserException
import traceback, time, threading

# ---------- import prompt / parser from your existing module ---------- #
from QandA_evaluation.construct_rubric import rubric_prompt, rubric_parser, Rubric  # <-- adjust path
from utils.clean_json import repair_json_output                # your helper
# --------------------------------------------------------------------- #

MAX_PARALLEL = 10         # how many agents to run simultaneously
OUT_FILE     = Path("logs/rubrics_parallel.jsonl")
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

ERR_FILE     = Path("logs/rubric_errors.log")
ERR_FILE.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()

# ---------- tiny helper ------------------------------------------------- #
def log_err(msg: str, exc: Exception | None = None):
    with _lock, ERR_FILE.open("a") as fh:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        fh.write(f"[{ts}] {msg}\n")
        if exc:
            fh.write("".join(traceback.format_exception(exc)))
        fh.write("-" * 60 + "\n")

load_dotenv()
llm = ChatAnthropic(
    model_name="claude-3-5-sonnet-20240620",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    timeout=None,
    stop=None
)

# We do NOT give the agent a "write_rubric" tool; we collect in memory.
agent = create_tool_calling_agent(llm, tools=[], prompt=rubric_prompt)
rubric_executor = AgentExecutor.from_agent_and_tools(
    agent=agent, tools=[], verbose=False
)

# -------------- worker -------------------------------- #
async def create_single_rubric(row: Dict[str, Any], sem: asyncio.Semaphore) -> Dict | None:
    """One question/answer/sources dict -> rubric dict"""
    async with sem:           # limit concurrency
        try:
            raw = await rubric_executor.ainvoke({
            "query":   row["question"],
                "answer":  row["answer"],
                "sources": row.get("sources", "")
            })
        except Exception as e:
            log_err(f"Failed to create rubric for {row['question']}", e)
            return None

    text = raw["output"][0]["text"]
    try:
        parsed = rubric_parser.parse(text)
    except OutputParserException:
        parsed = repair_json_output(text, Rubric)
    return {
        "question": row["question"],
        "rubric":   parsed.model_dump()
    }

# -------------- main ---------------------------------- #
async def run_parallel(question_path: Path, answer_path: Path):
    sem = asyncio.Semaphore(MAX_PARALLEL)

    # ingest the two JSONL files
    with question_path.open() as qf, answer_path.open() as af:
        questions = [json.loads(l) for l in qf]
        answers   = [json.loads(l) for l in af]

    rows = [
        {"question": q["question"],
         "answer":   a["answer"],
         "sources":  a.get("sources", "")}
        for q, a in zip(questions, answers)
    ]

    tasks = [asyncio.create_task(create_single_rubric(r, sem)) for r in rows]

    with OUT_FILE.open("w") as fh:
        for fut in asyncio.as_completed(tasks):
            result = await fut
            
            if result is None:
                log_err(f"Skipping rubric for due to failure")
            else:
                fh.write(json.dumps(result) + "\n")
                print("✔︎ wrote rubric for:", result["question"][:60])

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--question_path", required=True, type=Path)
    p.add_argument("--answer_path",   required=True, type=Path)
    args = p.parse_args()
    asyncio.run(run_parallel(args.question_path, args.answer_path))
    print("✅ All rubrics written to", OUT_FILE)

if __name__ == "__main__":
    main()
