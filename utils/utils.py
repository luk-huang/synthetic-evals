import re
import json5  # more forgiving than json

def extract_json(text: str) -> dict:
    """
    Tries to extract the first valid JSON object from a larger LLM response.
    Uses a regex-based search and json5 for fault-tolerant parsing.
    """
    json_blocks = re.findall(r"\{(?:[^{}]|(?R))*\}", text, re.DOTALL)

    for block in json_blocks:
        try:
            return json5.loads(block)
        except Exception as e:
            continue

    raise ValueError("No valid JSON object found in response.")
