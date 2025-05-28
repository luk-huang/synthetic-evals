from langchain_core.tools import tool, Tool
import os
import requests

def save_file(content: str, file_path: str = "log/log.txt") -> str:
    with open(file_path, 'w') as file:
        file.write(content)
        return "File saved successfully"
    
save_file_tool = Tool(
    name="save_file",
    description="Save a file to the file system",
    func=save_file
)

def read_diff_from_link(diff_url: str) -> str:
    response = requests.get(diff_url)
    return response.text

def create_read_diff_from_link_tool(diff_url: str) -> Tool:
    def custom_read_diff_from_link(diff_url: str) -> str:
        return read_diff_from_link(diff_url)
    return Tool(
        name="read_diff_from_link",
        description=f"Read a diff for the link {diff_url} for the merged pull request",
        func=custom_read_diff_from_link
    )

def read_file(file_path: str) -> str:
    if not os.path.exists(file_path):
        return "File not found"
    with open(file_path, 'r') as file:
        return file.read()
    
def create_read_file_tool(root_path: str) -> Tool:
    def custom_read_file(file_path: str) -> str:
        file_path = os.path.join(root_path, file_path)
        if not os.path.exists(file_path):
            return "File not found"
        with open(file_path, 'r') as file:
            return file.read()
        
    return Tool(
        name="read_file",
        description="Read a file and return the content",
        func=custom_read_file
    )

def create_list_files_tool(root_path: str) -> Tool:
    def custom_list_files(file_path: str) -> str:
        file_path = os.path.join(root_path, file_path)
        entries = os.listdir(file_path)
        return "\n".join(entries)
    return Tool(
        name="list_files",
        description="List the files in a directory",
        func=custom_list_files
    )
    
read_file_tool = Tool(
    name="read_file",
    description="Read a file and return the content",
    func=read_file
)
    
def list_files(file_path: str) -> str:
    try:
        entries = os.listdir(file_path)
        return "\n".join(entries)
    except Exception as e:
        return f"Error listing files: {str(e)}"
    

list_files_tool = Tool(
    name="list_files",
    description="List the files in a directory",
    func=list_files
)



    
