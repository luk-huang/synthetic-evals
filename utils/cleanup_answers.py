import json
from pathlib import Path

QUESTION_PATH = Path("logs/out/sampled_questions.jsonl")
ANSWER_PATH = Path("logs/out/sampled_agent_answers.jsonl")
OUTPUT_PATH = Path("logs/out/sampled_agent_answers_completed.jsonl")

# Load existing answers
with ANSWER_PATH.open() as f:
    existing_answers = [json.loads(line.strip()) for line in f]
    answered_questions = {item["question"] for item in existing_answers}

# Load questions
with QUESTION_PATH.open() as f:
    all_questions = [json.loads(line.strip()) for line in f]

# Add missing entries
completed_answers = list(existing_answers)  # start with current answers
for q in all_questions:
    if q["question"] not in answered_questions:
        completed_answers.append({
            "question": q["question"],
            "answer": "Failed to answer",
            "sources": []
        })

# Write to output file
with OUTPUT_PATH.open("w") as f:
    for entry in completed_answers:
        f.write(json.dumps(entry) + "\n")

print(f"✅ Completed answers written to {OUTPUT_PATH}")
