# utils/parallel_agent_executor.py

from typing import List, Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableParallel
from langchain_core.globals import set_llm_cache
from langchain.cache import InMemoryCache
from langchain.agents import AgentExecutor, create_tool_calling_agent

# Enable in-memory caching of LLM outputs
set_llm_cache(InMemoryCache())


def cache_system_prompt(prompt: ChatPromptTemplate) -> ChatPromptTemplate:
    """
    Injects caching metadata into the system message of a ChatPromptTemplate.
    """
    updated_messages = []
    for msg in prompt.messages:
        if isinstance(msg, tuple) and msg[0] == "system":
            updated_messages.append(SystemMessage(
                content=msg[1],
                additional_kwargs={"cache-control": {"type": "ephemeral"}}
            ))
        else:
            updated_messages.append(msg)
    return ChatPromptTemplate.from_messages(updated_messages)


def run_tool_agents_in_parallel(
    prompt_template: ChatPromptTemplate,
    llm,
    tools: List[Any],
    num_agents: int,
    input_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Creates and runs multiple ToolCalling agents in parallel using the same prompt, LLM, and tools.
    """
    prompt = cache_system_prompt(prompt_template)

    agents = {}
    for i in range(num_agents):
        agent = create_tool_calling_agent(llm=llm, prompt=prompt, tools=tools)
        executor = AgentExecutor.from_agent_and_tools(agent=agent, tools=tools, verbose=False)
        agents[f"agent_{i}"] = executor

    parallel_runner = RunnableParallel(**agents)
    return parallel_runner.invoke(input_data)

if __name__ == "__main__":
    from langchain_anthropic import ChatAnthropic
    from dotenv import load_dotenv
    import os
    from langchain.agents import create_tool_calling_agent

    load_dotenv()
    
