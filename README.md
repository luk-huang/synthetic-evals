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


PYTHONPATH=$(pwd) python codebase_qna/construct/get_merged_prs.py \
    --owner LadybirdBrowser \
    --repo ladybird \
    --pages 3 \
    --num_to_format 500

PYTHONPATH=$(pwd) python codebase_qna/construct/get_merged_prs.py \
    --owner langgenius \
    --repo dify \
    --pages 3 \
    --num_to_format 500

## Create Questions and Answers based on merged PRs

PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_dataset_pipeline.py \
    --repo_path cal.com \
    --merged_prs_path logs/calcom_cal.com_100pages_date2025-05-28/merged_prs_formatted.jsonl \
    --output_dir logs/calcom_cal.com_100pages_date2025-05-28/ \
    --num_to_run 200 --max_concurrency 10 --resume \
    --model claude-3-7-sonnet-20250219


PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_dataset_pipeline.py \
    --repo_path ladybird \
    --merged_prs_path logs/LadybirdBrowser_ladybird_3pages_date2025-05-30/merged_prs_formatted.jsonl \
    --output_dir logs/LadybirdBrowser_ladybird_3pages_date2025-05-30/ \
    --num_to_run 200 --max_concurrency 10 \
    --model claude-3-7-sonnet-20250219

## Construct Rubric From Generated Q and A's

PYTHONPATH=$(pwd) python QandA_evaluation/construct_rubric.py --question_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/sampled_questions_from_prs.jsonl --answer_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/sampled_answers_from_prs.jsonl

## Getting Q and A agent to respond

PYTHONPATH=$(pwd) python QandA_agent.py --question_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/sampled_questions_from_prs.jsonl --output_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/q_and_a_agent_answers.jsonl --pr_path logs/calcom_cal.com_10pages_date2025-05-27T14:32:03.535275/merged_prs.jsonl


