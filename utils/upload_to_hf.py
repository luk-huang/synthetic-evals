import json
import os
from pathlib import Path

import pandas as pd
from datasets import Dataset
from huggingface_hub import HfApi

# ---------- paths ----------
INPUT_FILE  = Path("logs/synthetic_dataset.json")
FLAT_JSONL  = Path("logs/synthetic_dataset_flat.jsonl")
FLAT_CSV    = Path("logs/synthetic_dataset_flat.csv")

# ---------- flatten ----------
with INPUT_FILE.open() as f:
    data = json.load(f)

flattened = []
for row in data:
    q  = row.get("question")
    ideal  = row.get("ideal_answer")
    agent  = row.get("agent_answer")
    rubric = row.get("rubric")
    graded = row.get("graded_rubric", {})

    criteria = graded.get("graded_criteria", [])
    scores   = [c.get("score", 0) for c in criteria if isinstance(c, dict)]
    pct      = round(sum(scores) / (len(scores) * 4) * 100, 1) if scores else 0.0

    flattened.append({
        "question": q,
        "score_percent": pct,
        "ideal_answer": ideal,
        "agent_answer": agent,
        "rubric": rubric,              # keep original structure
        "graded_rubric": criteria      # list of dicts
    })

# ---------- save locally ----------
with FLAT_JSONL.open("w") as f:
    for item in flattened:
        f.write(json.dumps(item) + "\n")

pd.DataFrame(flattened).to_csv(FLAT_CSV, index=False)

print(f"✅ Flattened {len(flattened)} rows → {FLAT_JSONL}")

# ---------- upload to Hugging Face Hub ----------
# 1. ensure you have `huggingface-cli login` already done (or set HF_TOKEN env var)
# 2. choose a dataset repo name, e.g. "agentic-synthetic-evals"
repo_id = "lukhuang/synthetic_evals"   # <-- change this

ds = Dataset.from_list(flattened)
ds.push_to_hub(repo_id, private=False)              # set private=True if needed

print(f"🚀 Uploaded dataset to https://huggingface.co/datasets/{repo_id}")
