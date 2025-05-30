import os
from pathlib import Path
from git import Repo, GitCommandError
import shutil
from typing import Tuple, List, Set
import uuid
from collections import defaultdict
import asyncio

DEFAULT_IGNORED_DIRS = {'.git', '.next', 'node_modules', '__pycache__', 'venv', '.venv', '.DS_Store', '.idea'}

def generate_file_tree(
    root_path: str,
    ignored_dirs: Set[str] = DEFAULT_IGNORED_DIRS,
    max_depth: int = 3,
    max_items: int = 15,
) -> str:
    """
    Generates a compact, ASCII-tree string representation of a file hierarchy
    in a single pass for maximal efficiency and compactness.
    """
    tree_lines = [os.path.basename(os.path.abspath(root_path)) + "/"]

    def _build_tree(current_path: str, prefix: str, depth: int):
        # Stop recursion if max depth is reached
        if depth >= max_depth:
            return
        try:
            # Filter, sort (directories first), and limit items in one go
            all_items = os.listdir(current_path)
            items = sorted(
                [
                    item for item in all_items 
                    if not item.startswith('.') and item not in ignored_dirs
                ],
                key=lambda item: not os.path.isdir(os.path.join(current_path, item))
            )[:max_items]
        except OSError:
            # Silently ignore directories we can't read
            return
        for i, item_name in enumerate(items):
            is_last = (i == len(items) - 1)
            connector = "└── " if is_last else "├── "
            full_path = os.path.join(current_path, item_name)

            if os.path.isdir(full_path):
                tree_lines.append(f"{prefix}{connector}{item_name}/")
                # Prepare the prefix for the next level of recursion
                new_prefix = prefix + ("    " if is_last else "│   ")
                _build_tree(full_path, new_prefix, depth + 1)
            else:
                tree_lines.append(f"{prefix}{connector}{item_name}")

    # Start the recursive build
    _build_tree(root_path, "", 0)
    return "\n".join(tree_lines)

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
    def __init__(self, repo_path: str, task: str = None):
        if task:
            self.task_id = task
        else:
            self.task_id = str(uuid.uuid4())

        self.repo_path = repo_path
        self.worktrees = {}
        self.origin_repo_path = repo_path

        repo_name = Path(self.repo_path).name

        self.base = Path(self.repo_path).resolve().parent / f"worktrees/{repo_name}/{self.task_id}"

        if not self.base.exists():
            print(f"Creating worktree directories for {repo_path} at {self.base}")
            self.base.mkdir(parents=True, exist_ok=True)
        else:
            # Ask User for Permission
            print(f"Removing existing worktrees for {repo_path} found at {self.base}")
            if input("Do you want to remove them? (y/n)") == "y":
                shutil.rmtree(self.base)
                self.base.mkdir(parents=True, exist_ok=True)
            else:
                print("Exiting...")
                exit(1)

        self.lock = asyncio.Lock()
        self.ref_counts = defaultdict(int)


    async def acquire(self, commit: str) -> Path:
        async with self.lock:
            if commit in self.worktrees and self.worktrees[commit].exists():
                self.ref_counts[commit] += 1
                return self.worktrees[commit]

            path = self.base / f"worktree_{commit}"
            self.worktrees[commit] = path
            self.ref_counts[commit] = 1  # first use

        # Outside lock: create the worktree
        repo = Repo(self.origin_repo_path)
        try:
            repo.git.worktree("prune")
        except GitCommandError:
            pass
        try:
            repo.git.rev_parse("--verify", f"{commit}^{{commit}}")
        except GitCommandError:
            repo.git.fetch("origin", commit)

        if path.exists():
            try:
                wt_repo = Repo(str(path))
                if wt_repo.head.commit.hexsha.lower() == commit.lower():
                    return path
                else:
                    shutil.rmtree(path)
            except Exception:
                shutil.rmtree(path)

        try:
            repo.git.worktree("add", "--detach", str(path), commit)
        except GitCommandError as e:
            if "already registered" in str(e) or "exists" in str(e):
                repo.git.worktree("add", "-f", "--detach", str(path), commit)
            else:
                raise RuntimeError(f"Failed to create worktree for {commit}: {e}")

        return path

    async def release(self, commit: str):
        async with self.lock:
            if commit not in self.ref_counts:
                print(f"⚠️ Attempted to release untracked worktree {commit}")
                return
            self.ref_counts[commit] -= 1
            if self.ref_counts[commit] <= 0:
                path = self.worktrees.get(commit)
                if path and path.exists():
                    shutil.rmtree(path)
                self.worktrees.pop(commit, None)
                self.ref_counts.pop(commit, None)

    def create(self, commit: str) -> Path:
        worktree_path = self.base / f"worktree_{commit}"
        self.worktrees[commit] = worktree_path
        repo = Repo(self.origin_repo_path)

        # 1) prune any broken entries
        try:
            repo.git.worktree("prune")
        except GitCommandError as e:
            print(f"⚠️ Failed to prune worktrees: {e}")

        # 2) ensure commit is local
        try:
            repo.git.rev_parse("--verify", f"{commit}^{{commit}}")
        except GitCommandError:
            repo.git.fetch("origin", commit)

        # 3) if folder already exists, validate HEAD
        if worktree_path.exists():
            try:
                wt_repo = Repo(str(worktree_path))
                current = wt_repo.head.commit.hexsha
                if current.lower() == commit.lower():
                    # ✅ already correct, bail out
                    return worktree_path
                else:
                    # ❌ wrong commit, blow it away
                    print(f"⚠️ Worktree at {worktree_path} is at {current}, expected {commit}. Re-creating…")
                    shutil.rmtree(worktree_path)
            except Exception as e:
                # e.g. not a git repo or HEAD missing
                print(f"⚠️ Could not validate existing worktree ({e}), forcing overwrite…")
                shutil.rmtree(worktree_path)

        # 4) add the worktree (with force fallback)
        try:
            repo.git.worktree("add", "--detach", str(worktree_path), commit)
        except GitCommandError as e:
            msg = str(e)
            if "already registered worktree" in msg or "exists" in msg:
                print(f"⚠️ Detected registered-but-missing worktree; forcing re-add…")
                repo.git.worktree("add", "-f", "--detach", str(worktree_path), commit)
            else:
                raise RuntimeError(f"❌ Failed to create worktree for {commit}: {e}")

        return worktree_path
    
    def get_worktree_file_hierarchy(self, worktree_id: str, max_depth: int = 3) -> str:
        if worktree_id not in self.worktrees:
            raise ValueError(f"❌ No worktree found for ID: {worktree_id}")

        worktree_path = self.worktrees[worktree_id]

        if not Path(worktree_path).exists():
            raise FileNotFoundError(f"❌ Worktree path does not exist: {worktree_path}")

        hierarchy = generate_file_tree(worktree_path, max_depth=3)
        return hierarchy
    
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


