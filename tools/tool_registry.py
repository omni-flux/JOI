import inspect
import json
import logging
from typing import Dict, Callable, Awaitable, Union, List, Tuple, Any


logger = logging.getLogger(__name__)

ToolHandler = Union[Callable[[Dict[str, Any]], str], Callable[[Dict[str, Any]], Awaitable[str]]]

class ToolRegistry:
    """Registry for managing and executing tools based on JSON calls."""

    def __init__(self):
        """Initializes the registry, storing tools by name."""
        self.tools: Dict[str, ToolHandler] = {}
        logger.info("ToolRegistry initialized.")

    def register(self, name: str, function: ToolHandler):
        """Register a tool with its name and handler function."""
        if not callable(function):
            raise TypeError(f"Handler for tool '{name}' must be a callable function, got {type(function)}")
        if name in self.tools:
             logger.warning(f"Tool '{name}' is being re-registered. Overwriting previous handler.")
        self.tools[name] = function
        logger.debug(f"Registered tool: '{name}'")

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        """
        Execute a tool by name with the provided arguments dictionary.
        Handles both synchronous and asynchronous tool functions.

        Args:
            tool_name (str): The name of the tool to execute (e.g., "fs_write").
            args (Dict[str, Any]): The dictionary of arguments extracted from the JSON payload.

        Returns:
            str: The result string from the executed tool function.
        """
        if tool_name not in self.tools:
            logger.error(f"Attempted to execute unknown tool: '{tool_name}'")
            return f"Error: Unknown tool '{tool_name}'. Available tools: {', '.join(self.tools.keys())}"

        handler = self.tools[tool_name]
        logger.info(f"Executing tool '{tool_name}' with args: {args}")

        try:

            if inspect.iscoroutinefunction(handler):
                result = await handler(args)
            else:
                result = handler(args)

            logger.info(f"Tool '{tool_name}' executed successfully.")
            return str(result) if result is not None else "Tool executed successfully, but returned no output."

        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}' with args {args}: {e}", exc_info=True)
            return f"Error: An exception occurred while executing tool '{tool_name}': {type(e).__name__} - {e}"

    def extract_tool_calls(self, text: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Extracts tool calls from text based on the TOOL_CALL:: prefix and JSON payload.

        Args:
            text (str): The text potentially containing tool call lines.

        Returns:
            List[Tuple[str, Dict[str, Any]]]: A list of tuples, where each tuple contains
                                              the tool name (str) and its arguments dictionary.
                                              Returns an empty list if no valid calls are found.
        """
        tool_calls: List[Tuple[str, Dict[str, Any]]] = []
        lines = text.splitlines()
        prefix = "TOOL_CALL::"

        for i, line in enumerate(lines):
            trimmed_line = line.strip()
            if trimmed_line.startswith(prefix):
                json_str = trimmed_line[len(prefix):].strip()
                if not json_str:
                     logger.warning(f"Found '{prefix}' on line {i+1} but no JSON payload followed.")
                     continue

                try:
                    payload = json.loads(json_str)
                    # Validate the structure
                    if not isinstance(payload, dict):
                        logger.warning(f"Parsed payload on line {i+1} is not a dictionary: {payload}")
                        continue
                    if "tool" not in payload or not isinstance(payload.get("tool"), str):
                        logger.warning(f"Parsed payload on line {i+1} missing or invalid 'tool' key: {payload}")
                        continue
                    if "args" not in payload or not isinstance(payload.get("args"), dict):
                        logger.warning(f"Parsed payload on line {i+1} missing or invalid 'args' key: {payload}")
                        continue

                    # Extract validated data
                    tool_name = payload["tool"]
                    args_dict = payload["args"]

                    # Check if tool name is actually registered
                    if tool_name not in self.tools:
                         logger.warning(f"Extracted tool call for unregistered tool '{tool_name}' on line {i+1}. Skipping.")
                         continue

                    logger.info(f"Successfully extracted tool call on line {i+1}: tool='{tool_name}', args={args_dict}")
                    tool_calls.append((tool_name, args_dict))

                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to decode JSON on line {i+1} after '{prefix}': {e}. Payload was: '{json_str}'")
                except Exception as e:
                    logger.error(f"Unexpected error processing line {i+1} starting with '{prefix}': {e}", exc_info=True)

        if not tool_calls:
            logger.debug("No valid tool calls found in the provided text.")
        else:
            logger.debug(f"Extracted {len(tool_calls)} tool call(s).")

        return tool_calls
