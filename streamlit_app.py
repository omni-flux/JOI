# In your streamlit_app.py (requires pip install streamlit requests)
import streamlit as st
import requests
import json

API_URL = "http://127.0.0.1:8000/chat"  # Make sure api_server.py is running

# Set page config for dark theme
st.set_page_config(page_title="AI Assistant", page_icon="🤖", layout="centered")

# Apply custom CSS for dark theme and better styling
st.markdown("""
<style>
    /* Dark theme base */
    .stApp {
        background-color: #121212;
        color: #f0f0f0;
    }

    /* Title styling */
    h1 {
        color: white;
        text-align: center;
        font-weight: 600;
        margin-bottom: 30px;
    }

    /* Chat message styling */
    .stChatMessage {
        background-color: #1e1e1e;
        border-radius: 10px;
        padding: 10px;
        margin: 5px 0;
    }

    /* User message styling */
    [data-testid="stChatMessageContent"] {
        background-color: transparent !important;
    }

    /* User message bubble styling */
    .user-message {
        background-color: #333333;
        border-radius: 15px;
        padding: 8px 12px;
        display: inline-block;
        margin-left: auto;
        margin-right: 0;
        float: right;
        clear: both;
    }

    /* Input container styling */
    .stChatInputContainer {
        background-color: #1e1e1e;
        border-radius: 20px;
        padding: 5px;
        border: 1px solid #333;
    }

    /* Tool execution styling */
    .tool-execution {
        background-color: #1a3a5a !important;
        border-radius: 8px;
        padding: 10px;
        margin: 5px 0;
        border-left: 4px solid #4a90e2;
    }

    /* Code block styling */
    pre {
        background-color: #1e2430 !important;
        border-radius: 5px;
        padding: 10px;
        border-left: 3px solid #3a506b;
    }

    code {
        color: #a9b7c6;
    }

    /* Syntax highlighting */
    .python-keyword {
        color: #cc7832;
    }

    .python-string {
        color: #6a8759;
    }

    .python-function {
        color: #ffc66d;
    }

    /* Avatar styling */
    .user-avatar {
        background-color: #e74c3c !important;
        color: white;
    }

    .assistant-avatar {
        background-color: #f39c12 !important;
        color: white;
    }

    /* Hide Streamlit branding */
    #MainMenu, footer, header {
        visibility: hidden;
    }

    /* Custom scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }

    ::-webkit-scrollbar-track {
        background: #1e1e1e;
    }

    ::-webkit-scrollbar-thumb {
        background: #555;
        border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb:hover {
        background: #777;
    }

    /* Send button styling */
    .stButton > button {
        background-color: #4a90e2;
        color: white;
        border-radius: 20px;
    }

    /* Expander styling */
    .st-expander {
        background-color: #1e1e1e !important;
        border-radius: 8px;
        border: none !important;
    }

    .st-emotion-cache-1wmy9hl {
        background-color: #1e1e1e !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("How may i help you today? 🤖")

# Initialize chat history in Streamlit session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages from history
for message in st.session_state.messages:
    role = message["role"]
    content = message["content"]

    # Skip tool execution and results for history display - we'll handle them specially
    if role in ["tool_execution", "tool_result"]:
        continue

    # Display user and assistant messages
    with st.chat_message(role, avatar="😊" if role == "user" else "🤖"):
        st.markdown(content)

# Accept user input
if prompt := st.chat_input("How can I help you today?"):
    # Add user message to local Streamlit history
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Display user message (only once)
    with st.chat_message("user", avatar="😊"):
        st.markdown(prompt)

    # Create a placeholder for the assistant's response
    with st.spinner("Thinking..."):
        try:
            response = requests.post(API_URL, json={"message": prompt})
            response.raise_for_status()
            backend_steps = response.json()

            # Process and display each step from the backend
            for step in backend_steps:
                display_role = step.get("role", "assistant")
                content = step.get("content", "")

                if display_role == "tool_execution":
                    # Display tool execution with special styling
                    with st.chat_message("assistant", avatar="🤖"):
                        st.markdown(f"""
                        <div class="tool-execution">
                            <span style="color: #4a90e2; font-weight: bold;">🛠️ Executing Tool:</span> {content}
                        </div>
                        """, unsafe_allow_html=True)

                elif display_role == "tool_result":
                    # Only display tool result if it's not the "no results" message
                    if "no results recorded in history" not in content:
                        with st.chat_message("assistant", avatar="🤖"):
                            with st.expander("🛠️ Tool Result", expanded=False):
                                st.code(content)

                elif display_role == "assistant":
                    # Display assistant message
                    with st.chat_message("assistant", avatar="🤖"):
                        st.markdown(content)
                    # Add to history
                    st.session_state.messages.append({"role": "assistant", "content": content})

                elif display_role == "error":
                    # Display error message
                    with st.chat_message("assistant", avatar="⚠️"):
                        st.error(content)
                    # Add to history
                    st.session_state.messages.append({"role": "error", "content": content})

        except requests.exceptions.RequestException as e:
            with st.chat_message("assistant", avatar="⚠️"):
                st.error(f"Error connecting to backend: {e}")
            st.session_state.messages.append({"role": "error", "content": f"Failed to get response: {e}"})

        except json.JSONDecodeError:
            with st.chat_message("assistant", avatar="⚠️"):
                st.error("Received invalid response from backend.")
            st.session_state.messages.append({"role": "error", "content": "Failed to decode backend response."})

        except Exception as e:
            with st.chat_message("assistant", avatar="⚠️"):
                st.error(f"An unexpected error occurred: {e}")
            st.session_state.messages.append({"role": "error", "content": f"An error occurred: {e}"})