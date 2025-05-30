from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field
from typing import List
import json
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor
from dotenv import load_dotenv
import os
from langchain.agents import create_tool_calling_agent
import argparse
from langchain_core.exceptions import OutputParserException
from codebase_qna.prompt_templates.prompts import RUBRIC_SYSTEM_PROMPT

class Criterion(BaseModel):
    name: str = Field(..., description="The name of the evaluation criterion.")
    description: str = Field(..., description="A detailed description of the criterion.")
    levels: List[str] = Field(..., description="Performance levels for this criterion.")

class Rubric(BaseModel):
    title: str = Field(..., description="The title of the rubric.")
    criteria: List[Criterion] = Field(..., description="A list of evaluation criteria.")

rubric_parser = PydanticOutputParser(pydantic_object=Rubric)

rubric_prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content=RUBRIC_SYSTEM_PROMPT),
    ("assistant", "{format_instructions}"),
    ("placeholder", "{chat_history}"),
    ("placeholder", "{agent_scratchpad}"),
    ("user", "Question:{query}"),
    ("user", "Answer: {answer}"),
    ("user", "Sources: {sources}"),
    ("assistant", "{format_instructions}"),
    ("assistant", "Here is your rubric in the desired format: {{")
]).partial(format_instructions=rubric_parser.get_format_instructions())

def main(args):

    load_dotenv()

    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=None,
        stop=None,
        model_name='claude-3-5-sonnet-20240620',
    )

    if args.output_path is None:
        output_path = f"logs/{args.qna_path.split('/')[-2]}/rubrics.jsonl"
    else:
        output_path = args.output_path

    tools = []
    agent = create_tool_calling_agent(llm, tools=tools, prompt=rubric_prompt)

    rubric_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )

    from utils.json_repair import JSONRepairAgent
    json_repair_agent = JSONRepairAgent(model_name='gpt-4o-mini')

    # Open all files simultaneously
    with open(args.qna_path, 'r') as qna_file:
        # Create iterators for each file
        qna_pairs = (json.loads(line.strip()) for line in qna_file)

        rubrics = {}
        for qna_pair in qna_pairs:
            question = qna_pair["question"]
            answer = qna_pair["answer"]
            sources = qna_pair["sources"]

            raw_response = rubric_agent.invoke({
                "query": question,
                "answer": answer,
                "sources": sources
            })
            raw_text = raw_response['output'][0]["text"]
            try:
                parsed_response = rubric_parser.parse("{" + raw_text)
            except OutputParserException as e:
                print(f"Error parsing rubric: {raw_text}")
                parsed_response = json_repair_agent.repair_json_output(raw_text, Rubric)

            print(f"Rubric: {parsed_response.model_dump()}")

            rubrics[question] = parsed_response.model_dump()

        with open(output_path, 'a') as f:
            f.write(json.dumps(rubrics) + "\n")  

    print(f"Rubric written to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--qna_path", type=str, required=True)
    parser.add_argument("--output_path")
    args = parser.parse_args()

    main(args)

    