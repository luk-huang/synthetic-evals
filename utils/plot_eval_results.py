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

