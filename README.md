# JOI
personal ai agent work in progress 


steps:-

clone the repo 

create a virtual enviorment (python -m venv .venv)

install the requirements.txt (pip install -r requirements.txt)

in the main dir i.e JOI\ create a .env file look at the dotenv_example file copy all and paste in .env

get all the api keys and paste them 

run the api_server.py file in a separate cmd window (python api_server.py)

api_server jason schema

ws://localhost:8000/ws/test_user

Start a New Chat
{
  "type": "start_chat",
  "model": "openai"
}

Start a New Chat:
Purpose: To initialize or reset the chat history for the current connection and optionally specify the AI model.
Structure:
{
  "type": "start_chat",
  "model": "<model_name_optional>" // e.g., "openai" or "gemini". Defaults to "openai" if not provided.
}

Handled by: handle_chat_start
Load an Existing Chat:
Purpose: To load a pre-existing chat history for the current connection, allowing the conversation to resume.
Structure:
{
  "type": "load_chat",
  "history": [ // Array of message objects
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}, // or "human" for Gemini
    {"role": "assistant", "content": "..."}
    // ... more messages
  ],
  "model": "<model_name_optional>" // e.g., "openai" or "gemini". Defaults to "openai" if not provided.
}

Handled by: handle_load_chat
Send User Message:
Purpose: To send the user's text input to the AI for processing.
Structure:
{
  "type": "user_message",
  "payload": "<user_text_string>",
  "model": "<model_name_optional>" // e.g., "openai" or "gemini". Can override the session's current model.
}

Handled by: handle_user_message
Messages Sent FROM the Server TO the Client:
These are identified by looking at await websocket.send_json({...}) calls throughout the server code.
Status Update:
Purpose: To inform the client about the status of an operation.
Structure:
{
  "type": "status",
  "payload": "<status_message_string>"
}

Examples of payload:
"New chat started with openai model"
"Loaded existing chat with gemini model"
"Processing tool call 1/3"
Error Message:
Purpose: To inform the client about an error that occurred.
Structure:
{
  "type": "error",
  "payload": "<error_message_string>"
}

Examples of payload:
"No chat history provided"
"Unknown message type: <received_type>"
"Server error: <description_of_error>"
AI Response Chunk (Streaming):
Purpose: To send parts of the AI's response as they are generated (for a streaming effect).
Structure:
{
  "type": "ai_chunk",
  "payload": "<chunk_of_ai_response_string>"
}

Tool Execution Status:
Purpose: To inform the client that a tool call is being executed.
Structure:
{
  "type": "tool_status",
  "payload": "<tool_status_message_string>" // e.g., "Executing tool call 1/2: fs_read"
}

Tool Execution Result:
Purpose: To send the result of an executed tool back to the client.
Structure:
{
  "type": "tool_result",
  "payload": {
    "tool": "<tool_name_string>",
    "args": { "<arg_key>": "<arg_value>", ... },
    "result": "<result_string_from_tool>"
  }
}

Warning Message:
Purpose: To inform the client about a non-critical issue or warning.
Structure:
{
  "type": "warning",
  "payload": "<warning_message_string>"
}

Example of payload:
"Reached maximum consecutive tool calls limit (15)"



