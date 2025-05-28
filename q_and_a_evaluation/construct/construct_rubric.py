from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing import List
from langchain_core.tools import tool, Tool
from pathlib import Path
import json
from pydantic import ValidationError
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor
from dotenv import load_dotenv
import os
from langchain.agents import create_tool_calling_agent
import argparse
from langchain_core.exceptions import OutputParserException

SYSTEM_PROMPT = """
You are a senior software engineer tasked with constructing a rubric to evaluate the quality of an answer to a given question. The primary focus is to assess whether the provided general plan or pseudocode demonstrates a comprehensive understanding and is sufficiently detailed to guide implementation.

The rubric should consist of a list of evaluation criteria, each with:
- A clear and concise name.
- A detailed description explaining the criterion.
- Performance levels ranging from 0 to 4, with explicit definitions for each level.

The criteria should be derived from:
- The specific question posed.
- The provided answer.
- The structure and components of the relevant codebase hierarchy.

Ensure that each criterion references specific elements from the codebase, such as modules, functions, or classes, to ground the evaluation in concrete aspects of the system.

Given:
Sample Question:
{sample_question}

Sample Answer:
{sample_answer}

Your response must include only the rubric. The rubric should be stringent, such that only an ideal answer—demonstrating thorough understanding, clear logic, and alignment with the codebase—would achieve a perfect score.

Sample Rubric:
{sample_rubric}
"""


with open('QandA_evaluation/sample_rubric.json', 'r') as f:
    sample_rubric = json.load(f)
with open('QandA_evaluation/sample_question.txt', 'r') as f:
    sample_question = f.read()
with open('QandA_evaluation/sample_answer.txt', 'r') as f:
    sample_answer = f.read()

class Criterion(BaseModel):
    name: str = Field(..., description="The name of the evaluation criterion.")
    description: str = Field(..., description="A detailed description of the criterion.")
    levels: List[str] = Field(..., description="Performance levels for this criterion.")

class Rubric(BaseModel):
    title: str = Field(..., description="The title of the rubric.")
    criteria: List[Criterion] = Field(..., description="A list of evaluation criteria.")

rubric_parser = PydanticOutputParser(pydantic_object=Rubric)

rubric_prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT + "\n{format_instructions}"),
    ("placeholder", "{chat_history}"),
    ("user", "Question:{query}"),
    ("user", "Answer: {answer}"),
    ("user", "Sources: {sources}"),
    ("placeholder", "{agent_scratchpad}")
]).partial(
    format_instructions=rubric_parser.get_format_instructions(), 
    sample_rubric=sample_rubric, 
    sample_question=sample_question, 
    sample_answer=sample_answer)


def create_rubric_tool(output_path: str):

    def write_rubric(rubric: dict = None) -> str:
        """
        Accepts a rubric object as a dictionary. Validates it using the Rubric schema.
        If valid, appends to a JSONL file. If invalid, returns a friendly error message.
        """
        if rubric is None:
            return "❌ No rubric provided"
            
        try:
            validated_rubric = Rubric(**rubric)
        except Exception as e:
            return f"❌ Invalid rubric format:\n{str(e)}"

        Path(output_path).parent.mkdir(exist_ok=True, parents=True)
        
        # Append to the JSONL file
        with Path(output_path).open("a") as f:
            f.write(json.dumps(validated_rubric.dict()) + "\n")

        return f"✅ Rubric appended to {output_path}"
    
    return Tool(
        name="write_rubric",
        description="Write a rubric to a JSONL file, will tell you if the rubric is valid or not",
        func=write_rubric
    )

def create_rubric_agent() -> AgentExecutor:
    load_dotenv()
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    
    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=None,
        stop=None,
        model_name='claude-3-5-sonnet-20240620',
    )

    tools = [create_rubric_tool('logs/rubrics.jsonl')]
    agent = create_tool_calling_agent(llm, tools=tools, prompt=rubric_prompt)

    rubric_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )
    return rubric_agent

