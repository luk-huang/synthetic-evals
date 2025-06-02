from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_anthropic import ChatAnthropic
from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
import asyncio

load_dotenv()

async def main():
    mcp_client = MultiServerMCPClient(
        {
        "deepwiki": {
            "url": "https://mcp.deepwiki.com/sse",
            "transport": "sse"}
        }
    )

    tools = await mcp_client.get_tools()

    agent = create_react_agent(
        model = "anthropic:claude-3-7-sonnet-20250219",
        tools = tools,
        prompt = "You are a helpful assistant that can answer questions about the codebase. You are given a question and a codebase. You need to answer the question based on the codebase. You can use the tools provided to you to answer the question.",
    )

    response = await agent.ainvoke({"messages": "What is the purpose of the cal.com repository?"})

    print(response["messages"][-1].content)

if __name__ == "__main__":
    asyncio.run(main())