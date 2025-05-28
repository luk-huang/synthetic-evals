

# # Wait for the running Q&A script to finish
# while pgrep -f parallel_executors/parallel_q_and_a_construct.py > /dev/null; do
#     echo "[Waiting] Q&A agent still running..."
#     sleep 10  # check every 10 seconds
# done

# # Once it's done, proceed with next steps
# echo "[Done] Q&A agent finished. Running rubric generation..."

# sleep 100

# # Generate Rubrics
# PYTHONPATH='/Users/lukehuang/Documents/projects/agentic-coding-evals/' python parallel_executors/parallel_rubric_runner.py \
#         --question_path logs/out/sampled_questions.jsonl \
#         --answer_path   logs/out/sampled_answers.jsonl

# Generate Agent Answers
PYTHONPATH='/Users/lukehuang/Documents/projects/agentic-coding-evals/' python parallel_executors/parallel_query_q_and_a_agent.py \
        --question_path logs/out/sampled_questions.jsonl \
        --pr_path       logs/calcom_cal.com_10pages_date2025-05-27T18:38:46.649304/merged_prs_clean.jsonl \
        --output_path   logs/out/sampled_agent_answers.jsonl \
        --repo_root     cal.com/

# Grade Agent Answers
PYTHONPATH='/Users/lukehuang/Documents/projects/agentic-coding-evals/' python parallel_executors/parallel_grader.py \
        --rubric_path   logs/rubrics_parallel.jsonl \
        --question_path logs/out/sampled_questions.jsonl \
        --answer_path   logs/out/sampled_agent_answers_completed.jsonl  \
        --output_path   logs/graded_agent_answers.jsonl