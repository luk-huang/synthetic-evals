import json, aiofiles, traceback
from typing import Dict, Any, Callable
from utils.json_repair import JSONRepairAgent
from langchain_core.exceptions import OutputParserException
from langchain.agents import create_tool_calling_agent
from codebase_qna.construct.construct_qna import create_question_agent, create_answer_agent
from langchain.agents import AgentExecutor
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import sys

@asynccontextmanager
async def agent_log_to_file(pr_number: int, task: str):
    log_file = Path(f"logs/agent_executor_logs/{pr_number}_{task}.log")
    log_file.parent.mkdir(exist_ok=True)

    # Logger for langchain.agents
    handler = logging.FileHandler(log_file, mode="a")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    agent_logger = logging.getLogger("langchain.agents")
    agent_logger.addHandler(handler)

    # Suppress print() output by redirecting sys.stdout/stderr
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        with open(log_file, "a") as f:
            sys.stdout = f
            sys.stderr = f
            yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        agent_logger.removeHandler(handler)
        handler.close()


json_repair = JSONRepairAgent(model_name="gpt-4o-mini")

def stage(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: wrap every stage with error capture + timing."""
    async def _wrapper(ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await fn(ctx)
        except Exception as e:
            ctx["error_log"].append(
                {"stage": fn.__name__, "trace": traceback.format_exc()}
            )
            # mark failure but keep ctx so downstream stages still run
            ctx[f"{fn.__name__}_error"] = str(e)
            return ctx
    return _wrapper

async def parse_json_output(
    text: str,
    model_label: str,  # e.g. "Answer", "Rubric"
    parser: Callable[[str], Any],
    repair_agent,
    model_class: Any,
    ctx: Dict[str, Any],
    default: Dict[str, Any]
) -> Dict[str, Any]:
    """Attempts multiple strategies to parse or repair a model-compatible JSON output."""
    pr_number = ctx["pr"]["number"]
    raw = text.strip()

    # Try parser with wrapped JSON
    try:
        return parser.parse("{" + raw)
    except OutputParserException:
        ctx["error_log"].append({
            "stage": f"parse_{model_label.lower()}",
            "pr_number": pr_number,
            "error": f"Attempt 0: Failed to parse/repair {model_label} from output: {str(e)}",
            "raw_output": raw[:500]
        })
        pass

    # Try repair with raw JSON
    try:
        return repair_agent.repair_json_output(raw, model_class)
    except Exception:
        ctx["error_log"].append({
            "stage": f"parse_{model_label.lower()}",
            "pr_number": pr_number,
            "error": f"Attempt 1: Failed to parse/repair {model_label} from output: {str(e)}",
            "raw_output": raw[:500]
        })
        pass

    # Try repair with wrapped JSON
    try:
        return repair_agent.repair_json_output("{" + raw, model_class)
    except Exception as e:
        ctx["error_log"].append({
            "stage": f"parse_{model_label.lower()}",
            "pr_number": pr_number,
            "error": f"Attempt 2: Failed to parse/repair {model_label} from output: {str(e)}",
            "raw_output": raw[:500]
        })
        return default

@stage
async def generate_qna(ctx):
    tools = ctx["tools"]
    llm = ctx["llm"]

    # -------- QUESTION --------
    try:
        question_agent = create_question_agent(llm, tools)
        with agent_log_to_file(ctx["pr"]["number"], "question_agent"):
            q_raw = await question_agent.ainvoke(
                {"merged_pull_request": ctx["pr"], "codebase_files": ctx["codebase_files"]}
            )

        q_text = q_raw["output"][0]["text"]

        q_parsed = await parse_json_output(
            q_text, 
            model_label="Question", 
            parser=ctx["question_parser"], 
            repair_agent=json_repair, 
            model_class=ctx["QuestionModel"], 
            ctx=ctx, 
            default={"question": "Failed to generate question"}
        )

        ctx["question"] = q_parsed.question  # keep for rubric stag
        
    except Exception as e:
        print(f"Error generating question: {e}")
        ctx["error_log"].append(
            {"stage": "create_question_agent", "pr_number": ctx["pr"]["number"], "error": str(e)}
        )
        ctx["question"] = "Failed to generate question"


    # -------- ANSWER ----------
    try:
        answer_agent = create_answer_agent(llm, tools)
        with agent_log_to_file(ctx["pr"]["number"], "answer_agent"):
            a_raw = await answer_agent.ainvoke(
                {
                    "question": ctx["question"],
                    "merged_pull_request": ctx["pr"],
                    "codebase_files": ctx["codebase_files"],
                }
            )

        a_text = a_raw["output"][0]["text"]
        a_parsed = await parse_json_output(
            a_text, 
            model_label="Answer", 
            parser=ctx["answer_parser"], 
            repair_agent=json_repair, 
            model_class=ctx["AnswerModel"], 
            ctx=ctx, 
            default={"answer": "Failed to generate answer", "sources": "Failed to generate sources"}
        )

        ctx["answer"] = a_parsed.answer
        ctx["sources"] = a_parsed.sources

    except Exception as e:
        ctx["error_log"].append(
            {"stage": "create_answer_agent", "pr_number": ctx["pr"]["number"], "error": str(e)}
        )
        ctx["answer"] = "Failed to generate answer"
        ctx["sources"] = "Failed to generate sources"

    # -------- persist ---------
    async with aiofiles.open(ctx["qna_path"], "a") as f:
        await f.write(
            json.dumps(
                {
                    "pr_number": ctx["pr"]["number"],
                    "commit_hash": ctx["pr"]["base_commit"],
                    "question": ctx["question"],
                    "answer": ctx["answer"],
                    "sources": ctx["sources"],
                }
            )
            + "\n"
        )
        await f.flush()
        print(f"📝 qna appended for PR {ctx['pr']['number']}")  # <— debug

    return ctx

@stage
async def generate_rubric(ctx):
    llm = ctx["llm"]
    tools = ctx["tools"]

    rubric_agent = create_tool_calling_agent(llm, tools, prompt=ctx["rubric_prompt"])
    rubric_agent =AgentExecutor.from_agent_and_tools(
        agent=rubric_agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )

    r_parsed = None

    try:
        with agent_log_to_file(ctx["pr"]["number"], "rubric_agent"):
            raw = await rubric_agent.ainvoke(
                {
                    "query": ctx.get("question", ""),
                    "answer": ctx.get("answer", ""),
                    "sources": ctx.get("sources", []),
                }
            )

        output = raw.get("output")
        if isinstance(output, list):
            r_text = output[0].get("text", "")
        elif isinstance(output, dict) and "text" in output:
            r_text = output["text"]
        else:
            r_text = output if isinstance(output, str) else ""

        r_parsed = await parse_json_output(
            r_text, 
            model_label="Rubric", 
            parser=ctx["rubric_parser"], 
            repair_agent=json_repair, 
            model_class=ctx["RubricModel"], 
            ctx=ctx, 
            default={"rubric": "Failed to generate rubric"}
        )

    except Exception as e:
        ctx["error_log"].append(
            {"stage": "create_rubric_agent", "pr_number": ctx["pr"]["number"], "error": str(e)}
        )
    
    rubric_output = (
        r_parsed.model_dump()
        if r_parsed else 
        ctx.get("rubric", "Failed to generate rubric")
    )

    
    async with aiofiles.open(ctx["rubric_path"], "a") as f:
        await f.write(
            json.dumps(
                {
                    "pr_number": ctx["pr"]["number"],
                    "rubric": rubric_output,
                    "errors": ctx["error_log"],
                }
            ) + "\n")
        await f.flush()
        print(f"📝 rubric appended for PR {ctx['pr']['number']}")  # <— debug

    return ctx

