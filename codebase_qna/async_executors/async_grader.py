import asyncio, json, os, time, traceback, threading
from pathlib import Path
import pandas as pd
from typing import Dict, Any, List, Callable, Set
import aiofiles
import re
import shutil
import tempfile

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.agents import initialize_agent, AgentType
from langchain_core.exceptions import OutputParserException
from langchain_core.tools import Tool

# ---------- schema, prompt & parser (same as your script) -------------
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from codebase_qna.evaluate.grade_answer import CriterionGrade, GradedRubric, grade_rubric_prompt
from utils.json_repair import JSONRepairAgent, ClaudeJSONRepairAgent       # helper for invalid JSON\
from utils.codebase_utils import WorktreeManager

json_repair_agent = ClaudeJSONRepairAgent()

ERR_FILE = Path("logs/grade_agent_answer_errors.log")

MAX_PARALLEL = 10

_lock = threading.Lock()
def log_err(msg: str, exc: Exception | None = None):
    with _lock, ERR_FILE.open("a") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        if exc:
            fh.write("".join(traceback.format_exception(exc)))
        fh.write("-"*60 + "\n")



def display_markdown(df):
    print(df.to_markdown(index=False))

def dataframe_from_grades(gr: GradedRubric) -> pd.DataFrame:
    return pd.DataFrame([g.model_dump() for g in gr.graded_criteria])

# -------------------------------- Tools for Grading ------------------------------------- #

def create_file_exists_tool(worktree_path: str):
    def _file_exists(path) -> bool:
        """
        args: { "path": "relative/path/to/file" }
        """
        return (Path(worktree_path) / path).exists()
    return Tool(
        name="file_exists",
        func=_file_exists,
        description=(
            "Check whether a file exists in the codebase, please use this tool to verify if answers reference real files in the codebase"
            "This is codebase BEFORE the PR was applied."
            "Arguments: relative filepath."
        ),
    )

def create_list_changed_files_tool(row: Dict[str, str]):
    def _list_changed_files(input: str = "", **kwargs) -> List[str]:
        """
        Returns a list of changed files for the PR.
        Ignores any incoming arguments 
        """
        return row["changed_files"]
    return Tool(
        name="list_changed_files",
        func=_list_changed_files,
        description=(
            "List all files that were changed in the PR. "
            "Use this tool to verify if answers reference the correct files as per the PR diff. "
            "Arguments: caller input will be ignored"
        ),
    )

def create_get_diff_tool(row: Dict[str, str]):
    def _get_diff(input: str = "", **kwargs) -> str:
        """
        Returns the raw diff of the PR.
        Ignores any incoming arguments 
        """
        return row["diff"]
    return Tool(
        name="get_diff",
        func=_get_diff,
        description=(
            "Get the diff of the PR. Use this tool to validate whether the answer references the same content as the PR diff. "
            "Arguments: caller input will be ignored"
        ),
    )


# -------------------------------- Main Functions ------------------------------------- #

async def filter_and_clean_graded_rubrics(
    answer_path: Path, output_path: Path, cleaned_path: Path
) -> Set[str]:
    """
    Create a cleaned output by filtering out failed graded rubrics.
    Merges with previously successful entries in `cleaned_path` if it exists.
    """
    failed_pr_numbers = set()
    seen_prs = set()
    entries_to_keep = {}

    # Load new graded results and track failures
    async with aiofiles.open(output_path, mode="r") as f:
        async for line in f:
            entry = json.loads(line)
            pr = entry["pr_number"]
            if entry.get("score_percent") == "Failed to grade":
                failed_pr_numbers.add(pr)
            else:
                entries_to_keep[pr] = entry

    # Merge in previously successful entries from cleaned_path if it exists
    if cleaned_path.exists():
        async with aiofiles.open(cleaned_path, mode="r") as f:
            async for line in f:
                entry = json.loads(line)
                pr = entry["pr_number"]
                if pr not in failed_pr_numbers:
                    entries_to_keep[pr] = entry  # don't overwrite with failure

    print(f"❌ Failed to grade: {len(failed_pr_numbers)} PRs")
    print(f"✅ Total entries to keep: {len(entries_to_keep)}")

    # Write to a temporary file to allow safe overwrite
    with tempfile.NamedTemporaryFile("w", delete=False, dir=cleaned_path.parent, suffix=".jsonl") as tmp_f:
        tmp_path = Path(tmp_f.name)

    async with aiofiles.open(tmp_path, mode="w") as f:
        for pr, entry in entries_to_keep.items():
            await f.write(json.dumps(entry) + "\n")

    shutil.move(tmp_path, cleaned_path)
    print(f"✅ Cleaned file written to {cleaned_path}")
    return failed_pr_numbers

