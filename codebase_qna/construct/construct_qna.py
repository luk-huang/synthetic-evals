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
from utils.agent_tools import create_list_files_tool, create_read_file_tool, create_read_diff_from_link_tool
from utils.codebase_utils import WorktreeManager
from codebase_qna.prompt_templates.prompts import QUESTION_SYSTEM_PROMPT, ANSWER_SYSTEM_PROMPT

class Question(BaseModel):
    question: str = Field(..., description="The question that was asked.")

class Answer(BaseModel):
    answer: str = Field(..., description="The answer to the question.")
    sources: List[str] = Field(..., description="The sources that were used to answer the question.")

question_parser = PydanticOutputParser(pydantic_object=Question)
answer_parser = PydanticOutputParser(pydantic_object=Answer)

question_prompt = ChatPromptTemplate.from_messages([
    ("system", QUESTION_SYSTEM_PROMPT),
    ("assistant", "{format_instructions}"),
    ("placeholder", "{agent_scratchpad}"),
    ("placeholder", "{chat_history}"),
    ("user", "Merged Pull Request: {merged_pull_request}"),
    ("user", "Codebase Files: {codebase_files}"),
    ("assistant", "{format_instructions}"),
    # ("assistant", "Here is your rubric in the desired format: {{")
]).partial(
    format_instructions=question_parser.get_format_instructions()
)

answer_prompt = ChatPromptTemplate.from_messages([
    ("system", ANSWER_SYSTEM_PROMPT),
    ("assistant", "{format_instructions}"),
    ("placeholder", "{agent_scratchpad}"),
    ("placeholder", "{chat_history}"),
    ("user", "Question: {question}"),
    ("user", "Merged Pull Request: {merged_pull_request}"),
    ("user", "Codebase Files: {codebase_files}"),
    ("assistant", "{format_instructions}"),
    # ("assistant", "Here is your rubric in the desired format: {{")
]).partial( 
    format_instructions=answer_parser.get_format_instructions()
)

def create_question_agent(llm, tools: List[Tool]) -> AgentExecutor:
    agent = create_tool_calling_agent(llm, tools=tools, prompt=question_prompt)

    question_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True,
        max_iterations=None
    )
    return question_agent

def create_answer_agent(llm, tools: List[Tool]) -> AgentExecutor:

    agent = create_tool_calling_agent(llm, tools=tools, prompt=answer_prompt)

    answer_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True,
        max_iterations=None
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
    from utils.json_repair import JSONRepairAgent
    json_repair_agent = JSONRepairAgent(model_name='gpt-4o-mini')

    questions_answers = []

    num_examples = 0
    max_examples = 1
    
    with open(merged_prs_path, "r") as f:
        for line in f:
            merged_pull_request = json.loads(line.strip())
            commit_hash = merged_pull_request["base_commit"]
            worktree_path = worktree_manager.create(commit_hash)
            codebase_files = worktree_manager.get_worktree_file_hierarchy(commit_hash)

            print(f"Worktree Path: {worktree_path}")
            print(f"Length of Codebase Files: {len(codebase_files)}")

            list_files_tool = create_list_files_tool(str(worktree_path))
            read_file_tool = create_read_file_tool(str(worktree_path))
            read_diff_from_link_tool = create_read_diff_from_link_tool(merged_pull_request["diff_url"])
            tools = [list_files_tool, read_file_tool, read_diff_from_link_tool]
            
            try:
                # Create Questions
                question_agent = create_question_agent(llm, tools)
                raw_response = question_agent.invoke({"merged_pull_request": merged_pull_request, "codebase_files": codebase_files})
                raw_text = raw_response['output'][0]["text"]
                try:
                    parsed_response = question_parser.parse("{" + raw_text)
                except OutputParserException as e:
                    print(f"Error parsing answer: {raw_text}")
                    parsed_response = json_repair_agent.repair_json_output(raw_text, Question)

                generated_question = parsed_response.question
                print(f"Generated Question: {generated_question}")

                # Create Answers
                answer_agent = create_answer_agent(llm, tools)
                raw_response = answer_agent.invoke({"question": generated_question, "merged_pull_request": merged_pull_request, "codebase_files": codebase_files})
                raw_text = raw_response['output'][0]["text"]

                try:
                    parsed_response = answer_parser.parse("{" + raw_text)
                except OutputParserException as e:
                    print(f"Error parsing answer: {raw_text}")
                    parsed_response = json_repair_agent.repair_json_output(raw_text, Answer)

                print(f"Generated Answer: {parsed_response.answer}")
                questions_answers.append({
                    "pr_number": merged_pull_request["number"],
                    "pr_url": merged_pull_request["url"],
                    "diff_url": merged_pull_request["diff_url"],
                    "commit_hash": commit_hash,
                    "question": generated_question,
                    "answer": parsed_response.answer,
                    "sources": parsed_response.sources
                })
            finally:
                worktree_manager.down(commit_hash)

            num_examples += 1
            if num_examples >= max_examples:
                break

    output_filename = f"logs/{merged_prs_path.split('/')[-2]}/qna_from_prs.jsonl"
    with open(output_filename, "w") as f:
        for question_answer in questions_answers:
            f.write(json.dumps(question_answer) + "\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_path", type=str, required=True)
    parser.add_argument("--merged_prs_path", type=str, required=True)
    args = parser.parse_args()
    main(repo_path=args.repo_path, merged_prs_path=args.merged_prs_path)