import subprocess
import json
import os
from dotenv import load_dotenv
import argparse
from pathlib import Path
from utils.codebase_utils import WorktreeManager
from codebase_qna.prompt_templates.prompts import ANSWER_SYSTEM_PROMPT

def main(args):
    load_dotenv()

    if args.output_file is None:
        args.output_file = Path(args.questions_file).parent / "claude_code_answers.jsonl"

    # Ensure the output directory exists
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
    QUESTIONS_FILE = args.questions_file
    REPO_PATH = args.repo_path
    OUTPUT_FILE = args.output_file

    worktree_manager = WorktreeManager(REPO_PATH)

    # Read questions
    with open(QUESTIONS_FILE, 'r') as qf: 
        questions = [json.loads(line) for line in qf]

    for item in questions:
        commit_hash = item["commit_hash"]
        question = item["question"]
        CLAUDE_CODE_PROMPT = f"""
        {ANSWER_SYSTEM_PROMPT}
        Also for the purposes of the question, answer as thoroughly as possible and try to think of the true intent of the question. Therefore refrain from asking too many clarifications.
        {question}
        """.format(ANSWER_SYSTEM_PROMPT=ANSWER_SYSTEM_PROMPT)

        # Create a temporary directory for the worktree
        try:
            worktree_path = worktree_manager.create(commit_hash)
        except Exception as e:
            print(f"Error creating worktree for commit {commit_hash}: {e}")
            continue

        try:

            # Run Claude Code in non-interactive mode
            print(f"Running Claude Code for commit {commit_hash}")
            process = subprocess.Popen(
                ["claude", "-p", CLAUDE_CODE_PROMPT, "--allowedTools", f"Read({worktree_path})"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1  # Line-buffered
            )

            # Stream output line by line
            for line in process.stdout:
                print(line, end='')  # Already includes newline

            process.wait()

            # Capture the response
            response = process.stdout.read().strip()

            # Append the response to the output file
            with open(OUTPUT_FILE, 'a') as outf:
                json.dump({
                    "pr_number": item["pr_number"],
                    "commit_hash": commit_hash,
                    "question": question,
                    "response": response
                }, outf)
                outf.write('\n')

            print(f"Generated answer for pr {item['pr_number']}")

        except Exception as e:
            print(f"Error generating answer for commit {commit_hash}: {e}")
            continue

        try:
            worktree_manager.down(commit_hash)
        except Exception as e:
            print(f"Error removing worktree for commit {commit_hash}: {e}")
            continue

        break

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", type=str, required=True)
    parser.add_argument("--questions_file", type=str, required=True)
    parser.add_argument("--output_file", type=str)
    args = parser.parse_args()

    main(args)