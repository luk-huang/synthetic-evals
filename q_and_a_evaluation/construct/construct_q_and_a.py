from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing import List
from langchain_core.tools import tool, Tool
import json
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor
from langchain_core.exceptions import OutputParserException
from dotenv import load_dotenv
import os
from langchain.agents import create_tool_calling_agent
from utils.tools import create_list_files_tool, create_read_file_tool, create_read_diff_from_link_tool
from utils.codebase_utils import WorktreeManager


QUESTION_SYSTEM_PROMPT = """
    You are a senior software engineer that has a deep understanding of the codebase. 
    You are given a merged pull request from a GitHub repository, and think of a question a junior engineer would ask about in terms of how to implement the changes in the pull request.
    The question should be one or two sentences long. FOLLOW THE FORMAT INSTRUCTIONS 
"""

ANSWER_SYSTEM_PROMPT = """
You are a senior software engineer mentoring a junior engineer who has posed a question about implementing a specific feature or addressing a particular issue within the codebase.

Your role is to guide them through the problem-solving process by:

- Encouraging them to articulate the problem clearly and identify the desired outcome.
- Helping them break down the problem into manageable components.
- Prompting them to consider relevant parts of the codebase, design patterns, or architectural principles that may apply.
- Asking probing questions that lead them to uncover insights and develop their own solutions.
- Offering high-level guidance and best practices without providing direct code implementations.

Your objective is to foster the junior engineer's critical thinking and autonomy, enabling them to arrive at a well-reasoned solution through exploration and understanding.
"""


class Question(BaseModel):
    question: str = Field(..., description="The question that was asked.")

class Answer(BaseModel):
    text: str = Field(..., description="Ideally Nothing in here, but if you need to say something, say it here.")
    answer: str = Field(..., description="The answer to the question.")
    sources: List[str] = Field(..., description="The sources that were used to answer the question.")

question_parser = PydanticOutputParser(pydantic_object=Question)
answer_parser = PydanticOutputParser(pydantic_object=Answer)

question_prompt = ChatPromptTemplate.from_messages([
    ("system", QUESTION_SYSTEM_PROMPT),
    ("placeholder", "{chat_history}"),
    ("user", "Merged Pull Request: {merged_pull_request}"),
    ("user", "Codebase Files: {codebase_files}"),
    ("user", "Format Instructions: {format_instructions}"),
    ("placeholder", "{agent_scratchpad}")

]).partial(
    format_instructions=question_parser.get_format_instructions()
)

answer_prompt = ChatPromptTemplate.from_messages([
    ("system", ANSWER_SYSTEM_PROMPT),
    ("placeholder", "{chat_history}"),
    ("user", "Question: {question}"),
    ("user", "Merged Pull Request: {merged_pull_request}"),
    ("user", "Codebase Files: {codebase_files}"),
    ("user", "Format Instructions: {format_instructions}"),
    ("placeholder", "{agent_scratchpad}")
]).partial( 
    format_instructions=answer_parser.get_format_instructions()
)

def create_question_agent(llm, tools: List[Tool]) -> AgentExecutor:
    agent = create_tool_calling_agent(llm, tools=tools, prompt=question_prompt)

    question_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )
    return question_agent

def create_answer_agent(llm, tools: List[Tool]) -> AgentExecutor:

    agent = create_tool_calling_agent(llm, tools=tools, prompt=answer_prompt)

    answer_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )
    return answer_agent

def main(repo_path: str, merged_prs_path: str):
    load_dotenv()
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    
    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=None,
        stop=None,
        model_name='claude-3-5-sonnet-20240620',
    )

    worktree_manager = WorktreeManager(repo_path)

    questions = []
    answers_and_sources = []

    num_examples = 0
    max_examples = 50
    
    with open(merged_prs_path, "r") as f:
        for line in f:
            merged_pull_request = json.loads(line.strip())
            commit_hash = merged_pull_request["base_commit"]
            worktree_path = worktree_manager.create(commit_hash)
            codebase_files = worktree_manager.get_worktree_file_hierarchy(commit_hash)

            print(f"Worktree Path: {worktree_path}")
            print(f"Codebase Files: {codebase_files}")

            print(f"Length of Codebase Files: {len(codebase_files)}")

            list_files_tool = create_list_files_tool(str(worktree_path))
            read_file_tool = create_read_file_tool(str(worktree_path))
            read_diff_from_link_tool = create_read_diff_from_link_tool(merged_pull_request["diff_url"])
            tools = [list_files_tool, read_file_tool, read_diff_from_link_tool]
            
            try:
                # Create Questions
                question_agent = create_question_agent(llm, tools)
                raw_response = question_agent.invoke({"merged_pull_request": merged_pull_request, "codebase_files": codebase_files})['output'][0]["text"]
                
                try:
                    parsed_response = question_parser.parse(raw_response)
                except OutputParserException as e:
                    from utils.clean_json import JSONRepairAgent
                    json_repair_agent = JSONRepairAgent(Question)
                    parsed_response = json_repair_agent.repair_json_output(raw_response)

                print(f"Generated Question: {parsed_response.question}")
                questions.append({"question": parsed_response.question})

                # Create Answers
                answer_agent = create_answer_agent(llm, tools)
                raw_response = answer_agent.invoke({"question": parsed_response.question, "merged_pull_request": merged_pull_request, "codebase_files": codebase_files})
                raw_text = raw_response['output'][0]["text"]

                try:
                    parsed_response = answer_parser.parse(raw_text)
                except OutputParserException as e:
                    from utils.clean_json import JSONRepairAgent
                    json_repair_agent = JSONRepairAgent(Answer)
                    parsed_response = json_repair_agent.repair_json_output(raw_text)

                print(f"Generated Answer: {parsed_response.answer}")
                
                answers_and_sources.append({
                    "answer": parsed_response.answer,
                    "sources": parsed_response.sources
                })

            finally:
                worktree_manager.down(commit_hash)

            num_examples += 1
            if num_examples >= max_examples:
                break

    output_filename = f"logs/{merged_prs_path.split('/')[-2]}/sampled_questions_from_prs.jsonl"
    with open(output_filename, "w") as f:
        for question in questions:
            f.write(json.dumps(question) + "\n")

    output_filename = f"logs/{merged_prs_path.split('/')[-2]}/sampled_answers_from_prs.jsonl"
    with open(output_filename, "w") as f:
        for answer_and_source in answers_and_sources:
            f.write(json.dumps(answer_and_source) + "\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", type=str, required=True)
    parser.add_argument("--merged_prs_path", type=str, required=True)
    args = parser.parse_args()
    main(repo_path=args.repo_path, merged_prs_path=args.merged_prs_path)