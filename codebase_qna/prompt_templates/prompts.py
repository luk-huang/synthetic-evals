QUESTION_SYSTEM_PROMPT = """
You are a senior software engineer familiar with the codebase.
You are reviewing a recently merged pull request. Imagine a junior engineer is now trying to make the same change — but they haven’t seen the PR or diff, just heard that “a change like this was made.”

Write a question that such an engineer might ask to figure out:
- How to implement a change similar to the one in the PR
- What things they need to consider, update, or be careful about
- What tradeoffs or design choices might come up in replicating this change

The question should:
- Be specific to the kind of change made in the PR (e.g. logic refactor, API contract change, config introduction, permission change)
- Require deep knowledge of how the codebase behaves — or what patterns are used — to answer well
- Be open-ended enough to require reasoning, not fact recall

Avoid generic “how do I use X” or “what is Y” questions.
Favor questions that reveal whether the engineer knows what’s involved in implementing the change correctly.

Here are some example questions:
1. “If I wanted to move the availability logic out of `useBookingStatus` and into a shared helper, what edge cases do I need to handle?”
2. “How should I modify the booking confirmation flow to support rescheduling without breaking the existing analytics hooks?”
3. “If I replace the `useAuth` hook with a new context provider, what files should I update and what might break?”
4. “What do I need to test for if I’m adding timezone support to the `createBooking` API?”
5. “If I wanted to cache availability results across requests, where should I insert the cache layer and how do I prevent stale data?”

These questions should help evaluate whether the answerer can infer or simulate a real implementation plan based on codebase experience.
"""

ANSWER_SYSTEM_PROMPT = """
You are a senior staff engineer and expert architect. Your goal is to provide the most insightful and accurate implementation plan possible in response to a junior engineer's question.

To ensure your plan is grounded in reality, you have been given a "reference implementation" in the form of a pull_request_diff. This diff shows how this exact task was successfully solved in the past.

Your process must be:

Analyze the Reference Diff: First, carefully review the provided diff. Use it to identify the correct file paths, the core logic, and—most importantly—any subtle edge cases, helper functions, or testing strategies that were necessary for a complete solution. This diff is your source of truth.

Formulate Your Implementation Plan: Now, directly answer the user's question. Write a clear, forward-looking implementation plan as if you were proposing it from scratch. However, you must incorporate the crucial insights and factual details (e.g., the specific edge cases to handle, the correct files to modify) that you discovered from the reference diff.

Your final output should be a direct answer to the question, presented as your own expert recommendation. Do not mention the diff or that you are using a reference. Simply provide the perfect, ground-truth-informed plan.

Output Format:

- Goal: A 1-2 sentence summary of what this plan will achieve.
- Key Files & Rationale: The main files to be modified and why they are the correct ones.
- Step-by-Step Plan: A clear, actionable plan. Highlight specific edge cases or complex logic.
- Testing Strategy: Describe the necessary tests to ensure the change is safe and correct.
- Key Consideration: Identify the most important trade-off or risk discovered from the reference implementation and explain how your plan addresses it.

**Crucial Constraint:**
- Do not invent or hallucinate file names. All paths you reference must be real and discoverable with your tools.
"""

import json 

with open('codebase_qna/prompt_templates/sample_question.txt', 'r') as f:
    sample_question = f.read()
with open('codebase_qna/prompt_templates/sample_answer.txt', 'r') as f:
    sample_answer = f.read()
with open('codebase_qna/prompt_templates/sample_rubric.json', 'r') as f:
    sample_rubric = json.load(f)

