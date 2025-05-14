import os
import uuid
from typing import Dict, List, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import logging
from openai import OpenAI
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import tools registry
from tools import tool_registry

# Import core functions from chatbot implementations
from openai_chatbot import send_to_openai
from gemini_chatbot import send_to_gemini

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("api_server")

# Configure APIs
try:
    openai_api_key = os.environ["OPENAI_API_KEY"]
    openai_api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    openai_client = OpenAI(api_key=openai_api_key, base_url=openai_api_base)
    logger.info("OpenAI client initialized successfully")
except KeyError:
    logger.warning("OpenAI API key not found in environment variables")
    openai_client = None

try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    logger.info("Google Gemini API initialized successfully")
except KeyError:
    logger.warning("Gemini API key not found in environment variables")

# Maximum number of consecutive tool calls to prevent infinite loops
MAX_CONSECUTIVE_TOOL_CALLS = 15

# Create FastAPI app
app = FastAPI(title="JOI - AI Assistant API")

# Add CORS middleware - update with your frontend URL in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins in development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global chat history - reset for each new chat
# Structure: {connection_id: {"history": [...], "model_type": str}}
active_connections: Dict[str, Dict[str, Any]] = {}

# Import system prompt
from prompts import system_prompt


# Message handlers for WebSocket
async def handle_chat_start(websocket: WebSocket, connection_id: str, data: Dict[str, Any]):
    """Handle request to start a new chat (reset history)"""
    model_type = data.get("model", "openai")  # Default to OpenAI if not specified

    # Initialize with system prompt and appropriate role format
    if model_type == "openai":
        active_connections[connection_id] = {
            "history": [{"role": "system", "content": system_prompt}],
            "model_type": "openai"
        }
    else:  # gemini
        active_connections[connection_id] = {
            "history": [{"role": "system", "content": system_prompt}],
            "model_type": "gemini"
        }

    await websocket.send_json({
        "type": "status",
        "payload": f"New chat started with {model_type} model"
    })
    logger.info(f"New chat started for connection {connection_id} using {model_type} model")


async def handle_load_chat(websocket: WebSocket, connection_id: str, data: Dict[str, Any]):
    """Handle loading an existing chat history"""
    chat_history = data.get("history", [])
    model_type = data.get("model", "openai")

    if not chat_history:
        await websocket.send_json({
            "type": "error",
            "payload": "No chat history provided"
        })
        return

    # Ensure system prompt is present
    if not any(msg.get("role") == "system" for msg in chat_history):
        if model_type == "openai":
            chat_history.insert(0, {"role": "system", "content": system_prompt})
        else:  # gemini
            chat_history.insert(0, {"role": "system", "content": system_prompt})

    active_connections[connection_id] = {
        "history": chat_history,
        "model_type": model_type
    }

    await websocket.send_json({
        "type": "status",
        "payload": f"Loaded existing chat with {model_type} model"
    })
    logger.info(f"Loaded existing chat for connection {connection_id} using {model_type} model")


async def handle_user_message(websocket: WebSocket, connection_id: str, data: Dict[str, Any]):
    """Process a user message and get AI response"""
    user_message = data.get("payload", "")
    model_override = data.get("model")  # Optional model override

    # Ensure connection exists
    if connection_id not in active_connections:
        # Create new connection with default model if not specified
        model_type = model_override or "openai"
        active_connections[connection_id] = {
            "history": [{"role": "system", "content": system_prompt}],
            "model_type": model_type
        }

    # Get current connection data
    connection_data = active_connections[connection_id]
    history = connection_data["history"]
    model_type = model_override or connection_data["model_type"]

    # Update model type if overridden
    if model_override:
        connection_data["model_type"] = model_override

    # Add user message to history with appropriate role
    if model_type == "openai":
        history.append({"role": "user", "content": user_message})
    else:  # gemini
        history.append({"role": "human", "content": user_message})

    # Process message with appropriate model
    await process_message_with_model(websocket, connection_id, model_type)


