from pathlib import Path
import json

# Input file paths
questions_path       = Path("logs/out/sampled_questions.jsonl")
ideal_answers_path   = Path("logs/out/sampled_answers.jsonl")
agent_answers_path   = Path("logs/out/sampled_agent_answers.jsonl")
rubrics_path         = Path("logs/rubrics_parallel.jsonl")
graded_path          = Path("logs/graded_agent_answers.jsonl")

# Output path
output_path          = Path("logs/synthetic_dataset.json")

def load_jsonl(path):
    with path.open("r") as f:
        return [json.loads(line) for line in f]

def main():
    questions      = load_jsonl(questions_path)
    ideal_answers  = load_jsonl(ideal_answers_path)
    agent_answers  = load_jsonl(agent_answers_path)
    rubrics        = load_jsonl(rubrics_path)
    graded         = load_jsonl(graded_path)

    # Index by question string
    agent_by_q     = {entry["question"]: entry["answer"] for entry in agent_answers}
    rubric_by_q    = {entry["question"]: entry["rubric"] for entry in rubrics}
    graded_by_q    = {entry["question"]: entry for entry in graded}

    synthetic_dataset = []

    for q_entry, ideal_entry in zip(questions, ideal_answers):
        q = q_entry["question"]

        if q not in agent_by_q or q not in rubric_by_q or q not in graded_by_q:
            print(f"❌ Skipping {q} because it's not in agent_by_q, rubric_by_q, or graded_by_q")
            continue  # skip incomplete

        synthetic_dataset.append({
            "question": q,
            "ideal_answer": ideal_entry["answer"],  # now aligned by line index
            "agent_answer": agent_by_q[q],
            "rubric": rubric_by_q[q],
            "graded_rubric": graded_by_q[q].get("graded_rubric"),
            "score_percent": graded_by_q[q].get("score_percent")
        })

    with output_path.open("w") as f:
        json.dump(synthetic_dataset, f, indent=2)

    print(f"✅ Saved synthetic dataset with {len(synthetic_dataset)} entries to {output_path}")

if __name__ == "__main__":
    main()
