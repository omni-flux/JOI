import os
import asyncio
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
from prompts import system_prompt

from tools import tool_registry

# OpenAI API configuration
api_key = os.getenv("OPENAI_API_KEY")
api_base = os.getenv("OPENAI_API_BASE", "https://models.inference.ai.azure.com")

# Create OpenAI client
client = OpenAI(api_key=api_key, base_url=api_base)

# Maximum number of consecutive tool calls to prevent infinite loops
MAX_CONSECUTIVE_TOOL_CALLS = 15


def send_to_openai(messages):
    """Sends the conversation history to OpenAI and returns the AI's response text."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # gpt-4o Input token limit 128,000 Output token limit 4096
            messages=messages,
            temperature=1,
            max_tokens=4096,
            top_p=1
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error with OpenAI API: {str(e)}")
        return f"I encountered an error: {str(e)}"


# Initialize conversation history with system instructions
conversation_history = [
    {
        "role": "system",
        "content": (system_prompt

                    )
    }
]


async def process_tool_calls(ai_response):
    """Process all tool calls in the AI response."""
    # Extract tool calls using the registry's method
    tool_calls = tool_registry.extract_tool_calls(ai_response)

    if not tool_calls:
        return False  # No tool calls found

    tool_results = []
    for i, (tool_type, tool_value) in enumerate(tool_calls):
        print(f"Executing tool call {i + 1}/{len(tool_calls)}: {tool_type} - {tool_value}")

        # Execute the tool using the registry
        result = await tool_registry.execute(tool_type, tool_value)

        # Log appropriate message based on tool type
        if tool_type == "app":
            print(f"Tool result: {result}")
        elif tool_type == "search":
            print(f"Search completed for: {tool_value}")
        else:
            print(f"Tool completed: {tool_type}")

        tool_results.append((tool_type, tool_value, result))

    # Add all tool results to conversation history
    for tool_type, tool_value, result in tool_results:
        # Use a consistent naming convention for tool types in the conversation
        type_name = "Application" if tool_type == "app" else "Search" if tool_type == "search" else tool_type.capitalize()
        conversation_history.append({
            "role": "system",
            "content": f"{type_name} tool execution result for '{tool_value}':\n\n{result}"
        })

    return True  # Tool calls were processed


async def conversation_loop():
    try:
        print("OpenAI Assistant initialized. Type 'exit' or 'quit' to end the conversation.")
        # print(f"Maximum consecutive tool calls: {MAX_CONSECUTIVE_TOOL_CALLS}")

        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting gracefully...")
                break

            # Append user's message to the conversation history
            conversation_history.append({"role": "user", "content": user_input})

            # Get AI response from OpenAI
            ai_response = send_to_openai(conversation_history)
            conversation_history.append({"role": "assistant", "content": ai_response})
            print("AI:", ai_response)

            # Process tool calls in a loop until no more tool calls or max limit reached
            tool_call_count = 0
            has_tool_calls = True

            while has_tool_calls and tool_call_count < MAX_CONSECUTIVE_TOOL_CALLS:
                has_tool_calls = await process_tool_calls(ai_response)

                if has_tool_calls:
                    tool_call_count += 1
                    # Get follow-up response from the AI after tool execution
                    ai_response = send_to_openai(conversation_history)
                    conversation_history.append({"role": "assistant", "content": ai_response})
                    print("AI:", ai_response)

            if tool_call_count == MAX_CONSECUTIVE_TOOL_CALLS:
                print(f"Reached maximum consecutive tool calls limit ({MAX_CONSECUTIVE_TOOL_CALLS})")

    except KeyboardInterrupt:
        print("\nGracefully exiting the conversation...")
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")


if __name__ == "__main__":
    asyncio.run(conversation_loop())