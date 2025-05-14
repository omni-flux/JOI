import asyncio
import os
from typing import Dict, Any

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr

# Try to import browser_use and set a flag
try:
    from browser_use import Agent, BrowserConfig, Browser

    BROWSER_USE_AVAILABLE = True
except ImportError:
    print("CRITICAL WARNING: 'browser-use' library not found. Browser tool will be unavailable.")
    BROWSER_USE_AVAILABLE = False
    Agent, BrowserConfig, Browser = type(None), type(None), type(None)

if BROWSER_USE_AVAILABLE:
    load_dotenv()

_MODULE_LLM = None
_MODULE_PLANNER_LLM = None

if BROWSER_USE_AVAILABLE:
    api_key_gemini = os.getenv('GEMINI_API_KEY')
    if api_key_gemini:
        try:
            _MODULE_LLM = ChatGoogleGenerativeAI(model='gemini-2.0-flash', api_key=SecretStr(api_key_gemini))
            _MODULE_PLANNER_LLM = ChatGoogleGenerativeAI(model='gemini-2.5-pro-preview-05-06',
                                                         api_key=SecretStr(api_key_gemini))
        except Exception as e:
            print(f"ERROR: Failed to initialize LLMs for browser_tool: {e}")
    else:
        print('WARNING: GEMINI_API_KEY not set. LLMs for browser_tool may not function correctly.')


async def browser_automation(args: Dict[str, Any]) -> str:
    if not BROWSER_USE_AVAILABLE:
        return "Error: The 'browser-use' library is not available. Browser automation tool cannot run."
    if not _MODULE_LLM or not _MODULE_PLANNER_LLM:
        return "Error: Core LLMs for the browser agent are not initialized. Check GEMINI_API_KEY and logs."

    task = args.get("task")
    if not task:
        return "Error: Missing 'task' argument for browser automation."

    browser_executable_path = os.getenv('CHROME_PATH')
    browser_user_data_dir = os.getenv('CHROME_DATA_PATH')

    if not browser_executable_path:
        return 'Error: CHROME_PATH environment variable is not configured.'
    if not browser_user_data_dir:
        return 'Error: CHROME_DATA_PATH environment variable is not configured.'

    local_browser_instance = None
    try:
        local_browser_instance = Browser(
            config=BrowserConfig(
                browser_binary_path=browser_executable_path,
                chrome_remote_debugging_port=9223,
                extra_browser_args=[
                    f"--user-data-dir={browser_user_data_dir}",
                ],
                headless=False,
                _force_keep_browser_alive=True,
            )
        )

        async with await local_browser_instance.new_context() as browser_context_for_task:
            agent = Agent(
                task=task,
                llm=_MODULE_LLM,
                max_actions_per_step=4,
                browser_context=browser_context_for_task,
                extend_system_message='',
                planner_llm=_MODULE_PLANNER_LLM,
                extend_planner_system_message='',
                use_vision_for_planner=False,
                planner_interval=4
            )
            history = await agent.run(max_steps=25)

        # --- Result Processing ---
        if history.is_done():
            final_text_result = history.final_result()
            if final_text_result:
                return f"Browser task completed successfully.\nSummary: {final_text_result}"
            else:
                return "Browser task completed successfully, but no specific text content was extracted by the agent."
        elif history.has_errors():
            return "Error: The browser automation agent encountered errors and could not complete the task."
        else:
            return "Browser automation task finished without errors, but the agent did not explicitly mark it as complete (e.g., max steps reached or goal unclear)."

    except Exception as e:
        error_type = type(e).__name__
        return f"Error: Browser automation failed due to an unexpected system issue ({error_type})."
    finally:
        if local_browser_instance:
            try:
                await local_browser_instance.close()
            except Exception as e_close:
                pass

if __name__ == '__main__':
    async def main_test():
        await browser_automation(args={"task": "Go to wikipedia.org, search for 'Playwright (software)', and tell me the first sentence of the main content."})

    asyncio.run(main_test())