async def parse_json_output_grade_rubric(
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
        return parser.parse(raw)
    except OutputParserException:
        pass
    
    # regex extract json
    try:
        return parser.parse(re.search(r"```json\n(.*)\n```", raw).group(1))
    except Exception:
        pass

    # regex extract json by matching { ... }
    try:
        # Use non-greedy match and handle nested braces
        pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        match = re.search(pattern, raw)
        if match:
            return parser.parse(match.group(0))
    except Exception:
        pass

    # Try repair with raw JSON
    try:
        return await repair_agent.repair_json_output(raw, schema)
    except Exception:
        pass

    # Try repair with wrapped JSON
    try:
        return await repair_agent.repair_json_output("{" + raw, schema)
    except Exception as e:
        return default

async def grade_worker(
        row: Dict[str, str], sem: asyncio.Semaphore, llm: ChatAnthropic, 
        graded_rubric_parser: PydanticOutputParser, output_file: Path, worktree_manager: WorktreeManager
    ) -> Dict[str, Any] | None:

    """Grade one (question, answer, rubric) row.  Returns None on hard failure."""

    async with sem:
        try:
            wt_path = await worktree_manager.acquire(row["commit_hash"])
        except Exception as e:
            print(f"Failed to create worktree for {row['commit_hash']}", e)
            return None
        
        tools = [create_file_exists_tool(str(wt_path)), create_list_changed_files_tool(row), create_get_diff_tool(row)]

        agent = create_tool_calling_agent(llm, tools, prompt=grade_rubric_prompt)
        executor = AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations = None)

        tool_calls = []
        try:
            result = await executor.ainvoke(
                {
                "rubric":   json.dumps(row["rubric"]),
                "question": row["question"],
                "answer":   row["answer"],
                },
                return_intermediate=True
            )

            intermediate_steps = result.get("intermediate_steps", [])

            for action, observation in intermediate_steps:
                tool_calls.append({
                    "tool": action.tool,
                    "input": action.tool_input,
                    "output": observation
                })
            

            text = result["output"][0]["text"]
        

            graded = await parse_json_output_grade_rubric(
                text, GradedRubric, graded_rubric_parser, json_repair_agent,
                default = None
            )
         
        except Exception as e:
            print(f"LLM retry failed for Q='{row['question'][:40]}…'", e)
            #print entire stack trace
            print(traceback.format_exc())
            result = {
                "pr_number":     row["pr_number"],
                "commit_hash":   row["commit_hash"],
                "score_percent": "Failed to grade",
                "graded_rubric": "Failed to grade",
                "tool_calls":    tool_calls,
                "agent_answer":  row["answer"],
                "question":      row["question"],
                "rubric":        row["rubric"],   
            }
            async with aiofiles.open(output_file, 'a') as f:
                await f.write(json.dumps(result) + "\n")
                await f.flush()
            return result
        
        try:
            await worktree_manager.release(row["commit_hash"])
        except Exception as e:
            print(f"Failed to delete worktree for {row['commit_hash']}", e)

    

    # --- compute percentage score ---
    if graded is None:
        pct = "Failed to grade"
        graded = GradedRubric(graded_criteria=[CriterionGrade(name="Failed to grade", score=0, justification="Failed to grade")])
    
    else:
        total   = sum(c.score for c in graded.graded_criteria)
        maximum = 4 * len(graded.graded_criteria)
        pct     = round((total / maximum) * 100, 2) if maximum else 0.0

    # pretty-print to console (optional)
    display_markdown(dataframe_from_grades(graded))

    result = {
        "pr_number":     row["pr_number"],
        "commit_hash":   row["commit_hash"],
        "score_percent": pct,
        "graded_rubric": graded.model_dump(),
        "tool_calls": tool_calls,
        "agent_answer": row["answer"],
        "question":      row["question"],
        "rubric":        row["rubric"],   
    }

    # Write result immediately
    async with aiofiles.open(output_file, 'a') as f:
        await f.write(json.dumps(result) + "\n")
        await f.flush()
    print("✔︎ graded:", result["question"][:60])

    return result

