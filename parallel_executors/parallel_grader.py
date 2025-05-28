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
from typing import Dict, Any, List

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.exceptions import OutputParserException

# ---------- schema, prompt & parser (same as your script) -------------
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from utils.clean_json import repair_json_output        # helper for invalid JSON
# ----------------------------------------------------------------------

MAX_PARALLEL = 10
OUT_FILE     = Path("logs/graded_agent_answers.jsonl")
ERR_FILE     = Path("logs/grade_agent_answer_errors.log")
OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
ERR_FILE.parent.mkdir(parents=True, exist_ok=True)

# -------------------- Schema ----------------------------------------- #
class CriterionGrade(BaseModel):
    name: str
    score: int = Field(ge=0, le=4)
    justification: str

class GradedRubric(BaseModel):
    graded_criteria: List[CriterionGrade]
# --------------------------------------------------------------------- #

parser = PydanticOutputParser(pydantic_object=GradedRubric)

GRADE_SYSTEM_PROMPT = """
You are a senior software engineer tasked with grading an answer using a rubric.

Each rubric criterion contains:
- A name
- A description
- A list of levels from 0 (worst) to 4 (best) with detailed performance descriptions

Your task is to:
- Match the answer against each rubric item
- Assign a score (0–4)
- Provide a short justification
"""

grade_prompt = ChatPromptTemplate.from_messages([
    ("system", GRADE_SYSTEM_PROMPT + "\n{format_instructions}"),
    ("user", "Rubric to apply: {rubric}"),
    ("user", "Question: {question}"),
    ("user", "Answer: {answer}"),
    ("placeholder", "{agent_scratchpad}")
]).partial(format_instructions=parser.get_format_instructions())

# ---------------- error logger --------------------------------------- #
_lock = threading.Lock()
def log_err(msg: str, exc: Exception | None = None):
    with _lock, ERR_FILE.open("a") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        if exc:
            fh.write("".join(traceback.format_exception(exc)))
        fh.write("-"*60 + "\n")
# --------------------------------------------------------------------- #

# ---------------- shared LLM + agent executor ------------------------ #
load_dotenv()
llm = ChatAnthropic(
    model_name="claude-3-5-sonnet-20240620",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    timeout=None,
    stop=None,
)

agent  = create_tool_calling_agent(llm, tools=[], prompt=grade_prompt)
executor = AgentExecutor.from_agent_and_tools(agent=agent, tools=[], verbose=False)
# --------------------------------------------------------------------- #

def display_markdown(df):
    print(df.to_markdown(index=False))

def dataframe_from_grades(gr: GradedRubric) -> pd.DataFrame:
    return pd.DataFrame([g.dict() for g in gr.graded_criteria])

async def grade_worker(row: Dict[str, str], sem: asyncio.Semaphore) -> Dict[str, Any] | None:
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
                return {
                    "question": row["question"],
                    "graded_rubric": "Failed to grade",
                    "score_percent": 0.0
                }

    text = raw["output"][0]["text"]
    try:
        graded = parser.parse(text)
    except OutputParserException:
        graded = repair_json_output(text, GradedRubric)

    # --- compute percentage score ---
    total   = sum(c.score for c in graded.graded_criteria)
    maximum = 4 * len(graded.graded_criteria)
    pct     = round((total / maximum) * 100, 2) if maximum else 0.0

    # pretty-print to console (optional)
    display_markdown(dataframe_from_grades(graded))

    return {
        "question":      row["question"],
        "graded_rubric": graded.dict(),
        "score_percent": pct,
    }

# ---------- main orchestrator ------------------------------------------
async def run_parallel(q_path: Path, a_path: Path, r_path: Path, out_path: Path):
    sem = asyncio.Semaphore(MAX_PARALLEL)

    # load files into dicts keyed by question text
    q_dict = {obj["question"]: obj
              for obj in map(json.loads, q_path.read_text().splitlines())}
    a_dict = {obj["question"]: obj["answer"]
              for obj in map(json.loads, a_path.read_text().splitlines())}
    r_dict = {obj["question"]: obj["rubric"]
              for obj in map(json.loads, r_path.read_text().splitlines())}

    shared = q_dict.keys() & a_dict.keys() & r_dict.keys()
    rows   = [{"question": k, "answer": a_dict[k], "rubric": r_dict[k]} for k in shared]

    tasks = [asyncio.create_task(grade_worker(row, sem)) for row in rows]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        async for fut in asyncio.as_completed(tasks):
            res = await fut
            if res:
                fh.write(json.dumps(res) + "\n")
                print("✔︎ graded:", res["question"][:60])
            else:
                print(f"❌ failed to grade")

    print(f"✅ Wrote {len(rows)} graded results → {out_path}")

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--rubric_path",   required=True, type=Path)
    p.add_argument("--question_path", required=True, type=Path)
    p.add_argument("--answer_path",   required=True, type=Path)
    p.add_argument("--output_path",   required=True, type=Path)
    args = p.parse_args()

    asyncio.run(run_parallel(args.question_path,
                             args.answer_path,
                             args.rubric_path,
                             args.output_path))

    print("✅ Graded rubrics written to", args.output_path)
    print("⚠️ Any errors logged to", ERR_FILE)

if __name__ == "__main__":
    main()