async def process_message_with_model(websocket: WebSocket, connection_id: str, model_type: str):
    """Process a message with the specified model and handle tool calls"""
    connection_data = active_connections[connection_id]
    history = connection_data["history"]

    # Process AI response and potential tool calls in a loop
    tool_call_count = 0
    has_tool_calls = True

    while has_tool_calls and tool_call_count < MAX_CONSECUTIVE_TOOL_CALLS:
        # Get AI response based on model type
        accumulated_response = ""

        if model_type == "openai":
            # Convert history format if needed (from gemini to openai)
            openai_messages = []
            for msg in history:
                role = msg["role"]
                if role == "human":
                    role = "user"
                elif role == "tool":
                    role = "system"  # Tool results go in system messages
                openai_messages.append({"role": role, "content": msg["content"]})

            # Stream response chunks to client
            for chunk, accumulated in send_to_openai(openai_messages, stream=True):
                accumulated_response = accumulated
                await websocket.send_json({
                    "type": "ai_chunk",
                    "payload": chunk
                })

            # Add response to history
            history.append({"role": "assistant", "content": accumulated_response})

            # Process tool calls
            has_tool_calls = await process_openai_tool_calls_for_websocket(
                websocket,
                accumulated_response,
                history
            )

        else:  # gemini
            # Stream response chunks to client
            for chunk, accumulated in send_to_gemini(history, stream=True):
                accumulated_response = accumulated
                await websocket.send_json({
                    "type": "ai_chunk",
                    "payload": chunk
                })

            # Add response to history
            history.append({"role": "assistant", "content": accumulated_response})

            # Process tool calls
            has_tool_calls = await process_gemini_tool_calls_for_websocket(
                websocket,
                accumulated_response,
                history
            )

        if has_tool_calls:
            tool_call_count += 1
            # Send status update to client
            await websocket.send_json({
                "type": "status",
                "payload": f"Processing tool call {tool_call_count}/{MAX_CONSECUTIVE_TOOL_CALLS}"
            })

    if tool_call_count == MAX_CONSECUTIVE_TOOL_CALLS:
        await websocket.send_json({
            "type": "warning",
            "payload": f"Reached maximum consecutive tool calls limit ({MAX_CONSECUTIVE_TOOL_CALLS})"
        })


async def process_openai_tool_calls_for_websocket(
        websocket: WebSocket,
        ai_response: str,
        history: List[Dict[str, str]]
) -> bool:
    """Adapter for OpenAI tool call processing that works with WebSockets"""
    # Extract tool calls
    tool_calls = tool_registry.extract_tool_calls(ai_response)

    if not tool_calls:
        return False  # No tool calls found

    tool_results = []
    for i, (tool_type, tool_value) in enumerate(tool_calls):
        # Send status to client
        await websocket.send_json({
            "type": "tool_status",
            "payload": f"Executing tool call {i + 1}/{len(tool_calls)}: {tool_type}"
        })

        # Execute the tool
        result = await tool_registry.execute(tool_type, tool_value)

        # Send result to client
        await websocket.send_json({
            "type": "tool_result",
            "payload": {
                "tool": tool_type,
                "args": tool_value,
                "result": result
            }
        })

        tool_results.append((tool_type, tool_value, result))

    # Add all tool results to conversation history
    for tool_type, tool_value, result in tool_results:
        # Use a consistent naming convention for tool types
        type_name = "Application" if tool_type == "app" else "Search" if tool_type == "search" else tool_type.capitalize()
        history.append({
            "role": "system",
            "content": f"{type_name} tool execution result for '{tool_value}':\n\n{result}"
        })

    return True  # Tool calls were processed


async def process_gemini_tool_calls_for_websocket(
        websocket: WebSocket,
        ai_response: str,
        history: List[Dict[str, str]]
) -> bool:
    """Adapter for Gemini tool call processing that works with WebSockets"""
    # Extract tool calls
    tool_calls = tool_registry.extract_tool_calls(ai_response)

    if not tool_calls:
        return False  # No tool calls found

    tool_results = []
    for i, (tool_type, tool_value) in enumerate(tool_calls):
        # Send status to client
        await websocket.send_json({
            "type": "tool_status",
            "payload": f"Executing tool call {i + 1}/{len(tool_calls)}: {tool_type}"
        })

        # Execute the tool
        result = await tool_registry.execute(tool_type, tool_value)

        # Send result to client
        await websocket.send_json({
            "type": "tool_result",
            "payload": {
                "tool": tool_type,
                "args": tool_value,
                "result": result
            }
        })

        tool_results.append((tool_type, tool_value, result))

    # Add all tool results to conversation history
    for tool_type, tool_value, result in tool_results:
        # Use a consistent naming convention for tool types
        type_name = "Application" if tool_type == "app" else "Search" if tool_type == "search" else tool_type.capitalize()
        history.append({
            "role": "tool",
            "content": f"{type_name} tool execution result for '{tool_value}':\n\n{result}"
        })

    return True  # Tool calls were processed


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time chat"""
    await websocket.accept()

    # Generate a unique connection ID for this session
    connection_id = f"{client_id}_{uuid.uuid4().hex[:8]}"

    try:
        logger.info(f"New WebSocket connection: {connection_id}")

        # Main message handling loop
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            message_type = data.get("type", "")

            # Process message based on type
            if message_type == "start_chat":
                await handle_chat_start(websocket, connection_id, data)
            elif message_type == "load_chat":
                await handle_load_chat(websocket, connection_id, data)
            elif message_type == "user_message":
                await handle_user_message(websocket, connection_id, data)
            else:
                await websocket.send_json({
                    "type": "error",
                    "payload": f"Unknown message type: {message_type}"
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {connection_id}")
        # Clean up connection data
        if connection_id in active_connections:
            del active_connections[connection_id]

    except Exception as e:
        logger.error(f"Error in WebSocket connection {connection_id}: {str(e)}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "payload": f"Server error: {str(e)}"
            })
        except:
            pass  # Connection might be closed already


# Add a simple health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "models": {
        "openai": openai_client is not None,
        "gemini": "GEMINI_API_KEY" in os.environ
    }}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)