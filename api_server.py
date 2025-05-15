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
# These will be used by the shared WebSocket handler
from openai_chatbot import send_to_openai
from gemini_chatbot import send_to_gemini

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("api_server")

# --- Hardcoded list for ALLOWED_CLIENT_IDS ---
ALLOWED_CLIENT_IDS: List[str] = ["frontend_main", "test_client_1","test_user","123"]  # Add your predefined IDs here
if not ALLOWED_CLIENT_IDS:
    logger.warning("ALLOWED_CLIENT_IDS is empty. All WebSocket connections will be rejected if this is unintentional.")
# --- End of ALLOWED_CLIENT_IDS ---


# Configure APIs
try:
    openai_api_key = os.environ["OPENAI_API_KEY"]
    openai_api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    openai_client = OpenAI(api_key=openai_api_key, base_url=openai_api_base)
    logger.info("OpenAI client initialized successfully")
except KeyError:
    logger.warning("OpenAI API key not found in environment variables. OpenAI endpoint will not function correctly.")
    openai_client = None

try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    logger.info("Google Gemini API initialized successfully")
except KeyError:
    logger.warning("Gemini API key not found in environment variables. Gemini endpoint will not function correctly.")
    # genai will raise errors if not configured, so no need to set it to None explicitly

# Maximum number of consecutive tool calls to prevent infinite loops
MAX_CONSECUTIVE_TOOL_CALLS = 15

