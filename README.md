# Customize Agentic QnA for any Codebase

This benchmark evaluates coding agents and their familiarity with specific real-world repositories like `cal.com`, `dify`, and `ladybird` by simulating how well they could perform answer junior engineer level questions.

## Mine Real Merged PRs

We first mine real merged pull requests

```bash
python codebase_qna/construct/get_merged_prs.py \
    --owner YOUR_REPO_OWNER \
    --repo YOUR_REPO_NAME \
    --output logs/merged_prs.jsonl \
    --pages 4
```

This command produces:

* `logs/merged_prs.jsonl`: Raw metadata for the latest merged PRs
* `logs/merged_prs_formatted.prs`: A text-formatted summary suitable for LLM input

These summaries are used downstream to generate questions, answers, and rubrics grounded in real development history.

## 🔍 Synthetic Benchmark Creation Flow

To evaluate coding agents on real-world tasks, each PR is turned into a synthetic Q\&A instance with three components:

### 1. **Question**

We ask the LLM to simulate what a junior developer might ask if told, “a change like this was made,” but given no code. The question probes their ability to:

* Recreate the change from scratch,
* Identify key files, edge cases, and side effects,
* Reason about tradeoffs and system behavior.

### 2. **Ideal Answer + Rubric**

To evaluate responses, we invert the task: we ask the LLM to review the final diff and writes the implementation plan they *would have proposed* beforehand.

This ideal answer:

* Identifies the correct files, logic, and precision details,
* Accounts for edge cases, architecture, and testing,
* Serves as the ground truth for evaluation.

A rubric is then auto-generated from the ideal answer and diff. It defines specific criteria (e.g. file identification, implementation soundness, test strategy), each scored from 0–4 with strict penalties for hallucinations or irrelevance.

> This structure ensures agents are tested on their ability to reason, plan, and align with the real behavior of the codebase.


To simulate realistic developer workflows, each stage—**question generation**, **answer generation**, and **rubric construction**—is powered by a tool-augmented agent operating on a snapshot of the codebase.

### 🛠 Tools & Context Available to the Agent

For every merged PR, the agent is granted access to the original historical context via a **temporary git worktree**:

* A fresh local checkout is created at the PR’s `base_commit`.
* All tool calls (file reads, diffs, directory listings) operate strictly within this isolated worktree.
* This guarantees that the agent’s reasoning is grounded in the *exact* state of the codebase as it existed when the change was made.
* Once processing completes, the worktree is deleted to free resources.

These tools are dynamically invoked by the agent during reasoning, enabling inspection of real project structure.

* **`list_files()`** – Lists the file hierarchy (up to depth 3) of the codebase at the PR’s base commit.
* **`read_file(path)`** – Reads the full contents of a file in the local checkout.
* **`read_diff()`** – Returns the full diff of the PR (used only in answer and rubric stages, not during question generation).

> This same infrastructure powers all three stages:
>
> * **Question generation** (simulating a junior engineer with no access to the diff),
> * **Answer generation** (inferring the ideal implementation from the diff),
> * **Rubric generation** (evaluating how well the answer reflects the true implementation).

### Run 

To generate questions, answers, and rubrics:

```bash
python codebase_qna/async_executors/async_dataset_pipeline.py \
  --repo_path YOUR_REPO_NAME \
  --merged_prs_path logs/merged_prs_formatted.jsonl \
  --output_dir logs/synthetic_dataset/ \
  --model claude-3-7-sonnet-20250219 \
  --num_to_run N --max_concurrency 10
```

> ⚠️ Ensure you have git clone `YOUR_REPO_NAME` locally. A temporary worktree is created per question based on this.


### Examples

