import json
from pathlib import Path
import matplotlib.pyplot as plt

def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f]

def save_jsonl(path, rows):
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

def main(args):
    # --- paths
    pr_path = args.pr_path
    qna_path = args.qna_path
    rubric_path = args.rubric_path
    graded_rubric_path = args.graded_rubric_path
    output_path = args.output_path
    agent_answer_path = args.agent_answer_path

    # --- load files
    pr_data     = {pr["number"]: pr for pr in load_jsonl(pr_path)}
    qna_data    = {q["pr_number"]: q for q in load_jsonl(qna_path)}
    rubric_data = {r["pr_number"]: r for r in load_jsonl(rubric_path)}
    with open(agent_answer_path) as f:
        if "pr_number" in next(f):
            agent_answer_data = {a["pr_number"]: a for a in load_jsonl(agent_answer_path)}
            use_pr_number = True
        else:
            agent_answer_data = {a["question"]: a for a in load_jsonl(agent_answer_path)}
    graded_rubric_data = {r["pr_number"]: r for r in load_jsonl(graded_rubric_path)}

    # --- merge and filter
    merged = []
    print(21544 in pr_data.keys())
    for pr_number in pr_data.keys():
        qna   = qna_data.get(pr_number)
        rubric = rubric_data.get(pr_number)

        if not qna or not rubric:
            print(f"Skipping {pr_number} because it failed due to a question or rubric")
            continue

        if (
            "failed" in qna["question"].lower()
            or "failed" in json.dumps(rubric["rubric"]).lower()
        ):
            print(f"Skipping {pr_number} because it failed due to a question or rubric")
            continue

        if use_pr_number:
            if pr_number not in agent_answer_data:
                print(f"No agent answer for {pr_number}")
                continue

            agent_answer = agent_answer_data[pr_number]["answer"] if "answer" in agent_answer_data[pr_number] else agent_answer_data[pr_number]["response"]
        else:
            question = qna["question"]

            if question not in agent_answer_data:
                print(f"No agent answer for {pr_number}")
                continue

            agent_answer = (
                agent_answer_data[
                    qna["question"]
                ]["answer"] if "answer" in 
                agent_answer_data[qna["question"]] 
                else agent_answer_data[qna["question"]]["response"]
            )

        if pr_number not in graded_rubric_data:
            print(f"No graded rubric for {pr_number}")
            continue

        print(f"Graded rubric for {pr_number}: {graded_rubric_data[pr_number]}")

        merged.append({
            "pr_number": pr_number,
            "diff_url": pr_data[pr_number]["diff_url"],
            "question": qna["question"],
            "ideal_answer": qna["answer"],
            "ideal_sources": qna["sources"],
            "rubric": rubric["rubric"],
            "agent_answer": agent_answer,
            "score_percent": graded_rubric_data[pr_number]["score_percent"],
        })

    # --- save
    save_jsonl(output_path, merged)
    print(f"✅ Saved {len(merged)} clean entries to {output_path}")

def plot_score_distribution(final_graded_path):
    # Load the graded rubric data
    final_graded_path = Path(final_graded_path)
    pct_scores = []

    with open(final_graded_path) as f:
        for line in f:
            data = json.loads(line)
            pct_scores.append(data.get("score_percent"))

    # Calculate average score
    avg_score = sum(pct_scores) / len(pct_scores)

    # Plot histogram of scores
    plt.hist(pct_scores, bins=range(0, 101, 10), align='left', rwidth=0.8)
    plt.xlabel("Score")
    plt.ylabel("Count")
    plt.title("Histogram of Graded Rubric Scores")
    plt.xticks(range(0, 101, 10))
    plt.grid(axis='y')

    # Add vertical dotted line for average score
    plt.axvline(x=avg_score, color='red', linestyle=':', label=f'Average: {avg_score:.1f}')
    plt.legend()

    plt.savefig(f"{final_graded_path.parent}/score_distribution.png")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr_path", type=Path, required=True)
    parser.add_argument("--qna_path", type=Path, required=True)
    parser.add_argument("--rubric_path", type=Path, required=True)
    parser.add_argument("--agent_answer_path", type=Path, required=True)
    parser.add_argument("--graded_rubric_path", type=Path, required=True)
    parser.add_argument("--output_path", type=Path, required=True)
    args = parser.parse_args()
    main(args)

    plot_score_distribution(args.output_path)


'''
PYTHONPATH=$(pwd) python utils/format_results.py \
    --pr_path logs/calcom_cal.com_10pages_2025-05-27/merged_prs.jsonl \
    --qna_path logs/calcom_cal.com_10pages_2025-05-27/qna.jsonl \
    --rubric_path logs/calcom_cal.com_10pages_2025-05-27/rubrics.jsonl \
    --agent_answer_path logs/calcom_cal.com_10pages_2025-05-27/claude_code_answers.jsonl \
    --graded_rubric_path logs/calcom_cal.com_10pages_2025-05-27/claude_graded_rubrics.jsonl \
    --output_path logs/calcom_cal.com_10pages_2025-05-27/claude_synthetic_dataset.jsonl


PYTHONPATH=$(pwd) python utils/format_results.py \
    --pr_path logs/calcom_cal.com_10pages_2025-05-27/merged_prs.jsonl \
    --qna_path logs/calcom_cal.com_10pages_2025-05-27/qna.jsonl \
    --rubric_path logs/calcom_cal.com_10pages_2025-05-27/rubrics.jsonl \
    --agent_answer_path logs/calcom_cal.com_10pages_2025-05-27/dyi_agent/dyi_agent_answers.jsonl \
    --graded_rubric_path logs/calcom_cal.com_10pages_2025-05-27/dyi_agent/dyi_agent_graded_rubrics.jsonl \
    --output_path logs/calcom_cal.com_10pages_2025-05-27/dyi_agent/dyi_agent_synthetic_dataset.jsonl

'''
