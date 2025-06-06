import argparse
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from typing import List
from langchain_core.tools import Tool
from pathlib import Path
import json
from langchain_core.exceptions import OutputParserException
import pandas as pd
from codebase_qna.prompt_templates.prompts import GRADE_SYSTEM_PROMPT
from langchain_core.messages import SystemMessage

class CriterionGrade(BaseModel):
    name: str = Field(..., description="Name of the rubric criterion being graded.")
    score: int = Field(..., ge=0, le=4, description="Score from 0 to 4 according to the rubric levels.")
    justification: str = Field(..., description="Justification for the assigned score.")

class GradedRubric(BaseModel):
    graded_criteria: List[CriterionGrade] = Field(..., description="List of graded rubric items.")
    feedback: str = Field(..., description="Feedback for the answer.")

grade_rubric_parser = PydanticOutputParser(pydantic_object=GradedRubric)

grade_rubric_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(GRADE_SYSTEM_PROMPT),
    ("assistant", "{format_instructions}"),
    ("placeholder", "{agent_scratchpad}"),
    ("user", "Rubric to apply: {rubric}"),
    ("user", "Question: {question}"),
    ("user", "Answer to grade: {answer}")
]).partial(
    format_instructions=grade_rubric_parser.get_format_instructions()
)

def pretty_print_graded_rubric(raw_response: GradedRubric):
    parsed = raw_response.model_dump()
    pretty = json.dumps(parsed, indent=2)
    print(pretty)

def test_grade_answer(args):
    from langchain_anthropic import ChatAnthropic
    from dotenv import load_dotenv
    import os
    from langchain.agents import create_tool_calling_agent
    from langchain.agents import AgentExecutor

    load_dotenv()

    # Load your Anthropic API key
    llm = ChatAnthropic(
        model_name="claude-3-5-sonnet-20240620",
        api_key=os.getenv("ANTHROPIC_API_KEY")
    )

    def display_rubric_locally(graded_rubric: GradedRubric):
        data = [
            {
                "Criterion": criterion.name,
                "Score": criterion.score,
                "Justification": criterion.justification
            }
            for criterion in graded_rubric.graded_criteria
        ]
        df = pd.DataFrame(data)
        print(df.to_markdown(index=False))
        return df
    
    filled_prompt = grade_rubric_prompt

    # Create agent and executor
    agent = create_tool_calling_agent(llm=llm, tools=[], prompt=filled_prompt)
    executor = AgentExecutor.from_agent_and_tools(agent=agent, tools=[], verbose=True)

    with open(args.question_path, 'r') as file_q, open(args.answer_path, 'r') as file_a, open(args.rubric_path, 'r') as file_r:
        questions = (json.loads(line.strip()) for line in file_q)
        answers = (json.loads(line.strip()) for line in file_a)
        rubrics = (json.loads(line.strip()) for line in file_r)

        for question, answer, rubric in zip(questions, answers, rubrics):
            response = executor.invoke({
                "rubric": json.dumps(rubric),
                "question": question,
                "answer": answer
            })

            try:
                response = grade_rubric_parser.parse(response['output'][0]["text"])

            except OutputParserException as e:
                from utils.json_repair import ClaudeJSONRepairAgent
                repair_agent = ClaudeJSONRepairAgent()
                response = repair_agent.repair_json_output(response['output'][0]["text"], GradedRubric)


            print(response.graded_criteria)

            display_rubric_locally(response)    

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rubric_path", type=str, required=True)
    parser.add_argument("--question_path", type=str, required=True)
    parser.add_argument("--answer_path", type=str, required=True)
    args = parser.parse_args()
    
    test_grade_answer(args)

    

    