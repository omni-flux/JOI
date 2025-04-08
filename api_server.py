# --- START OF FILE api_server.py ---

import asyncio
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Literal, Tuple

# --- CHOOSE YOUR BACKEND ---
# Option 1: Use Gemini (requires gemini_chatbot.py structure)
from gemini_chatbot import (
    send_to_gemini,
    process_tool_calls,
    conversation_history, # IMPORTANT: Using global history for demo purposes ONLY
    MAX_CONSECUTIVE_TOOL_CALLS,
    tool_registry # Import tool_registry to extract calls for reporting
)
CHATBOT_SEND_FUNCTION = send_to_gemini
CHATBOT_PROCESS_TOOLS_FUNCTION = process_tool_calls
USER_ROLE = "user"
ASSISTANT_ROLE = "model"
TOOL_RESULT_ROLE = "function" # Gemini uses 'function' for results

# # Option 2: Use OpenAI (requires openai_chatbot.py structure)
# # Comment out Gemini imports above and uncomment these
# from openai_chatbot import (
#     send_to_openai,
#     process_tool_calls,
#     conversation_history, # IMPORTANT: Using global history for demo purposes ONLY
#     MAX_CONSECUTIVE_TOOL_CALLS,
#     tool_registry # Import tool_registry to extract calls for reporting
# )
# CHATBOT_SEND_FUNCTION = send_to_openai
# CHATBOT_PROCESS_TOOLS_FUNCTION = process_tool_calls
# USER_ROLE = "user"
# ASSISTANT_ROLE = "assistant"
# TOOL_RESULT_ROLE = "system" # OpenAI example used 'system' for simulated results
# --- END BACKEND CHOICE ---


# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Pydantic Models for API ---

class UserInput(BaseModel):
    message: str

class ChatStep(BaseModel):
    role: Literal["user", "assistant", "tool_execution", "tool_result", "error", "system_info"]
    content: str

# --- FastAPI App ---
app = FastAPI(
    title="Simple Chatbot API",
    description="Wraps the console chatbot logic. WARNING: Uses global state, suitable for single-user demo ONLY.",
)

# --- WARNING ABOUT GLOBAL STATE ---
# This API uses a global list `conversation_history` imported from the chatbot module.
# This makes the API STATEFUL and UNSUITABLE for concurrent users.
# Every call modifies the same history list. Restart the API server to clear history.
# This is purely for demonstrating the chatbot logic via a UI quickly.
# -----------------------------------

