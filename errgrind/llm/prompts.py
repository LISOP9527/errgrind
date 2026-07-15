import os


PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")


class PromptManager:
    def __init__(self, prompts_dir: str = PROMPTS_DIR):
        self.prompts_dir = prompts_dir

    def load(self, name: str) -> str:
        path = os.path.join(self.prompts_dir, name)
        with open(path, encoding="utf-8") as f:
            return f.read()
