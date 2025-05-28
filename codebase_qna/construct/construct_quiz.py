from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

class Quiz(BaseModel):
    quiz: str = Field(description="The quiz for the evaluation")

parser = PydanticOutputParser(pydantic_object=Quiz)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a expert software engineer that can construct a quiz for the evaluation of a question and answer pair. \n{format_instructions}"),
])