@app.post("/chat", response_model=List[ChatStep])
async def handle_chat_turn(user_input: UserInput):
    """
    Handles one turn of the conversation:
    1. Takes user input.
    2. Gets AI response.
    3. Processes tools if requested by AI.
    4. Gets final AI response if tools were used.
    5. Returns all steps in the turn.
    """
    turn_steps: List[ChatStep] = []
    original_history_len = len(conversation_history) # Track history additions this turn

    try:
        logger.info(f"Received message: {user_input.message}")
        if not user_input.message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        # 1. Add user message to history and steps
        conversation_history.append({"role": USER_ROLE, "content": user_input.message})
        turn_steps.append(ChatStep(role="user", content=user_input.message))

        consecutive_tool_calls = 0
        while consecutive_tool_calls < MAX_CONSECUTIVE_TOOL_CALLS:
            # Store length before potential tool results are added
            history_len_before_tool_results = len(conversation_history)

            # 2. Get AI response
            logger.info("Sending current history to LLM...")
            ai_response_text = CHATBOT_SEND_FUNCTION(conversation_history)
            logger.info(f"LLM Raw Response: {ai_response_text[:100]}...") # Log snippet

            # Add AI response to history and steps
            conversation_history.append({"role": ASSISTANT_ROLE, "content": ai_response_text})
            turn_steps.append(ChatStep(role="assistant", content=ai_response_text))

            # 3. Check for and process tool calls
            # Extract calls just to report them *before* execution
            try:
                tool_calls_extracted: List[Tuple[str, str]] = tool_registry.extract_tool_calls(ai_response_text)
            except Exception as e:
                logger.error(f"Error extracting tool calls: {e}", exc_info=True)
                tool_calls_extracted = [] # Proceed without tool calls if extraction fails

            if tool_calls_extracted:
                logger.info(f"AI requested {len(tool_calls_extracted)} tool call(s).")
                # Report planned execution to UI
                exec_details = [f"{name}({arg[:30]}...)" for name, arg in tool_calls_extracted]
                turn_steps.append(ChatStep(role="tool_execution", content=f"Executing Tools: {', '.join(exec_details)}"))

                # Execute tools (this function modifies conversation_history directly)
                try:
                    # Pass only the latest AI response text for tool processing
                    await CHATBOT_PROCESS_TOOLS_FUNCTION(ai_response_text)
                    tools_processed = True # Assume success if no exception
                except Exception as tool_exec_err:
                    logger.error(f"Error during tool processing: {tool_exec_err}", exc_info=True)
                    error_msg = f"Failed to execute tools: {tool_exec_err}"
                    turn_steps.append(ChatStep(role="error", content=error_msg))
                    # Optionally add error to history? Decided against it for now to avoid confusing the LLM further.
                    tools_processed = False
                    break # Stop processing this turn on tool error

                # Report tool results (read from history additions)
                if tools_processed:
                    new_history_items = conversation_history[history_len_before_tool_results:]
                    tool_result_items = [item for item in new_history_items if item["role"] == TOOL_RESULT_ROLE]

                    if not tool_result_items and tool_calls_extracted:
                         # This case might happen if process_tool_calls fails silently or has issues
                         logger.warning("Tools were extracted, but no results found in history after processing.")
                         turn_steps.append(ChatStep(role="tool_result", content="Tool execution attempted, but no results recorded in history."))

                    for result_item in tool_result_items:
                        turn_steps.append(ChatStep(role="tool_result", content=result_item["content"]))

                    consecutive_tool_calls += 1
                    # Loop back to get AI response based on tool results

            else:
                # No tool calls requested in the latest AI response
                logger.info("No tool calls requested by AI.")
                break # Exit the tool processing loop

        if consecutive_tool_calls == MAX_CONSECUTIVE_TOOL_CALLS:
            logger.warning("Reached maximum consecutive tool calls.")
            info_msg = f"System Note: Reached maximum consecutive tool calls ({MAX_CONSECUTIVE_TOOL_CALLS})."
            turn_steps.append(ChatStep(role="system_info", content=info_msg))
            # Optionally add to history for the LLM to see
            conversation_history.append({"role": USER_ROLE, "content": info_msg}) # Use USER_ROLE to inject system notes sometimes

        logger.info(f"Turn completed. Returning {len(turn_steps)} steps.")
        return turn_steps

    except HTTPException:
        # Re-raise HTTP exceptions directly
        raise
    except Exception as e:
        logger.exception("An unexpected error occurred in /chat endpoint.") # Log full traceback
        # Return the steps accumulated so far plus the error
        turn_steps.append(ChatStep(role="error", content=f"An unexpected server error occurred: {str(e)}"))
        # Don't raise HTTPException here if we want to return partial steps + error message
        # Instead, return the list containing the error step
        return turn_steps
        # Alternatively, re-raise as a 500 error:
        # raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {str(e)}")

@app.get("/history", response_model=List[dict])
async def get_history():
    """Returns the current global conversation history (for debugging)."""
    # WARNING: Exposes the entire internal state. Use with caution.
    return conversation_history

@app.delete("/history", status_code=204)
async def clear_history():
    """Clears the global conversation history."""
    logger.info("Clearing global conversation history.")
    conversation_history.clear()
    # Re-add system prompt if necessary (depends on chatbot implementation)
    # For Gemini (using system_instruction in model init), clearing might be enough.
    # For OpenAI, you might need to re-add the system message:
    # from prompts import system_prompt
    # conversation_history.append({"role": "system", "content": system_prompt})
    return None # Return No Content

# --- Run the server (for local testing) ---
if __name__ == "__main__":
    import uvicorn
    print("\n--- WARNING ---")
    print("This API server uses global state for conversation history.")
    print("It is suitable for SINGLE-USER DEMO purposes ONLY.")
    print("Restart the server to clear history if needed, or use the DELETE /history endpoint.")
    print("---------------\n")
    # Ensure host is 0.0.0.0 to be accessible on your network if needed
    # Default is 127.0.0.1 (localhost)
    uvicorn.run(app, host="127.0.0.1", port=8000)

# --- END OF FILE api_server.py ---