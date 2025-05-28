# Customize Agentic Coding for any codebase

Comprehensive evaluation suite for coding agents tailored to a real word codebase (like `cal.com`).

## Tasks

* **A. Codebase Understanding:**
* Retrieve / Localize Correct functions.
* Understanding tasks based on Deepwiki concepts.
* **B. Codebase Maintenance / Weird edge cases:**
* Fixing configuration issues (e.g., Yarn cache, `package.json` corruption, dependency misplacement).
* Providing plausible solutions for wrong/unclear instructions.
* Adversarial attacks.
* **C. Codebase Improvement:**
* Recreating diffs from LLM-generated prompts (interpreting intent, localizing changes, generating code).


## Get Merged PRs

PYTHONPATH=$(pwd) python utils/get_merged_prs.py --owner calcom --repo cal.com --pages 10


## Create Questions and Answers based on merged PRs

PYTHONPATH=$(pwd) python QandA_evaluation/construct_q_and_a.py --repo_path cal.com/ --merged_prs_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/merged_prs.jsonl

PYTHONPATH=$(pwd) python QandA_evaluation/construct_q_and_a_parallel.py --repo_path cal.com/ --merged_prs_path /Users/lukehuang/Documents/projects/agentic-coding-evals/logs/calcom_cal.com_10pages_date2025-05-27T18:38:46.649304/merged_prs_clean.jsonl

## Construct Rubric From Generated Q and A's

PYTHONPATH=$(pwd) python QandA_evaluation/construct_rubric.py --question_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/sampled_questions_from_prs.jsonl --answer_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/sampled_answers_from_prs.jsonl

## Getting Q and A agent to respond

PYTHONPATH=$(pwd) python QandA_agent.py --question_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/sampled_questions_from_prs.jsonl --output_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/q_and_a_agent_answers.jsonl --pr_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/merged_prs.jsonl


