import os
import json
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv
from prompts import system_prompt

# Load environment variables
load_dotenv()
# Import the  tools
from tools import tool_registry

try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
except KeyError:
    print("Error: GEMINI_API_KEY environment variable not found")
    print("Make sure you have a .env file with GEMINI_API_KEY=your_api_key")
    exit(1)

# Maximum number of consecutive tool calls to prevent infinite loops
MAX_CONSECUTIVE_TOOL_CALLS = 15

# Gemini model configuration
generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

try:
    model = genai.GenerativeModel(
        model_name="gemini-2.0-pro-exp-02-05",# gemini-2.0-pro-exp-02-05 Input token limit 2,048,576 Output token limit 8,192
        generation_config=generation_config,
    )
    chat_session = model.start_chat(history=[])
except Exception as e:
    print(f"Error initializing Gemini model: {str(e)}")
    print("Try using a different model name like 'gemini-1.5-pro' if the specified model isn't available")
    exit(1)

# Initialize conversation history with a system instruction
conversation_history = [
    {
        "role": "system",
        "content": (system_prompt
                    )
    }
]


def send_to_gemini(history, stream=True):
    """
    Sends the conversation history to Gemini as a JSON payload and returns the AI's response text.
    When stream=True, yields chunks of the response as they arrive.
    """
    try:
        payload = json.dumps(history)

        if stream:
            # For streaming response
            accumulated_response = ""
            for chunk in chat_session.send_message(payload, stream=True):
                if chunk.text:
                    accumulated_response += chunk.text
                    yield chunk.text, accumulated_response
            return accumulated_response
        else:
            # For non-streaming response (original behavior)
            response = chat_session.send_message(payload)
            return response.text
    except Exception as e:
        error_message = f"Error communicating with Gemini API: {str(e)}"
        print(error_message)
        return error_message


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
            "role": "tool",
            "content": f"{type_name} tool execution result for '{tool_value}':\n\n{result}"
        })

    return True  # Tool calls were processed


async def conversation_loop():
    try:
        print("Gemini Assistant initialized. Type 'exit' or 'quit' to end the conversation.")

        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting gracefully...")
                break

            # Append user's message to the conversation history
            conversation_history.append({"role": "human", "content": user_input})

            # Get AI response from Gemini with streaming
            accumulated_response = ""
            print("AI: ", end="", flush=True)

            for chunk, accumulated in send_to_gemini(conversation_history, stream=True):
                print(chunk, end="", flush=True)
                accumulated_response = accumulated

            print()  # New line after streaming completes

            # Add the complete response to conversation history
            conversation_history.append({"role": "assistant", "content": accumulated_response})

            # Process tool calls in a loop until no more tool calls or max limit reached
            tool_call_count = 0
            has_tool_calls = True

            while has_tool_calls and tool_call_count < MAX_CONSECUTIVE_TOOL_CALLS:
                has_tool_calls = await process_tool_calls(accumulated_response)

                if has_tool_calls:
                    tool_call_count += 1
                    # Get follow-up response from the AI after tool execution with streaming
                    accumulated_response = ""
                    print("AI: ", end="", flush=True)

                    for chunk, accumulated in send_to_gemini(conversation_history, stream=True):
                        print(chunk, end="", flush=True)
                        accumulated_response = accumulated

                    print()  # New line after streaming completes

                    conversation_history.append({"role": "assistant", "content": accumulated_response})

            if tool_call_count == MAX_CONSECUTIVE_TOOL_CALLS:
                print(f"Reached maximum consecutive tool calls limit ({MAX_CONSECUTIVE_TOOL_CALLS})")

    except KeyboardInterrupt:
        print("\nGracefully exiting the conversation...")
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")


if __name__ == "__main__":
    asyncio.run(conversation_loop())