"""
parallel_grade_rubrics.py
-------------------------
Grade many (question, answer) pairs with their rubric **in parallel**.

Run:
    python parallel_grade_rubrics.py \
        --rubric_path   data/rubrics.jsonl \
        --question_path data/questions.jsonl \
        --answer_path   data/answers.jsonl   \
        --output_path   logs/graded_rubrics.jsonl
"""
import asyncio, json, os, time, traceback, threading
from pathlib import Path
import pandas as pd
from typing import Dict, Any, List, Callable
import aiofiles

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.exceptions import OutputParserException

# ---------- schema, prompt & parser (same as your script) -------------
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from codebase_qna.evaluate.grade_answer import CriterionGrade, GradedRubric, grade_rubric_prompt
from utils.json_repair import JSONRepairAgent        # helper for invalid JSON\

json_repair_agent = JSONRepairAgent()

ERR_FILE = Path("logs/grade_agent_answer_errors.log")

MAX_PARALLEL = 10

_lock = threading.Lock()
def log_err(msg: str, exc: Exception | None = None):
    with _lock, ERR_FILE.open("a") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        if exc:
            fh.write("".join(traceback.format_exception(exc)))
        fh.write("-"*60 + "\n")

# --------------------------------------------------------------------- #

async def parse_json_output(
    text: str,
    schema: type[BaseModel],
    parser: PydanticOutputParser,
    repair_agent,
    default: Dict[str, Any]
) -> Dict[str, Any]:
    """Attempts multiple strategies to parse or repair a model-compatible JSON output."""
    raw = text.strip()

    # Try parser with wrapped JSON
    try:
        return parser.parse("{" + raw)
    except OutputParserException:
        pass

    # Try repair with raw JSON
    try:
        return repair_agent.repair_json_output(raw, schema)
    except Exception:
        pass

    # Try repair with wrapped JSON
    try:
        return repair_agent.repair_json_output("{" + raw, schema)
    except Exception as e:
        return default
    
def display_markdown(df):
    print(df.to_markdown(index=False))

def dataframe_from_grades(gr: GradedRubric) -> pd.DataFrame:
    return pd.DataFrame([g.model_dump() for g in gr.graded_criteria])

async def grade_worker(row: Dict[str, str], sem: asyncio.Semaphore, executor: AgentExecutor, 
                      graded_rubric_parser: PydanticOutputParser, output_file: Path) -> Dict[str, Any] | None:
    """Grade one (question, answer, rubric) row.  Returns None on hard failure."""
    async with sem:
        for attempt in (1, 2):                       # single retry
            try:
                raw = await executor.ainvoke({
                    "rubric":   json.dumps(row["rubric"]),
                    "question": row["question"],
                    "answer":   row["answer"],
                })
                break                                # success
            except Exception as e:
                if attempt == 1:
                    await asyncio.sleep(4)           # back-off then retry
                    continue
                log_err(f"LLM retry failed for Q='{row['question'][:40]}…'", e)
                result = {
                    "question": row["question"],
                    "graded_rubric": "Failed to grade",
                    "score_percent": 0.0
                }
                async with aiofiles.open(output_file, 'a') as f:
                    await f.write(json.dumps(result) + "\n")
                    await f.flush()
                return result

    text = raw["output"]
    graded = await parse_json_output(
        text, GradedRubric, graded_rubric_parser, json_repair_agent,
        default = {
                    "pr_number": row["pr_number"],
                    "commit_hash": row["commit_hash"],
                    "question": row["question"],
                    "graded_rubric": "Failed to grade",
                    "score_percent": 0.0
                }
    )

    graded = GradedRubric(**graded)

    # --- compute percentage score ---
    total   = sum(c.score for c in graded.graded_criteria)
    maximum = 4 * len(graded.graded_criteria)
    pct     = round((total / maximum) * 100, 2) if maximum else 0.0

    # pretty-print to console (optional)
    display_markdown(dataframe_from_grades(graded))

    result = {
        "pr_number":     row["pr_number"],
        "commit_hash":   row["commit_hash"],
        "question":      row["question"],
        "graded_rubric": graded.model_dump(),
        "score_percent": pct,
    }

    # Write result immediately
    async with aiofiles.open(output_file, 'a') as f:
        await f.write(json.dumps(result) + "\n")
        await f.flush()
    print("✔︎ graded:", result["question"][:60])

    return result

# ---------- main orchestrator ------------------------------------------
async def run_parallel(
        a_path: Path, 
        r_path: Path, 
        out_path: Path, 
        executor: AgentExecutor, 
        graded_rubric_parser: PydanticOutputParser, 
        resume: bool = False
    ):

    sem = asyncio.Semaphore(MAX_PARALLEL)

    a_dict = {obj["pr_number"]: obj["answer"]
              for obj in map(json.loads, a_path.read_text().splitlines())}
    r_dict = {obj["pr_number"]: obj["rubric"]
              for obj in map(json.loads, r_path.read_text().splitlines())}

    shared = a_dict.keys() & r_dict.keys()
    rows   = [{"question": k, "answer": a_dict[k], "rubric": r_dict[k]} for k in shared]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if resume:
        if out_path.exists():
            with out_path.open() as f:
                existing_pr_numbers = [json.loads(line)["pr_number"] for line in f]
            rows = [row for row in rows if row["pr_number"] not in existing_pr_numbers]
        else:
            print("Output file does not exist. Please run without --resume.")
            return
    else:
        if out_path.exists():
            print("Do you want to delete the existing output file? (y/n)")
            if input() == "y":
                out_path.unlink()
            else:
                print("Exiting...")
                return

    tasks = [asyncio.create_task(grade_worker(row, sem, executor, graded_rubric_parser, out_path)) 
             for row in rows]

    results = await asyncio.gather(*tasks)
    print(f"✅ Completed {sum(r is not None for r in results)} graded results → {out_path}")

def main(args):
    if args.output_path is None:
        args.output_path = Path(args.rubric_path).parent / f"{args.answer_path.stem}_graded_rubrics.jsonl"

    ERR_FILE = Path(args.output_path).with_suffix("errors.log")

    graded_rubric_parser = PydanticOutputParser(pydantic_object=GradedRubric)

    # ---------------- shared LLM + agent executor ------------------------ #
    load_dotenv()
    llm = ChatAnthropic(
        model_name="claude-3-5-sonnet-20240620",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        timeout=None,
        stop=None,
    )
    agent  = create_tool_calling_agent(llm, tools=[], prompt=grade_rubric_prompt)
    executor = AgentExecutor.from_agent_and_tools(agent=agent, tools=[], verbose=False)

    # --------------------------------------------------------------------- #

    asyncio.run(run_parallel(
        args.answer_path, args.rubric_path, args.output_path, executor, graded_rubric_parser, args.resume
    ))

    print("✅ Graded rubrics written to", args.output_path)
    print("⚠️ Any errors logged to", ERR_FILE)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rubric_path",   required=True, type=Path)
    p.add_argument("--answer_path",   required=True, type=Path)
    p.add_argument("--output_path",   required=True, type=Path)
    p.add_argument("--resume",        required=False, action="store_true", default=False)
    args = p.parse_args()
    main(args)

'''
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_grader.py \
    --rubric_path   data/rubrics.jsonl \
    --question_path data/questions.jsonl \
    --answer_path   data/answers.jsonl   \
    --output_path   logs/graded_rubrics.jsonl
    
'''
