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

    import pandas as pd
    import matplotlib.pyplot as plt
    from pathlib import Path

    # Load the flattened CSV created earlier
    csv_path = Path("logs/synthetic_dataset_flat.csv")
    df = pd.read_csv(csv_path)

    # Ensure we have the score column
    if "score_percent" not in df.columns:
        raise ValueError("score_percent column not found in the dataframe!")

    # Create histogram bins (0–10, 10–20, ..., 90–100)
    bins = [i for i in range(0, 110, 10)]
    labels = [f"{bins[i]}-{bins[i+1]-1}" for i in range(len(bins) - 1)]

    # Cut scores into bins
    df["score_bin"] = pd.cut(df["score_percent"], bins=bins, labels=labels, right=False)

    # Count entries per bin
    score_counts = df["score_bin"].value_counts().sort_index()

    # Plot
    plt.figure(figsize=(10, 4))
    score_counts.plot(kind="bar")
    plt.title("Distribution of Score Percentages")
    plt.xlabel("Score Range (%)")
    plt.ylabel("Number of Questions")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig("logs/synthetic_dataset_agent_score_histogram.png")


