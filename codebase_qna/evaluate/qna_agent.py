from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain.agents import create_tool_calling_agent, AgentExecutor
from typing import List
from utils.agent_tools import read_file_tool, list_files_tool
from utils.codebase_utils import get_file_hierarchy
from langchain_core.exceptions import OutputParserException
import json
from utils.codebase_utils import WorktreeManager
load_dotenv()

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

class QandAResponse(BaseModel):
    answer: str = Field(description="The answer to the question ")
    sources: List[str] = Field(description="The sources used to answer the question")

parser = PydanticOutputParser(pydantic_object=QandAResponse)

ANSWER_SYSTEM_PROMPT = """
You are a highly experienced senior software engineer mentoring a junior developer.

When answering a question:
- Think through the problem step-by-step before answering.
- Provide a detailed high-level implementation plan.
- Suggest relevant pseudocode where appropriate.
- Explain potential edge cases and how to handle them.
- Describe how you would test the implementation, including both unit and integration tests.
- Cite relevant files or modules if applicable.

You should be thorough and clear, and avoid stopping short. Even if unsure about a tool or part of the codebase, do your best to reason out what a good approach might be.
"""


prompt = ChatPromptTemplate.from_messages([
    ("system", ANSWER_SYSTEM_PROMPT),
    ("assistant", "\n{format_instructions}"),
    ("placeholder", "{chat_history}"),
    ("user", "Relevant Codebase Files: {codebase_hierarchy}"),
    ("user", "Question: {query}"),
    ("placeholder", "{agent_scratchpad}")
]).partial(format_instructions=parser.get_format_instructions())


if __name__ == "__main__":
    import argparse
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--question_path", type=str, required=True)
    arg_parser.add_argument("--pr_path", type=str, required=True)
    arg_parser.add_argument("--output_path", type=str, required=True)
    args = arg_parser.parse_args()

    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=None,
        stop=None,
        model_name='claude-3-5-sonnet-20240620',
    )

    tools = [read_file_tool, list_files_tool]

    QandA_Agent = create_tool_calling_agent(
        llm = llm, 
        prompt = prompt, 
        tools = tools
    )
    agent_executor = AgentExecutor(agent=QandA_Agent, tools=tools, verbose=True)

    generated_answers = {}

    worktree_manager = WorktreeManager(os.getenv("CAL_COM_REPO_PATH"))


    with open(args.question_path, 'r') as q_file, open(args.pr_path, 'r') as p_file:
        questions = (json.loads(line.strip()) for line in q_file)
        prs = (json.loads(line.strip()) for line in p_file)
        
        for question, pr in zip(questions, prs):
            worktree_manager.create(pr["base_commit"])

            codebase_hierarchy = get_file_hierarchy(worktree_manager.repo_path)

            raw_response = agent_executor.invoke({
                "query": question["question"],
                "codebase_hierarchy": codebase_hierarchy
            })

            try:
                stuctured_response = parser.parse(raw_response['output'][0]["text"])
                generated_answers[question["question"]] = stuctured_response.model_dump()
            except OutputParserException as e:
                stuctured_response = raw_response['output'][0]["text"]
                generated_answers[question["question"]] = stuctured_response

            print(stuctured_response)

            

            worktree_manager.down(pr["base_commit"])
            break
        
    with open(args.output_path, 'w') as f:
        f.write(json.dumps(generated_answers) + "\n")