# ---------- main orchestrator ------------------------------------------
async def run_parallel(
        merged_prs_path: Path,
        answer_path: Path, 
        rubric_path: Path, 
        out_path: Path, 
        llm: ChatAnthropic, 
        graded_rubric_parser: PydanticOutputParser, 
        resume: bool = False,
        num_to_grade: int | None = None,
        worktree_manager: WorktreeManager = None
    ):

    sem = asyncio.Semaphore(MAX_PARALLEL)

    a_dict = {obj["pr_number"]: obj for obj in map(json.loads, answer_path.read_text().splitlines())}
    r_dict = {obj["pr_number"]: obj for obj in map(json.loads, rubric_path.read_text().splitlines())}
    pr_dict = {obj["pr_number"]: obj for obj in map(json.loads, merged_prs_path.read_text().splitlines())}

    shared = a_dict.keys() & r_dict.keys()
    
    if "answer" not in a_dict[list(shared)[0]]:
        rows   = [
            {"pr_number": k, 
             "changed_files": pr_dict[k]["changed_files"],
             "diff": pr_dict[k]["diff"],
             "commit_hash": a_dict[k]["commit_hash"], 
             "question": a_dict[k]["question"], 
             "answer": a_dict[k]["response"], 
             "rubric": r_dict[k]["rubric"]} 
        for k in shared]
    else:
        rows   = [
            {"pr_number": k, 
             "changed_files": pr_dict[k]["changed_files"],
             "diff": pr_dict[k]["diff"],
             "commit_hash": a_dict[k]["commit_hash"], 
             "question": a_dict[k]["question"], 
             "answer": a_dict[k]["answer"], 
             "rubric": r_dict[k]["rubric"]
    } for k in shared]
    
    print(f"Grading {len(rows)} questions")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if resume:
        if not out_path.exists():
            print("Output file does not exist. Please run without --resume.")
            return
        failed_pr_numbers = await filter_and_clean_graded_rubrics(answer_path, out_path, out_path)
        rows = [row for row in rows if row["pr_number"] not in failed_pr_numbers]

    if num_to_grade:
        rows = rows[:num_to_grade]

    tasks = [asyncio.create_task(grade_worker(row, sem, llm, graded_rubric_parser, out_path, worktree_manager)) 
             for row in rows]

    results = await asyncio.gather(*tasks)
    print(f"✅ Completed {sum(r is not None for r in results)} graded results → {out_path}")

def main(args):

    ERR_FILE = Path(args.output_path).with_suffix(".errors.log")

    graded_rubric_parser = PydanticOutputParser(pydantic_object=GradedRubric)

    worktree_manager = WorktreeManager(repo_path=args.repo_path, task="grading")

    # ---------------- shared LLM + agent executor ------------------------ #
    load_dotenv()
    llm = ChatAnthropic(
        model_name=args.model,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        timeout=None,
        stop=None,
    )

    # set global variable for MAX_PARALLEL
    global MAX_PARALLEL
    MAX_PARALLEL = int(args.max_parallel)

    # --------------------------------------------------------------------- #

    asyncio.run(run_parallel(
        args.formatted_prs_path, args.answer_path, args.rubric_path, args.output_path, llm, graded_rubric_parser, args.resume, args.num_to_grade, worktree_manager
    ))

    print("✅ Graded rubrics written to", args.output_path)
    print("⚠️ Any errors logged to", ERR_FILE)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--repo_path",     required=True, type=Path)
    p.add_argument("--formatted_prs_path", required=True, type=Path)
    p.add_argument("--rubric_path",   required=True, type=Path)
    p.add_argument("--answer_path",   required=True, type=Path)
    p.add_argument("--output_path",   required=True, type=Path)
    p.add_argument("--resume",        required=False, action="store_true", default=False)
    p.add_argument("--num_to_grade",  required=False, default=None, type=int)
    p.add_argument("--model",        required=False, default="claude-3-5-sonnet-20240620")
    p.add_argument("--max_parallel", required=False, default=10)
    args = p.parse_args()
    main(args)

'''
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_grader.py \
    --repo_path     cal.com \
    --formatted_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --rubric_path   logs/calcom_cal.com_100pages_date2025-05-28/rubrics.jsonl \
    --answer_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude_code_answers.jsonl  \
    --output_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude_graded_rubrics.jsonl \
    --model claude-3-7-sonnet-20250219 \
    --resume --num_to_grade 15 --max_parallel 10

# Grade 3.7 sonnet
    
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_grader.py \
    --repo_path     cal.com \
    --formatted_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --rubric_path   logs/calcom_cal.com_100pages_date2025-05-28/rubrics_v4.jsonl \
    --answer_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.7_code_answers_v4.jsonl  \
    --output_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.7_graded_rubrics_v4.jsonl \
    --model claude-3-7-sonnet-20250219 \
    --resume --num_to_grade 50 --max_parallel 10

# Grade 3.5 sonnet

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_grader.py \
    --repo_path     cal.com \
    --formatted_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --rubric_path   logs/calcom_cal.com_100pages_date2025-05-28/rubrics_v4.jsonl \
    --answer_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.5_sonnet_code_answers_v4.jsonl  \
    --output_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.5_sonnet_graded_rubrics_v4.jsonl \
    --model claude-3-7-sonnet-20250219 \
    --num_to_grade 70 --max_parallel 10 --resume 

# Grade 3.5 haiku

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_grader.py \
    --repo_path     cal.com \
    --formatted_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --rubric_path   logs/calcom_cal.com_100pages_date2025-05-28/rubrics_v4.jsonl \
    --answer_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.5_haiku_code_answers_v4.jsonl  \
    --output_path   logs/calcom_cal.com_100pages_date2025-05-28/claude_code/claude3.5_haiku_graded_rubrics_v4.jsonl \
    --model claude-3-7-sonnet-20250219 \
    --num_to_grade 70 --max_parallel 10 --resume 


'''
