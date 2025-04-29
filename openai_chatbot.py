# --- START OF REFACTORED FILE openai_chatbot.py ---

import os
import asyncio
import logging # Import logging
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
from prompts import system_prompt

# Import the refactored tool registry
from tools import tool_registry

# Setup logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# OpenAI API configuration (No changes)
api_key = os.getenv("OPENAI_API_KEY")
api_base = os.getenv("OPENAI_API_BASE", "https://models.inference.ai.azure.com")

# Create OpenAI client (No changes)
try:
    client = OpenAI(api_key=api_key, base_url=api_base)
except Exception as e:
     logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
     exit(1)


# Maximum number of consecutive tool calls to prevent infinite loops (No changes)
MAX_CONSECUTIVE_TOOL_CALLS = 15


def send_to_openai(messages):
    """Sends the conversation history to OpenAI and returns the AI's response text."""
    # (No changes to this function)
    try:
        logger.info(f"Sending {len(messages)} messages to OpenAI...")
        response = client.chat.completions.create(
            model="gpt-4o",  # Keep model name as is
            messages=messages,
            temperature=1,
            max_tokens=4096,
            top_p=1
        )
        content = response.choices[0].message.content
        logger.info(f"Received response from OpenAI: {content[:100]}...")
        return content
    except Exception as e:
        logger.error(f"Error with OpenAI API: {str(e)}", exc_info=True)
        return f"I encountered an error communicating with the AI model: {str(e)}"


# Initialize conversation history with system instructions (No changes)
conversation_history = [
    {
        "role": "system",
        "content": system_prompt # Use the updated system prompt
    }
]

# --- Refactored process_tool_calls ---
async def process_tool_calls(ai_response: str):
    """
    Process tool calls found in the AI response using the refactored ToolRegistry.
    Extracts calls based on TOOL_CALL:: prefix and JSON payload.
    Executes tools sequentially and adds results to history.
    """
    # Use the refactored extract_tool_calls - returns List[Tuple[str, Dict]]
    tool_calls = tool_registry.extract_tool_calls(ai_response)

    if not tool_calls:
        logger.info("No tool calls found in the response.")
        return False  # No tool calls found

    logger.info(f"Found {len(tool_calls)} tool call(s) in the response.")
    tool_results = [] # Store results before adding to history

    for i, (tool_name, args_dict) in enumerate(tool_calls):
        # Log execution attempt with new arguments format
        logger.info(f"Executing tool call {i + 1}/{len(tool_calls)}: tool='{tool_name}', args={args_dict}")

        # Use the refactored execute method with tool_name and args_dict
        result = await tool_registry.execute(tool_name, args_dict)

        logger.info(f"Tool '{tool_name}' execution result: {result[:100]}...") # Log snippet of result
        # Store result along with name and args for adding to history later
        tool_results.append((tool_name, args_dict, result))

    # Add all tool results to conversation history after processing all calls in this batch
    for tool_name, args_dict, result in tool_results:
        # Create a concise representation of args for the history message
        args_summary = str(args_dict)
        max_summary_len = 150
        if len(args_summary) > max_summary_len:
            args_summary = args_summary[:max_summary_len] + "...}"

        # Use 'system' role for tool results, consistent with previous OpenAI version
        conversation_history.append({
            "role": "system",
            "content": f"Tool execution result for '{tool_name}' with args {args_summary}:\n\n{result}"
        })
        logger.debug(f"Added result for tool '{tool_name}' to conversation history.")

    return True



async def conversation_loop():
    """Main loop for console-based interaction."""
    try:
        print("OpenAI Assistant initialized. Type 'exit' or 'quit' to end the conversation.")
        print(f"(Using model: gpt-4o, Max consecutive tool calls: {MAX_CONSECUTIVE_TOOL_CALLS})")

        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting gracefully...")
                break
            if not user_input:
                 continue

            conversation_history.append({"role": "user", "content": user_input})

            consecutive_tool_calls = 0
            while consecutive_tool_calls < MAX_CONSECUTIVE_TOOL_CALLS:
                 ai_response = send_to_openai(conversation_history)
                 conversation_history.append({"role": "assistant", "content": ai_response})
                 print("\nAI:", ai_response)

                 processed_tools = await process_tool_calls(ai_response)

                 if not processed_tools:
                     break
                 else:
                     consecutive_tool_calls += 1
                     logger.info(f"Processed tool call batch {consecutive_tool_calls}. Re-querying AI.")

            if consecutive_tool_calls == MAX_CONSECUTIVE_TOOL_CALLS:
                warning_msg = f"Reached maximum consecutive tool calls limit ({MAX_CONSECUTIVE_TOOL_CALLS})."
                print(f"\nSystem: {warning_msg}")

    except KeyboardInterrupt:
        print("\nGracefully exiting the conversation...")
    except Exception as e:
        logger.error(f"An unexpected error occurred in the conversation loop: {e}", exc_info=True)
        print(f"\nAn error occurred: {str(e)}")


if __name__ == "__main__":
    asyncio.run(conversation_loop())

