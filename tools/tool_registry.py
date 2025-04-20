from typing import Dict, Callable, Awaitable,Union, List, Tuple, Optional
import inspect
import re

ToolHandler = Union[Callable[..., str], Callable[..., Awaitable[str]]]

class ToolRegistry:
    """Registry for managing available tools (Minimal Change Version)."""

    def __init__(self):
        self.tools: Dict[str, ToolHandler] = {}
        self.patterns: Dict[str, str] = {}
        self.priorities: Dict[str, int] = {}


    def register(self, name: str, function: ToolHandler, pattern: str, priority: int):
        """Register a tool with its name, handler function, regex pattern, and EXPLICIT priority."""

        if not callable(function):
             raise TypeError(f"Handler for tool '{name}' must be a callable function, got {type(function)}")
        self.tools[name] = function
        self.patterns[name] = pattern
        self.priorities[name] = priority

    async def execute(self, tool_type: str, tool_argument: Optional[str]) -> str: # tool_argument can be None
        """
        Execute a tool and handle both sync and async handlers.
        **MODIFIED** to handle tools requiring one or two arguments.
        """
        if tool_type not in self.tools:
            return f"Unknown tool type: {tool_type}"

        handler = self.tools[tool_type]

        try:
            if tool_type in ["fs_write", "fs_find"]:
                if tool_argument is None or '|' not in tool_argument:
                    usage = f"[{tool_type.upper()}: {'path/file.txt | content' if tool_type == 'fs_write' else 'start_path | *.pattern'}]"
                    return f"Error: Invalid arguments for {tool_type}. Usage: {usage}. Got: '{tool_argument}'"

                parts = tool_argument.split('|', 1)
                arg1 = parts[0].strip()
                arg2 = parts[1].strip()

                if not arg1:
                     return f"Error: Path/start_path argument cannot be empty for {tool_type}."

                if inspect.iscoroutinefunction(handler):
                    return await handler(arg1, arg2)
                else:
                    return handler(arg1, arg2)

            else:
                actual_argument = tool_argument


                if tool_type == "sysinfo" and not actual_argument:
                    actual_argument = "basic"
                elif actual_argument is None and tool_type != "sysinfo":
                     return f"Error: Missing argument for tool {tool_type}."


                if inspect.iscoroutinefunction(handler):
                    return await handler(actual_argument)
                else:
                    return handler(actual_argument)


        except Exception as e:
             return f"Error: A {e} error occurred while executing tool '{tool_type}'."


    def extract_tool_calls(self, text: str) -> List[Tuple[str, str]]:
        """Extract all tool calls from text using registered patterns, sorted by priority."""
        tool_calls = []

        sorted_tools = sorted(self.patterns.items(),
                              key=lambda item: self.priorities.get(item[0], float('inf')))

        processed_text = text
        for tool_name, pattern in sorted_tools:
            try:
                # Find all matches in the current text state
                matches = re.finditer(pattern, processed_text)
                new_processed_text = ""
                last_end = 0
                for match in matches:
                    new_processed_text += processed_text[last_end:match.start()]
                    new_processed_text += "[#MATCHED#]" * len(match.group(0))
                    last_end = match.end()

                    if match.lastindex and match.lastindex >= 1:
                         tool_calls.append((tool_name, match.group(1).strip()))
                    else:
                         tool_calls.append((tool_name, ""))

                new_processed_text += processed_text[last_end:]
                processed_text = new_processed_text.replace("[#MATCHED#]", "")

            except re.error as e:
                 print(f'error:{e} while doing regx')
                 continue

        return tool_calls