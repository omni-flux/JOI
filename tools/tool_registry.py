from typing import Dict, Callable, Awaitable, Any, Union, List, Tuple, Optional
import inspect
import re

# Type for tool handlers - can be sync or async functions
ToolHandler = Union[Callable[..., str], Callable[..., Awaitable[str]]] # Allow variable args


class ToolRegistry:
    """Registry for managing available tools (Minimal Change Version)."""

    def __init__(self):
        # Using the structure from your OLD code
        self.tools: Dict[str, ToolHandler] = {}
        self.patterns: Dict[str, str] = {}
        self.priorities: Dict[str, int] = {}
        # Keep track of next default priority if needed
        # self._next_priority = 0 # You can manage priorities explicitly in __init__.py

    # --- MINIMAL CHANGE START ---
    # Changed parameters from tool_type, handler to name, function
    def register(self, name: str, function: ToolHandler, pattern: str, priority: int):
        """Register a tool with its name, handler function, regex pattern, and EXPLICIT priority."""
        # Using the structure from your OLD code, but ensuring priority is required
        if not callable(function):
             # Added a check to ensure the function passed is actually callable
             raise TypeError(f"Handler for tool '{name}' must be a callable function, got {type(function)}")
        self.tools[name] = function # Use 'name' and 'function' internally now
        self.patterns[name] = pattern
        self.priorities[name] = priority
        # print(f"Registered tool (minimal): '{name}' with priority {priority}") # Optional debug print
    # --- MINIMAL CHANGE END ---

    async def execute(self, tool_type: str, tool_argument: Optional[str]) -> str: # tool_argument can be None
        """
        Execute a tool and handle both sync and async handlers.
        **MODIFIED** to handle tools requiring one or two arguments.
        """
        if tool_type not in self.tools:
            return f"Unknown tool type: {tool_type}"

        handler = self.tools[tool_type]

        try:
            # --- ESSENTIAL CHANGE: Argument Handling ---
            if tool_type in ["fs_write", "fs_find"]:
                # These tools expect two arguments separated by '|'
                if tool_argument is None or '|' not in tool_argument:
                    # Provide specific error message based on tool
                    usage = f"[{tool_type.upper()}: {'path/file.txt | content' if tool_type == 'fs_write' else 'start_path | *.pattern'}]"
                    return f"Error: Invalid arguments for {tool_type}. Usage: {usage}. Got: '{tool_argument}'"

                parts = tool_argument.split('|', 1)
                arg1 = parts[0].strip()
                arg2 = parts[1].strip() # Content/pattern can be empty

                if not arg1: # Path/start_path cannot be empty
                     return f"Error: Path/start_path argument cannot be empty for {tool_type}."

                # Call handler with TWO arguments
                if inspect.iscoroutinefunction(handler):
                    return await handler(arg1, arg2)
                else:
                    # Handle sync function (though all our FS tools are async)
                    # This branch might need adjustment if sync 2-arg tools exist
                    return handler(arg1, arg2)

            else:
                # --- Original Logic for single-argument tools ---
                # Handle tools requiring one argument (or optional like sysinfo)
                actual_argument = tool_argument # Pass the raw argument string

                # Handle sysinfo default specifically if needed
                if tool_type == "sysinfo" and not actual_argument:
                    actual_argument = "basic"
                elif actual_argument is None and tool_type != "sysinfo": # Check required arguments
                     # Most tools require an argument, except potentially sysinfo
                     return f"Error: Missing argument for tool {tool_type}."


                # Call handler with ONE argument
                if inspect.iscoroutinefunction(handler):
                    return await handler(actual_argument)
                else:
                    # It's a regular sync function
                    return handler(actual_argument)
            # --- End of Essential Change ---

        except Exception as e:
             # Basic error handling
             # Consider adding logging here
             # print(f"Error executing tool '{tool_type}': {e}") # Debug print
             return f"Error: An unexpected error occurred while executing tool '{tool_type}'. Check logs."


    def extract_tool_calls(self, text: str) -> List[Tuple[str, str]]:
        """Extract all tool calls from text using registered patterns, sorted by priority."""
        # Using the structure from your OLD code
        tool_calls = []

        # Sort tools by priority (lower number = higher priority)
        sorted_tools = sorted(self.patterns.items(),
                              key=lambda item: self.priorities.get(item[0], float('inf')))

        # Extract tool calls based on priority
        # Simple extraction - assumes non-overlapping markers primarily
        processed_text = text
        # Use tool_name consistent with the rest of the class scope
        for tool_name, pattern in sorted_tools:
            try:
                # Find all matches in the current text state
                matches = re.finditer(pattern, processed_text)
                new_processed_text = ""
                last_end = 0
                for match in matches:
                    # Add text before match
                    new_processed_text += processed_text[last_end:match.start()]
                    # Add placeholder for matched text to prevent re-matching inner parts
                    new_processed_text += "[#MATCHED#]" * len(match.group(0))
                    last_end = match.end()

                    # Extract argument (assuming group 1)
                    if match.lastindex and match.lastindex >= 1:
                         # Use tool_name consistent with registration/execution
                         tool_calls.append((tool_name, match.group(1).strip()))
                    else:
                         # Handle pattern that matched but didn't capture group 1 if necessary
                         # print(f"Warning: Pattern for '{tool_name}' matched but didn't capture group 1.")
                         # Use tool_name consistent with registration/execution
                         tool_calls.append((tool_name, "")) # Or None, depending on how you handle it

                # Add text after the last match
                new_processed_text += processed_text[last_end:]
                processed_text = new_processed_text.replace("[#MATCHED#]", "") # Clean up placeholders for next iteration if needed

            except re.error as e:
                 # Use tool_name consistent with registration/execution
                 # print(f"Regex error for tool '{tool_name}' with pattern '{pattern}': {e}")
                 continue # Skip this tool if regex is invalid

        # The sorting ensures priority, but this extraction method is basic.
        # It might find overlapping markers if not careful with regex.
        return tool_calls