Here is an example Question (from `ladybird/ladybird`), generated from a real merged PR ([#4446](https://github.com/LadybirdBrowser/ladybird/pull/4446)):

```json
{
  "pr_number": 4446,
  "commit_hash": "98aad2da6e3242a0ca3ba67158e459c91299aada",
  "question": "If I need to add support for a new color primaries standard to our media conversion pipeline, like BT601 was added, what considerations should I keep in mind about the illuminant values, and how would I verify the conversion matrices are accurate without breaking existing video playback? Should I be concerned about the constant values' precision?"
}
```

Example 'ideal' Answer (Inferred from PR #4446 on Ladybird Codebase)

<details>
<summary>Click to view ideal implementation plan</summary>

```markdown
# Implementation Plan for Adding a New Color Primaries Standard\n\n## Goal\nAdd support for a new color primaries standard to our media conversion pipeline while ensuring accurate color reproduction and maintaining compatibility with existing video playback.\n\n## Key Files & Rationale\n- `Libraries/LibMedia/Color/ColorPrimaries.cpp`: This is the main file that implements color conversion matrices and contains the standard-specific color primary values.\n- `Libraries/LibMedia/Color/ColorPrimaries.h`: The header defining the public interface for the color primaries conversion system.\n\n## Step-by-Step Plan\n\n1. **Research the new standard's specifications**:\n   - Find the official documentation for the new color primaries standard\n   - Identify the exact chromaticity coordinates for red, green, and blue primaries\n   - Determine the white point (illuminant) for the standard\n\n2. **Add the primary coordinates**:\n   ```cpp\n   constexpr FloatVector2 NEW_STANDARD_RED = { x, y };\n   constexpr FloatVector2 NEW_STANDARD_GREEN = { x, y };\n   constexpr FloatVector2 NEW_STANDARD_BLUE = { x, y };\n   ```\n   - Use sufficient decimal precision (at least 3 decimal places as seen in BT.601 implementation)\n\n3. **Generate the RGB to XYZ conversion matrix**:\n   ```cpp\n   constexpr FloatMatrix3x3 new_standard_rgb_to_xyz = generate_rgb_to_xyz_matrix(\n       NEW_STANDARD_RED, NEW_STANDARD_GREEN, NEW_STANDARD_BLUE, ILLUMINANT_D65);\n   ```\n   - Note that we're using the D65 illuminant, which is the same one used for BT.601, BT.709, and BT.2020\n\n4. **Update the conversion matrix getter function**:\n   - Add handling for the new standard in both input and output cases:\n   ```cpp\n   case ColorPrimaries::NEW_STANDARD:\n       input_conversion_matrix = new_standard_rgb_to_xyz;\n       break;\n   ```\n   - And for the output case:\n   ```cpp\n   case ColorPrimaries::NEW_STANDARD:\n       output_conversion_matrix = new_standard_rgb_to_xyz.inverse();\n       break;\n   ```\n\n5. **Ensure enum value exists**: Make sure the ColorPrimaries enum in CodingIndependentCodePoints.h includes your new standard.\n\n## Testing Strategy\n\n1. **Verify conversion matrices**:\n   - Use a reference implementation or trusted tool to generate the same conversion matrices and compare results\n   - Check that the matrix values have sufficient precision to avoid rounding errors\n\n2. **Test with real media**:\n   - Find sample videos that use the new color primaries standard\n   - Verify that they display correctly with the new implementation\n   - Compare with other media players to ensure colors match\n\n3. **Regression testing**:\n   - Ensure existing videos using BT.601, BT.709, and BT.2020 still play correctly\n   - Test on different platforms to ensure consistent results\n\n4. **Visual verification**:\n   - Create test patterns that highlight color accuracy\n   - Verify the colors visually match reference images\n\n## Key Consideration\nThe most important consideration is maintaining the same illuminant (D65) across all standards for consistency in conversion. The BT.601 implementation shows that we should use high precision floating-point values (3+ decimal places) for the chromaticity coordinates to ensure accurate color reproduction. Using the same illuminant avoids potential color shifts when converting between different primaries standards, which could break existing video playback that depends on consistent color transformation.
```

**Referenced files:**
- `Libraries/LibMedia/Color/ColorPrimaries.cpp`  
- `Libraries/LibMedia/Color/ColorPrimaries.h`

</details>

Example Rubric (for PR #4446 – Adding New Color Primaries Support)

<details> 
<summary>Click to view rubric</summary>

```json
{
  "title": "Rubric for Adding New Color Primaries Standard to Media Conversion Pipeline", 
  "criteria": [
    {
      "name": "Identification of Key Files and Components", 
      "description": "To add support for a new color primaries standard, the answer must correctly identify the relevant files and components that need modification.", 
      "levels": [
        "0: No mention of specific files or components", 
        "1: Vague mention of 'color conversion files' without specifics", 
        "2: Mentions either ColorPrimaries.cpp or ColorPrimaries.h but misses the other", 
        "3: Correctly identifies both ColorPrimaries.cpp and ColorPrimaries.h but lacks detail on their roles", 
        "4: Accurately identifies ColorPrimaries.cpp and ColorPrimaries.h with clear explanation of their roles in the conversion pipeline because they contain the implementation and interface for color primaries conversion"
        ]
    }, 
    {
      "name": "Understanding of Color Primary Coordinates Implementation", 
      "description": "To add a new color primaries standard, the answer must demonstrate understanding of how color primary coordinates are defined and implemented.", 
      "levels": [
        "0: No mention of color primary coordinates", 
        "1: Vague mention of 'defining color values' without specifics", 
        "2: Mentions adding color values but doesn't specify the format or structure", 
        "3: Identifies the need for red, green, and blue primary coordinates but lacks precision details",
        "4: Correctly specifies the exact implementation pattern using constexpr FloatVector2 for each primary (RED, GREEN, BLUE) because this matches the existing pattern in ColorPrimaries.cpp"]
    }, 
    {
      "name": "Illuminant Handling in generate_rgb_to_xyz_matrix Function", 
      "description": "To add support for a new color primaries standard, the answer must correctly address how illuminant values are used in the conversion matrix generation.", 
      "levels": [
        "0: No mention of illuminants or white points", 
        "1: Mentions illuminants but doesn't explain their relevance", 
        "2: Identifies that illuminants matter but doesn't specify which one to use", 
        "3: Specifies using D65 illuminant but doesn't explain why this choice is important", 
        "4: Explicitly states using ILLUMINANT_D65 in generate_rgb_to_xyz_matrix function and explains the importance of maintaining the same illuminant across standards because it ensures consistent conversion and prevents color shifts"
        ]
    },
    {
      "name": "Handling of RGB to XYZ Conversion Matrix", 
      "description": "To add a new color primaries standard, the answer must correctly explain how to generate and implement the RGB to XYZ conversion matrix.", 
      "levels": [
        "0: No mention of conversion matrices", 
        "1: Vague mention of 'conversion' without specifics about matrices", 
        "2: Identifies need for conversion matrices but doesn't explain how to generate them", 
        "3: Explains generating conversion matrices but misses implementation details like using .inverse() for output case", 
        "4: Correctly details both matrix generation and implementation in the conversion function, including use of inverse matrices for output conversion because this follows the existing pattern in ColorPrimaries.cpp"
        ]
    }, 
    {
      "name": "Numerical Precision Considerations", 
      "description": "To add a new color primaries standard, the answer must address considerations about numerical precision in constant values.", 
      "levels": [
        "0: No mention of precision considerations", 
        "1: Vague mention of 'accurate values' without specifics about precision", 
        "2: Mentions precision but doesn't specify required level or why it matters", 
        "3: Specifies using high precision values but doesn't link to existing implementation or explain consequences", 
        "4: Explicitly states using at least 3 decimal places based on existing BT.601 implementation and explains why precision matters for preventing rounding errors and ensuring accurate color reproduction because small errors can accumulate in matrix operations"
        ]
    }
  ]
}
```

</details>


## Evaluation Flow with Multi-Turn Interaction 

Here’s a concise and clean version of that section for your README:

---

### Single-Turn Evaluation

We evaluated Claude Code on 150 synthetic questions across 4 real-world codebases using 3 Claude models:

* Claude 3.7 Sonnet
* Claude 3.5 Sonnet
* Claude 3.5 Haiku

#### Generate Agent Answers

```bash
python codebase_qna/async_executors/async_claude_pipeline.py \
  --repo_path YOUR_REPO_NAME \
  --questions_file logs/synthetic_dataset/qna.jsonl \
  --output_file logs/agent/claude3.7_answers.jsonl \
  --model MODEL_TO_TEST
```

where MODEL_TO_TEST is the name of the model answering the questions (e.g. claude-3-7-sonnet-20250219)

#### Grade Answers

```bash
PYTHONPATH=$(pwd) python codebase_qna/async_executors/async_grader.py \
  --repo_path YOUR_REPO_NAME \
  --formatted_prs_path logs/merged_prs_formatted.jsonl \
  --rubric_path logs/synthetic_dataset/rubrics.jsonl \
  --answer_path logs/agent/claude3.7_answers.jsonl \
  --output_path logs/agent/claude3.7_graded_rubrics.jsonl \
  --model claude-3-7-sonnet-20250219 \
  --max_parallel 10
```

### 🔄 Multi-Turn Evaluation

We test whether agents can revise their answers based on feedback:

* **Round 1**: Agent submits an initial plan.
* **Grading**: A rubric scores the response, and **Socratic feedback** raises one targeted concern.
* **Round 2**: The agent revises its plan using only that feedback.
* *(Repeat for more rounds as needed.)*

Each round includes **one piece of Socratic feedback**: a natural, open-ended doubt about a specific choice in the answer. It does **not** reveal the correct solution.

> *"You're focusing on `PaymentService`—can you say why that fits the credit logic better than a dedicated credit module?"*

#### Generate Multi-Turn Answers Based on Feedback

```bash
python codebase_qna/async_executors/async_claude_pipeline_multiturn.py \
    --repo_path cal.com/ \
    --feedback_file PATH_TO_GRADED_RUBRICS \
    --questions_file logs/synthetic_dataset/qna.jsonl \
    --output_file PATH_TO_NEW_ANSWERS \
    --model MODEL_TO_TEST \
    --max_concurrency 10
```