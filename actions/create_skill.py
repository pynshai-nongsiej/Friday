import json
import re
import sys
from pathlib import Path

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

TOOL_SCHEMA = """
create_skill
  skill_name: string (required) — snake_case name of the new skill
  description: string (required) — detailed description of what the skill should do
"""

def create_skill(parameters: dict, player=None, **kwargs) -> str:
    skill_name = parameters.get("skill_name", "").strip()
    description = parameters.get("description", "").strip()
    
    if not skill_name or not description:
        return "Please provide both skill_name and description, sir."
        
    # Sanitize skill_name to snake_case
    skill_name = re.sub(r"[^\w\-]", "_", skill_name).lower()
    
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    prompt = f"""You are an expert Python developer building a skill for FRIDAY, an AI assistant.
Write a single Python file for a new skill.

Skill Name: {skill_name}
Goal: {description}

The file MUST follow this exact structure:

```python
# actions/{skill_name}.py

TOOL_SCHEMA = \"\"\"
{skill_name}
  # List parameters here in this format:
  # param_name: type (required/optional) — description
\"\"\"

def {skill_name}(parameters: dict, player=None, **kwargs) -> str:
    # Implement the skill here
    # Extract parameters
    # Do the work
    # Return a string summary of the result
    return "Result summary"
```

Rules:
1. Include the `TOOL_SCHEMA` variable with the tool name and parameters. This is critical for the planner to discover it.
2. The main function must be named exactly `{skill_name}`.
3. Handle errors gracefully and return informative strings.
4. Use standard library or common packages if needed.
5. Return ONLY the executable Python code. No explanation, no markdown, no backticks.

Code:"""

    try:
        response = model.generate_content(prompt)
        code = response.text.strip()
        code = re.sub(r"^```[a-zA-Z]*\\n?", "", code)
        code = re.sub(r"\\n?```$", "", code)
        code = code.strip()
        
        # Verify it has TOOL_SCHEMA and the function
        if "TOOL_SCHEMA" not in code or f"def {skill_name}" not in code:
            return "Failed to generate valid skill structure. Please try again with a better description."
            
        file_path = BASE_DIR / "actions" / f"{skill_name}.py"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
            
        return f"Skill '{skill_name}' created successfully, sir. It is saved at actions/{skill_name}.py and is ready for use on the next turn."
        
    except Exception as e:
        return f"Failed to create skill: {e}"
