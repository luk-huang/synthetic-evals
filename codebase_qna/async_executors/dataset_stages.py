import json, aiofiles, traceback
from typing import Dict, Any, Callable
from utils.json_repair import JSONRepairAgent
from langchain_core.exceptions import OutputParserException
from langchain.agents import create_tool_calling_agent
from codebase_qna.construct.construct_qna import create_question_agent, create_answer_agent
from langchain.agents import AgentExecutor
import re

json_repair = JSONRepairAgent(model_name="gpt-4.1-mini")

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
    pr_number = ctx["pr"]["pr_number"]
    raw = text.strip()

    # Try parser with wrapped JSON
    try:
        return parser.parse(raw)
    except OutputParserException as e:
        ctx["error_log"].append({
            "stage": f"parse_{model_label.lower()}",
            "pr_number": pr_number,
            "error": f"Attempt 0: Failed to parse/repair {model_label} from output: {str(e)}",
            "raw_output": raw[:500]
        })
        pass

    try:
        return parser.parse("{" + raw)
    except OutputParserException as e:
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
        return repair_agent.repair_json_output(raw, model_class)
    except Exception as e:
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

        print(f"Failed to parse/repair {text} from output: {str(e)}")

        return default

@stage
async def generate_qna(ctx):
    tools = ctx["tools"]
    llm = ctx["llm"]

    # -------- QUESTION --------
    q_tool_calls = []
    try:
        question_agent = create_question_agent(llm, tools)

         
        q_raw = await question_agent.ainvoke(
            {"merged_pull_request": ctx["pr"]["summary"], "codebase_files": ctx["codebase_files"]}
        )

        q_text = q_raw["output"][0]["text"]
        q_tool_calls = [
            {
                "tool": action.tool,
                "input": action.tool_input,
                "output": str(observation)[:20]
            }
            for action, observation in q_raw.get("intermediate_steps", [])
        ]

        q_parsed = await parse_json_output(
            q_text, 
            model_label="Question", 
            parser=ctx["question_parser"], 
            repair_agent=json_repair, 
            model_class=ctx["QuestionModel"], 
            ctx=ctx, 
            default=ctx["QuestionModel"](question="Failed to generate question")
        )

        ctx["question"] = q_parsed.question  # keep for rubric stag
        print(f"Question: \n {ctx['question']} \n \n")

    except Exception as e:
        print(f"Error generating question: {e}")
        ctx["error_log"].append(
            {"stage": "create_question_agent", "pr_number": ctx["pr"]["pr_number"], "error": str(e)}
        )
        ctx["question"] = "Failed to generate question"


    # -------- ANSWER ----------
    a_tool_calls = []
    try:
        answer_agent = create_answer_agent(llm, tools)
        a_raw = await answer_agent.ainvoke(
            {
                "question": ctx["question"],
                "merged_pull_request": ctx["pr"]["summary"],
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
            default=ctx["AnswerModel"](answer=a_text, sources=["Failed to generate sources"])
        )

        a_tool_calls = [
            {
                "tool": action.tool,
                "input": action.tool_input,
                "output": str(observation)[:20]
            }
            for action, observation in a_raw.get("intermediate_steps", [])
        ]

        ctx["answer"] = a_parsed.answer
        ctx["sources"] = a_parsed.sources

    except Exception as e:
        ctx["error_log"].append(
            {"stage": "create_answer_agent", "pr_number": ctx["pr"]["pr_number"], "error": str(e)}
        )
        print(f"Error generating answer: {e}")
        ctx["answer"] = "Failed to generate answer"
        ctx["sources"] = "Failed to generate sources"

    # -------- persist ---------
    async with aiofiles.open(ctx["qna_path"], "a") as f:
        await f.write(
            json.dumps(
                {
                    "pr_number": ctx["pr"]["pr_number"],
                    "commit_hash": ctx["pr"]["base_commit"],
                    "question": ctx["question"],
                    "answer": ctx["answer"],
                    "sources": ctx["sources"],
                    "question_tool_calls": q_tool_calls,
                    "answer_tool_calls": a_tool_calls,
                    "errors": ctx["error_log"],
                }
            )
            + "\n"
        )
        await f.flush()
        print(f"📝 qna appended for PR {ctx['pr']['pr_number']}")  # <— debug

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
    r_tool_calls = []

    try:
        # async with agent_log_to_file(ctx["pr"]["pr_number"], "rubric_agent"):
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
            default=ctx["RubricModel"](title="Failed to generate rubric", criteria=[])
        )

        r_tool_calls = [
            {
                "tool": action.tool,
                "input": action.tool_input,
                "output": str(observation)[:20]
            }
            for action, observation in raw.get("intermediate_steps", [])
        ]

    except Exception as e:
        ctx["error_log"].append(
            {"stage": "create_rubric_agent", "pr_number": ctx["pr"]["pr_number"], "error": str(e)}
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
                    "pr_number": ctx["pr"]["pr_number"],
                    "commit_hash": ctx["pr"]["base_commit"],
                    "rubric": rubric_output,
                    "errors": ctx["error_log"],
                    "rubric_tool_calls": r_tool_calls,
                }
            ) + "\n")
        await f.flush()
        print(f"📝 rubric appended for PR {ctx['pr']['pr_number']}")  # <— debug

    return ctx