# Create FastAPI app
app = FastAPI(title="JOI - AI Assistant API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global chat history (will be replaced by DB later)
active_connections: Dict[str, Dict[str, Any]] = {}

# Import system prompt
from prompts import system_prompt


async def handle_chat_start_internal(connection_id: str, data: Dict[str, Any], default_model_type: str):
    """Internal handler for starting a new chat."""
    model_type = data.get("model", default_model_type)  # Use endpoint's model as default

    # Ensure consistency if data.get("model") is present and different from endpoint's default
    if data.get("model") and data.get("model") != default_model_type:
        logger.warning(
            f"Connection {connection_id}: Chat start requested model '{data.get('model')}' which differs from endpoint default '{default_model_type}'. Using '{model_type}'.")
    else:
        model_type = default_model_type  # Strictly use the endpoint's model

    active_connections[connection_id] = {
        "history": [{"role": "system", "content": system_prompt}],
        "model_type": model_type  # Store the determined model_type
    }
    return model_type


async def handle_load_chat_internal(connection_id: str, data: Dict[str, Any], default_model_type: str):
    """Internal handler for loading chat history."""
    chat_history = data.get("history", [])
    # Similar to handle_chat_start, model_type can be influenced by endpoint or data
    model_type = data.get("model", default_model_type)

    if data.get("model") and data.get("model") != default_model_type:
        logger.warning(
            f"Connection {connection_id}: Load chat requested model '{data.get('model')}' which differs from endpoint default '{default_model_type}'. Using '{model_type}'.")
    else:
        model_type = default_model_type

    if not chat_history:
        return None, "No chat history provided"  # Return error message

    if not any(msg.get("role") == "system" for msg in chat_history):
        chat_history.insert(0, {"role": "system", "content": system_prompt})

    active_connections[connection_id] = {
        "history": chat_history,
        "model_type": model_type
    }
    return model_type, None  # Return model_type and no error


async def process_user_message_internal(connection_id: str, data: Dict[str, Any], default_model_type: str):
    """Internal handler for processing a user message."""
    user_message = data.get("payload", "")
    message_model_override = data.get("model")

    if connection_id not in active_connections:
        logger.warning(
            f"Connection {connection_id}: Received user_message without prior chat_start/load_chat. Initializing with default model: {default_model_type}")
        active_connections[connection_id] = {
            "history": [{"role": "system", "content": system_prompt}],
            "model_type": default_model_type
        }

    connection_data = active_connections[connection_id]
    history = connection_data["history"]

    # Determine the model_type for this specific message processing turn
    current_turn_model_type = default_model_type
    if message_model_override and message_model_override != default_model_type:
        logger.info(
            f"Connection {connection_id}: User message specified model override to '{message_model_override}'. Using it for this turn.")
        current_turn_model_type = message_model_override
        connection_data["model_type"] = current_turn_model_type  # Update active connection's model if overridden
    elif connection_data.get("model_type") != default_model_type:
        current_turn_model_type = connection_data["model_type"]

    # Add user message to history with role appropriate for the *current_turn_model_type*
    if current_turn_model_type == "openai":
        history.append({"role": "user", "content": user_message})
    else:  # gemini
        history.append({"role": "human", "content": user_message})

    return current_turn_model_type  # Return the model to be used for LLM call


async def process_message_with_model_internal(websocket: WebSocket, connection_id: str, model_to_use: str):
    """Shared logic to process message with LLM and handle tools."""
    connection_data = active_connections[connection_id]
    history = connection_data["history"]  # This history is already updated by process_user_message_internal

    tool_call_count = 0
    has_tool_calls = True  # Start assuming there might be tool calls

    while has_tool_calls and tool_call_count < MAX_CONSECUTIVE_TOOL_CALLS:
        accumulated_response = ""

        # Prepare messages for the LLM call, ensuring correct roles
        llm_messages = []
        if model_to_use == "openai":
            for msg in history:
                role = msg["role"]
                if role == "human":
                    role = "user"
                elif role == "model":
                    role = "assistant"  # Gemini's 'model' is OpenAI's 'assistant'
                elif role == "tool":
                    role = "system"  # Your current adaptation for OpenAI
                llm_messages.append({"role": role, "content": msg["content"]})
        else:  # gemini
            for msg in history:
                role = msg["role"]
                if role == "user":
                    role = "human"
                elif role == "assistant":
                    role = "model"  # OpenAI's 'assistant' is Gemini's 'model'
                # 'tool' role is fine for Gemini
                llm_messages.append({"role": role, "content": msg["content"]})

        # Call the appropriate LLM
        if model_to_use == "openai":
            for chunk, acc_resp in send_to_openai(llm_messages, stream=True):
                accumulated_response = acc_resp
                await websocket.send_json({"type": "ai_chunk", "payload": chunk})
            await websocket.send_json({"type": "stream_end", "payload": "OpenAI response turn ended."})
            history.append({"role": "assistant", "content": accumulated_response})
            has_tool_calls = await process_openai_tool_calls_for_websocket(websocket, accumulated_response, history)

        else:  # gemini
            for chunk, acc_resp in send_to_gemini(llm_messages, stream=True):
                accumulated_response = acc_resp
                await websocket.send_json({"type": "ai_chunk", "payload": chunk})
            await websocket.send_json({"type": "stream_end", "payload": "Gemini response turn ended."})
            history.append({"role": "model", "content": accumulated_response})  # Gemini uses 'model' for its responses
            has_tool_calls = await process_gemini_tool_calls_for_websocket(websocket, accumulated_response, history)

        if has_tool_calls:
            tool_call_count += 1
            await websocket.send_json({
                "type": "status",
                "payload": f"Processing tool call {tool_call_count}/{MAX_CONSECUTIVE_TOOL_CALLS}"
            })
        else:
            break

    if tool_call_count == MAX_CONSECUTIVE_TOOL_CALLS:
        await websocket.send_json({
            "type": "warning",
            "payload": f"Reached maximum consecutive tool calls limit ({MAX_CONSECUTIVE_TOOL_CALLS})"
        })

async def process_openai_tool_calls_for_websocket(
        websocket: WebSocket, ai_response: str, history: List[Dict[str, str]]
) -> bool:
    tool_calls = tool_registry.extract_tool_calls(ai_response)
    if not tool_calls: return False
    for i, (tool_type, tool_value) in enumerate(tool_calls):
        await websocket.send_json(
            {"type": "tool_status", "payload": f"Executing tool call {i + 1}/{len(tool_calls)}: {tool_type}"})
        result = await tool_registry.execute(tool_type, tool_value)
        await websocket.send_json(
            {"type": "tool_result", "payload": {"tool": tool_type, "args": tool_value, "result": result}})
        type_name = tool_type.capitalize()
        history.append(
            {"role": "system", "content": f"{type_name} tool execution result for '{tool_value}':\n\n{result}"})
    return True


async def process_gemini_tool_calls_for_websocket(
        websocket: WebSocket, ai_response: str, history: List[Dict[str, str]]
) -> bool:
    tool_calls = tool_registry.extract_tool_calls(ai_response)
    if not tool_calls: return False
    for i, (tool_type, tool_value) in enumerate(tool_calls):
        await websocket.send_json(
            {"type": "tool_status", "payload": f"Executing tool call {i + 1}/{len(tool_calls)}: {tool_type}"})
        result = await tool_registry.execute(tool_type, tool_value)
        await websocket.send_json(
            {"type": "tool_result", "payload": {"tool": tool_type, "args": tool_value, "result": result}})
        type_name = tool_type.capitalize()
        history.append({"role": "tool",
                        "content": f"{type_name} tool execution result for '{tool_value}':\n\n{result}"})  # Gemini uses 'tool' role
    return True


async def common_websocket_handler(websocket: WebSocket, client_id: str, default_model_type: str):
    """The shared handler for all WebSocket connections."""
    if client_id not in ALLOWED_CLIENT_IDS:
        logger.warning(
            f"Rejected WebSocket connection for model {default_model_type} from unauthorized client_id: '{client_id}'. Allowed: {ALLOWED_CLIENT_IDS}")
        await websocket.close(code=1008, reason="Unauthorized client_id")
        return

    await websocket.accept()
    connection_id_suffix = uuid.uuid4().hex[:8]
    # connection_id is unique for THIS WebSocket instance
    connection_id = f"{client_id}_{default_model_type}_{connection_id_suffix}"

    try:
        logger.info(
            f"New WebSocket ({default_model_type}) connection accepted: {connection_id} (from client_id: {client_id})")

        active_model_type = await handle_chat_start_internal(connection_id, {}, default_model_type)
        await websocket.send_json({
            "type": "status",
            "payload": f"Chat started with {active_model_type} model via dedicated endpoint."
        })

        while True:
            data = await websocket.receive_json()
            message_type = data.get("type", "")

            if message_type == "user_message":
                model_to_use_for_llm = await process_user_message_internal(connection_id, data, default_model_type)
                await process_message_with_model_internal(websocket, connection_id, model_to_use_for_llm)

            elif message_type == "load_chat":
                loaded_model_type, error_msg = await handle_load_chat_internal(connection_id, data, default_model_type)
                if error_msg:
                    await websocket.send_json({"type": "error", "payload": error_msg})
                else:
                    active_model_type = loaded_model_type
                    await websocket.send_json({
                        "type": "status",
                        "payload": f"Loaded existing chat with {active_model_type} model"
                    })

            elif message_type == "start_chat":
                active_model_type = await handle_chat_start_internal(connection_id, data, default_model_type)
                await websocket.send_json({
                    "type": "status",
                    "payload": f"New chat explicitly started/reset with {active_model_type} model."
                })

            else:
                await websocket.send_json({"type": "error", "payload": f"Unknown message type: {message_type}"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket ({default_model_type}) disconnected: {connection_id}")
    except Exception as e:
        logger.error(f"Error in WebSocket ({default_model_type}) connection {connection_id}: {str(e)}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "payload": f"Server error: {str(e)}"})
        except:
            pass  # Connection might be closed
    finally:
        if connection_id in active_connections:
            logger.info(f"Cleaning up active connection for {connection_id}")
            del active_connections[connection_id]


# --- Define separate WebSocket endpoints ---
@app.websocket("/ws/openai/{client_id}")
async def websocket_openai_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint specifically for OpenAI."""
    if not openai_client:  # Check if OpenAI client was initialized
        logger.error("OpenAI client not available. Rejecting WebSocket connection to /ws/openai/.")
        await websocket.close(code=1011, reason="OpenAI service not configured on server.")  # Internal server error
        return
    await common_websocket_handler(websocket, client_id, default_model_type="openai")


@app.websocket("/ws/gemini/{client_id}")
async def websocket_gemini_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint specifically for Gemini."""
    if "GEMINI_API_KEY" not in os.environ:  # Basic check
        logger.error("Gemini API key not configured. Rejecting WebSocket connection to /ws/gemini/.")
        await websocket.close(code=1011, reason="Gemini service not configured on server.")
        return
    await common_websocket_handler(websocket, client_id, default_model_type="gemini")


@app.get("/health")
async def health_check():
    return {"status": "ok", "models": {
        "openai_configured": openai_client is not None,
        "gemini_configured": "GEMINI_API_KEY" in os.environ
    }}


if __name__ == "__main__":
    import uvicorn
    if not ALLOWED_CLIENT_IDS:
        print("CRITICAL: ALLOWED_CLIENT_IDS is not set or is empty. WebSocket connections will be rejected.")
    logger.info(f"Starting server. Allowed client IDs for WebSocket: {ALLOWED_CLIENT_IDS}")
    uvicorn.run(app, host="0.0.0.0", port=8000)