def test_rubric_agent():
    from langchain_anthropic import ChatAnthropic
    from langchain.agents import AgentExecutor
    from dotenv import load_dotenv
    import os
    from langchain.agents import create_tool_calling_agent

    load_dotenv()

    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=None,
        stop=None,
        model_name='claude-3-5-sonnet-20240620',
    )

    tools = [write_rubric]
    agent = create_tool_calling_agent(llm, tools=tools, prompt=rubric_prompt)

    rubric_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )


    query = "How are bookings handled by the event manager?"
    answer = '''

    You're asking about how bookings are handled by the event manager in Cal.com's booking system. Based on the codebase, this involves the `EventManager` class and its integration with the booking creation and management flow.

    ## Event Manager's Role in Booking Handling

    The event manager is responsible for creating, updating, and managing calendar events across different integrations when bookings are processed. Here's how it works:

    ### Booking Creation Flow

    When a new booking is created, the event manager handles the calendar event creation through the `handleNewBooking` function [1](#3-0) . The event manager is instantiated with the organizer's credentials and app configurations.

    For confirmed bookings, the event manager creates calendar events across all configured integrations [2](#3-1) . The results from these integrations are then processed to handle video call URLs, meeting links, and other calendar-specific data.

    ### Rescheduling Logic

    For rescheduled bookings, the event manager uses a different approach. It calls the `reschedule` method to update existing calendar events [3](#3-2) . This handles cases where the organizer might change in round-robin events or when the meeting details need to be updated across all calendar integrations.

    ### API v2 Integration

    In the API v2 system, bookings are handled through the `BookingsService_2024_08_13` which orchestrates the entire booking process [4](#3-3) . This service determines the booking type (regular, recurring, seated, or instant) and delegates to the appropriate creation method.

    The service uses the platform libraries' `handleNewBooking` function [5](#3-4)  which internally manages the event manager interactions.

    ### Event Manager Results Processing

    The event manager returns results that include success/failure status for each integration, video call URLs, and calendar-specific metadata [6](#3-5) . These results are processed to:

    1. Extract video call URLs and meeting links
    2. Handle Google Meet integration specifically
    3. Update booking records with calendar event IDs
    4. Manage integration errors and fallbacks

    ### Error Handling

    The system includes comprehensive error handling for integration failures [7](#3-6) . If calendar integrations fail, the booking can still proceed, but appropriate warnings are logged and users are notified.

    ## Notes

    The event manager is a critical component that abstracts calendar integrations, allowing Cal.com to work with multiple calendar providers (Google Calendar, Outlook, etc.) seamlessly. The booking flow ensures that even if some integrations fail, the core booking functionality remains intact, providing a robust scheduling experience.

    Wiki pages you might want to explore:
    - [API Architecture (calcom/cal.com)](/wiki/calcom/cal.com#3)


    '''

    sources = '''
    - packages/features/bookings/lib/handleNewBooking.ts
    - apps/api/v2/src/ee/bookings/2024-08-13/services/bookings.service.ts
    '''

    raw_response = rubric_agent.invoke({
        "query": query,
        "answer": answer,
        "sources": sources
    })

    print(raw_response['output'][0]["text"])
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--question_path", type=str, required=True)
    parser.add_argument("--answer_path", type=str, required=True)
    parser.add_argument("--output_path")
    args = parser.parse_args()

    from langchain_anthropic import ChatAnthropic
    from langchain.agents import AgentExecutor
    from dotenv import load_dotenv
    import os
    from langchain.agents import create_tool_calling_agent

    load_dotenv()

    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    llm = ChatAnthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=None,
        stop=None,
        model_name='claude-3-5-sonnet-20240620',
    )

    output_path = f"logs/{args.question_path.split('/')[-2]}/rubrics.jsonl"
    tools = []
    agent = create_tool_calling_agent(llm, tools=tools, prompt=rubric_prompt)

    rubric_agent = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        verbose=True,
        return_intermediate_steps=True
    )

    # Open all files simultaneously
    with open(args.question_path, 'r') as q_file, open(args.answer_path, 'r') as a_file:
        # Create iterators for each file
        questions = (json.loads(line.strip()) for line in q_file)
        answers = (json.loads(line.strip()) for line in a_file)

        rubrics = {}
        
        # Process each line from all files simultaneously
        for question, answer in zip(questions, answers):
            raw_response = rubric_agent.invoke({
                "query": question,
                "answer": answer["answer"],
                "sources": answer["sources"]
            })
            try:
                parsed_response = rubric_parser.parse(raw_response['output'][0]["text"])
            except OutputParserException as e:
                from utils.clean_json import JSONRepairAgent
                json_repair_agent = JSONRepairAgent(Rubric)
                parsed_response = json_repair_agent.repair_json_output(raw_response['output'][0]["text"])

            print(f"Rubric: {parsed_response.dict()}")

            rubrics[question["question"]] = parsed_response.dict()

        with open(output_path, 'a') as f:
            f.write(json.dumps(rubrics) + "\n")  

    print(f"Rubric written to {output_path}")