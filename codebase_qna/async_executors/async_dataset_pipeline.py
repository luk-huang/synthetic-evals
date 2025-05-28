import json, asyncio, os, aiofiles
from pathlib import Path
from codebase_qna.aync_executors.dataset_stages import generate_qna, generate_rubric
from utils.codebase_utils import WorktreeManager
from langchain_anthropic import ChatAnthropic
from dotenv import load_dotenv
from codebase_qna.construct.construct_qna import Question, Answer
from codebase_qna.construct.construct_rubric import Rubric
from utils.agent_tools import create_list_files_tool, create_read_file_tool, create_read_diff_from_link_tool
from codebase_qna.construct.construct_qna import question_prompt, answer_prompt, question_parser, answer_parser
from codebase_qna.construct.construct_rubric import rubric_prompt, rubric_parser
import shutil

MAX_CONCURRENCY = 10          # tweak for API / CPU limits
STAGES = [generate_qna, generate_rubric]

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
            wt_path = cfg["worktree"].create(commit)
            ctx["codebase_files"] = cfg["worktree"].get_worktree_file_hierarchy(commit)
            ctx["tools"] = cfg["tool_factory"](str(wt_path), pr["diff_url"])
        except Exception as e:
            print(f"Error creating worktree: {e}")
            ctx["error_log"].append(
                {"stage": "create_worktree", "pr_number": pr["number"], "error": str(e)}
            )
            async with aiofiles.open(ctx["qna_path"], "a") as f:
                await f.write(
                    json.dumps(
                        {
                            "pr_number": ctx["pr"]["number"],
                            "commit_hash": ctx["pr"]["base_commit"],
                            "question": "Worktree creation failed",
                            "answer": "Worktree creation failed",
                            "sources": "Worktree creation failed",
                            "errors": ctx["error_log"],
                        }
                    )
                    + "\n"
                )
            async with aiofiles.open(ctx["rubric_path"], "a") as f:
                await f.write(
                    json.dumps(
                        {
                            "pr_number": ctx["pr"]["number"],
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
            cfg["worktree"].down(commit)   # cleanup
        except Exception as e:
            print(f"Error cleaning up worktree in {pr['number']}: {ctx['error_log']}")
            ctx["error_log"].append(
                {"stage": "create_worktree", "pr_number": pr["number"], "error": str(e)}
            )
            return ctx
        
        return ctx


async def main(repo_path, merged_prs_path):
    load_dotenv()
    llm = ChatAnthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        model_name="claude-3-5-sonnet-20240620",
        timeout=None,
        stop=None
    )

    if os.path.exists('worktrees'):
        print("Removing worktrees")
        shutil.rmtree('worktrees', ignore_errors=True)

    # --- create dirs / files
    log_dir = Path("logs") / Path(merged_prs_path).parent.name
    log_dir.mkdir(parents=True, exist_ok=True)
    qna_path    = log_dir / "qna.jsonl"
    rubric_path = log_dir / "rubrics.jsonl"

    cfg = dict(
        llm=llm,
        worktree=WorktreeManager(repo_path),
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
        tool_factory=lambda wt, diff: [
            create_list_files_tool(wt),
            create_read_file_tool(wt),
            create_read_diff_from_link_tool(diff)
        ],
    )

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    # ---- launch tasks
    tasks = []
    async with aiofiles.open(merged_prs_path) as f:
        async for line in f:
            pr = json.loads(line.strip())
            tasks.append(asyncio.create_task(worker(pr, cfg, sem)))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    import argparse, asyncio
    p = argparse.ArgumentParser()
    p.add_argument("--repo_path", required=True)
    p.add_argument("--merged_prs_path", required=True)
    args = p.parse_args()
    asyncio.run(main(args.repo_path, args.merged_prs_path))