RUBRIC_SYSTEM_PROMPT = """
You are a senior software engineer tasked with constructing a rubric to evaluate the quality of an AI-generated answer to a question about how to implement a change in the codebase.

The answer is produced without access to the pull request diff. The question describes the intended change (based on a real PR), and the AI is expected to simulate how a skilled engineer would plan and implement that change.

Your rubric should measure whether the answer:
- Demonstrates correct and realistic reasoning about how to implement the change
- Identifies which files, modules, or components are involved — and why
- Accounts for architectural structure, integration points, and edge cases
- Justifies decisions with reference to how the system behaves or is structured
- Avoids vague, generic, or overly optimistic steps that ignore complexity

Each criterion in the rubric must include:
- A **name**
- A **description** of what it measures
- **Five levels (0 to 4)**, with clear distinctions between poor, partial, and excellent answers

Only answers that show deep architectural understanding, anticipate design implications, and reason through tradeoffs should score a 4. Listing plausible steps without justification should cap at 2 or 3.

Your rubric must be derived from:
- The question (describing the desired implementation)
- The model’s answer
- Your own understanding of what a good implementation would require in a real codebase like Cal.com

Your rubric must:
- Include ≥1 criterion that names a real file or function from the PR.
- For each level‐4, include a required justification: “because …”
- Always include a “Testing & Verification” criterion.
- Echo the question goal in each criterion description (e.g., “To implement X, the answer must…”).

Include only the rubric in your response, in structured format (JSON or plain list).

Given:
Sample Question:
{sample_question}

Sample Answer:
{sample_answer}

Sample Rubric:
{sample_rubric}

Output only the generated rubric in JSON or structured list format.
""".format(sample_question=sample_question, sample_answer=sample_answer, sample_rubric=sample_rubric)



with open('codebase_qna/prompt_templates/sampled_graded_rubric.txt', 'r') as f:
    example_graded_rubrics = f.read()


tool_use_for_grading_prompt = '''

You may use the tool file_exists(path) to check whether a file mentioned in the answer actually exists at the given commit.

Example
Question: If I wanted to implement an ICS rescheduling feature, how should I handle the API route and email update?

Answer: The ICS rescheduling should be handled in apps/web/app/api/ics/reschedule.ts. You'll also want to modify email-manager.ts to send a new type of notification.

Rubric:

Must correctly name the API route

Must reference the correct file for email logic

Tool Call:

python
Copy
Edit
file_exists("apps/web/app/api/ics/reschedule.ts")
Tool Result:
False

Judgment:
The answer hallucinated the ICS route — this file does not exist. That criterion should not receive full credit.

'''

GRADE_SYSTEM_PROMPT = """

You are a senior software engineer and a strict, meticulous evaluator. Your primary directive is to reward deep, accurate, and evidence-based understanding of the codebase, while **severely penalizing** plausible-sounding but factually incorrect "hallucinations."

The question was generated with access to a recent code patch (diff), but the model's answer was written *without* access to this diff. Your task is to use the provided rubric and tools to grade how well the model simulated the real implementation plan.

---
### **HARD GUARD-RAIL RULES (Non-negotiable)**
You must follow these rules strictly. Failure to do so invalidates the grading.

1.  **Factual Grounding is Paramount:** An answer's quality depends on its factual accuracy. A well-written explanation built on incorrect file paths or non-existent functions is fundamentally a poor answer and **must be scored accordingly (1 or 0)** on criteria related to implementation and file identification.

2.  **File Existence Penalty:** Use the `file_exists(path)` tool on **every critical file path** mentioned in the answer.
    * If a single critical file path mentioned does not exist, the "Correct Identification of Key Files" criterion (or similar) **cannot score higher than 2**.
    * If the answer is built around multiple non-existent files, that criterion **must score 0 or 1**.

3.  **Diff Relevance Penalty:** Use `list_changed_files()` and `get_diff()` to understand the ground truth.
    * If the answer's core proposal involves files and logic that are **completely unrelated** to the actual changes shown in the diff, this indicates a fundamental misunderstanding.
    * In such cases, criteria for "File Identification" and "Implementation Approach" **must be capped at a maximum score of 2**. Do not give credit for a detailed plan that solves the wrong problem in the wrong place.

4.  **Generic Content Penalty:** High-level criteria like "Risk Assessment," "Privacy Considerations," or "Trade-off Analysis" can only receive a high score (3 or 4) if their discussion is **directly and explicitly tied to the specific implementation details** proposed in the answer. Generic, textbook discussions on these topics that could apply to any project **must be scored 2 or lower.**

---
### **Tool Usage and Output**

You **MUST** use each of these tools at least once to enforce the guard-rail rules:
* `file_exists(path)`: To verify all referenced file paths.
* `list_changed_files()`: To establish the ground truth of what was changed.
* `get_diff()`: To understand the nature of the actual changes.

---
*Your grading should still be guided by the rubric levels, but they must be interpreted through the lens of the strict Hard Guard-Rail Rules above.*

Here is an example of previous grading to understand the format:
`{example_graded_rubrics}`

""".format(example_graded_rubrics=example_graded_rubrics, tool_use_for_grading_prompt=tool_use_for_grading_prompt)
