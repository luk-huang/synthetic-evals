import os
from pathlib import Path
from git import Repo, GitCommandError
import shutil
from typing import Tuple, List

DEFAULT_IGNORED_DIRS = {'.git', '.next', 'node_modules', '__pycache__', 'venv', '.venv', '.DS_Store', '.idea'}

def get_file_hierarchy(
    path: str,
    ignored_dirs=DEFAULT_IGNORED_DIRS,
    max_depth=2,
    max_files_per_dir=100,
    include_file_sizes=False
) -> dict:
    """
    Recursively collects file hierarchy up to a max depth and file count per directory.
    """
    def _get(path: str, depth: int):
        if depth > max_depth:
            return {}

        hierarchy = {}
        try:
            items = sorted(os.listdir(path))[:max_files_per_dir]
            for item in items:
                if item.startswith('.') or item in ignored_dirs:
                    continue
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    hierarchy[item] = _get(full_path, depth + 1)
                else:
                    if include_file_sizes:
                        hierarchy[item] = os.path.getsize(full_path)
                    else:
                        hierarchy[item] = None
        except Exception as e:
            print(f"Error accessing {path}: {str(e)}")
        return hierarchy

    return _get(path, 0)


def flatten_hierarchy(hierarchy: dict, path: str = "", include_sizes=False) -> List[str]:
    """
    Flattens the hierarchy dict into a list of "dir/subdir/file" strings.
    """
    flat = []
    for name, value in sorted(hierarchy.items()):
        full_path = os.path.join(path, name) if path else name
        if isinstance(value, dict):
            flat.append(full_path + "/")
            flat.extend(flatten_hierarchy(value, full_path, include_sizes))
        else:
            if include_sizes and isinstance(value, int):
                size_str = f"{value}B" if value < 1024 else f"{value/1024:.1f}KB"
                flat.append(f"{full_path} ({size_str})")
            else:
                flat.append(full_path)
    return flat

def add_lines_list(content):
    content_with_lines = list()
    for ix, line in enumerate(content.split("\n"), start=1):
        content_with_lines.append(f"{ix} {line}")
    return content_with_lines

def add_lines(content):
    return "\n".join(add_lines_list(content))

def make_code_text(files_dict, add_line_numbers=True):
    all_text = ""
    for filename, contents in sorted(files_dict.items()):
        all_text += f"[start of {filename}]\n"
        if add_line_numbers:
            all_text += add_lines(contents)
        else:
            all_text += contents
        all_text += f"\n[end of {filename}]\n"
    return all_text.strip("\n")

class WorktreeManager:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.worktrees = {}
        self.origin_repo_path = repo_path
        self.base = Path(self.repo_path).resolve().parent / "worktrees"

    def create(self, commit: str) -> Path:
        worktree_id = commit
        worktree_path = self.base / f"worktree_{worktree_id}"
        self.worktrees[worktree_id] = worktree_path

        repo = Repo(self.origin_repo_path)

        # Clean up stale worktrees (prune broken entries)
        try:
            repo.git.worktree("prune")
        except GitCommandError as e:
            print(f"⚠️ Failed to prune worktrees: {e}")

        # Fetch commit if needed
        try:
            repo.git.rev_parse("--verify", f"{commit}^{{commit}}")
        except GitCommandError:
            repo.git.fetch("origin", commit)

        # Add worktree (force if it is registered but missing)
        try:
            repo.git.worktree("add", "--detach", str(worktree_path), commit)
        except GitCommandError as e:
            if "already registered worktree" in str(e):
                print(f"⚠️ Detected registered but missing worktree. Forcing re-add...")
                repo.git.worktree("add", "-f", "--detach", str(worktree_path), commit)
            else:
                raise RuntimeError(f"❌ Failed to create worktree for {commit}: {e}")

        return worktree_path

    
    def get_worktree_file_hierarchy(self, worktree_id: str) -> str:
        if worktree_id not in self.worktrees:
            raise ValueError(f"❌ No worktree found for ID: {worktree_id}")

        worktree_path = self.worktrees[worktree_id]

        if not Path(worktree_path).exists():
            raise FileNotFoundError(f"❌ Worktree path does not exist: {worktree_path}")

        hierarchy = get_file_hierarchy(worktree_path)
        return "\n".join(flatten_hierarchy(hierarchy))
    
    def down(self, worktree_id: str):
        worktree_path = self.worktrees[worktree_id]
        shutil.rmtree(worktree_path)
        del self.worktrees[worktree_id]



if __name__ == "__main__":
    # Example usage
    codebase_path = os.getenv("CAL_COM_REPO_PATH")
    if codebase_path:
        hierarchy = get_file_hierarchy(codebase_path)
        formatted_hierarchy = flatten_hierarchy(hierarchy)
        print("\n".join(formatted_hierarchy))
    else:
        print("Please set the CAL_COM_REPO_PATH environment variable") 


