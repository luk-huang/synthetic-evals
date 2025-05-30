from pathlib import Path
import json
from typing import List
import random
import time
import json
from typing import Type
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain.output_parsers import OutputFixingParser, PydanticOutputParser
from langchain_openai import ChatOpenAI
import openai
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
import asyncio
import os

def clean_json(input_path: Path, output_path: Path) -> None:
    """
    Reads any number of JSON objects concatenated in the file (with or without newlines),
    and writes each one as its own line in output_path.
    """
    text = input_path.read_text()
    decoder = json.JSONDecoder()
    pos = 0
    cleaned: List[dict] = []
    length = len(text)

    while pos < length:
        # skip any whitespace or separators
        while pos < length and text[pos].isspace():
            pos += 1

        if pos >= length:
            break

        try:
            obj, idx = decoder.raw_decode(text, pos)
            cleaned.append(obj)
            pos = idx
        except json.JSONDecodeError:
            # If we hit malformed JSON at this position, advance by one char and retry
            pos += 1

    # write each object as a separate JSONL line
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as outfile:
        for obj in cleaned:
            json.dump(obj, outfile)
            outfile.write("\n")

# ✅ Decorator factory
def retry_with_exponential_backoff(
    initial_delay: float = 1,
    exponential_base: float = 2,
    jitter: bool = True,
    max_retries: int = 10,
    errors: tuple = (openai.RateLimitError,),
):
    def decorator(func):
        def wrapper(*args, **kwargs):
            num_retries = 0
            delay = initial_delay

            while True:
                try:
                    return func(*args, **kwargs)
                except errors as e:
                    num_retries += 1
                    if num_retries > max_retries:
                        raise Exception(f"Max retries ({max_retries}) exceeded: {e}")
                    delay *= exponential_base * (1 + jitter * random.random())
                    print(f"Rate limit hit. Retrying in {delay:.2f} seconds...")
                    time.sleep(delay)
                except Exception:
                    raise  # Don't retry unexpected errors
        return wrapper
    return decorator


class JSONRepairAgent():
    def __init__(self, model_name: str = "gpt-4.1-mini"):
        load_dotenv()
        self.llm = ChatOpenAI(model_name=model_name, temperature=0.0)

    def repair_json_output(self, raw_output: str, schema_model: Type[BaseModel]) -> BaseModel:
        """
        Attempts to parse and repair a JSON output to match the provided Pydantic schema.
        """

        self.parser = PydanticOutputParser(pydantic_object=schema_model)

        @retry_with_exponential_backoff()
        def parse_with_backoff(output: str):
            return OutputFixingParser.from_llm(llm=self.llm, parser=self.parser, max_retries=0).parse(output)
        
        return parse_with_backoff(raw_output)
    
class ClaudeJSONRepairAgent():
    def __init__(self, model_name: str = "claude-3-5-sonnet-20240620"):
        load_dotenv()
        self.llm = ChatAnthropic(
            model_name=model_name,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            timeout=None,
            stop=None,
        )

    async def repair_json_output(self, raw_output: str, schema_model: Type[BaseModel]) -> BaseModel:
        """
        Attempts to parse and repair a JSON output to match the provided Pydantic schema.
        """
        
        parser = PydanticOutputParser(pydantic_object=schema_model)
        
        system_prompt = """
        You are a JSON repair agent. You are given a close to valid JSON output and a Pydantic schema.
        You need to repair or extract the JSON output to match the Pydantic schema.
        """

        user_prompt = f"""
        JSON output: {raw_output}
        """
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", user_prompt),
            ("assistant", "{format_instructions}"),
            ("placeholder", "{agent_scratchpad}"),
            ("assistant", "Here is the correctly formatted JSON: {{")
        ]).partial(
            format_instructions=parser.get_format_instructions()
        )

        chain = prompt | self.llm | OutputFixingParser.from_llm(llm=self.llm, parser=parser, max_retries=2)
        fixed_model: BaseModel = await chain.ainvoke({"raw_output": raw_output})  # this returns the parsed BaseModel
        
        return fixed_model
    



if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract all JSON objects from a file and write them as JSONL."
    )
    parser.add_argument("--input_path",  type=Path, required=True)
    parser.add_argument("--output_path", type=Path, required=True)
    args = parser.parse_args()

    clean_json(args.input_path, args.output_path)