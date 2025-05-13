import asyncio
import os
from typing import Dict, Any

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr
from browser_use import Agent, BrowserConfig, Browser

load_dotenv()
api_key = os.getenv('GEMINI_API_KEY')
browser_path = os.getenv('CHROME_PATH')
browser_data_path = os.getenv('CHROME_DATA_PATH')
if not api_key:
    raise ValueError('GEMINI_API_KEY is not set')
if not browser_path:
    raise ValueError('CHROME_PATH is not set')
if not browser_data_path:
    raise ValueError('CHROME_DATA_PATH is not set')

llm = ChatGoogleGenerativeAI(model='gemini-2.0-flash', api_key=SecretStr(api_key))
planner_llm = ChatGoogleGenerativeAI(model='gemini-2.5-pro-preview-05-06', api_key=SecretStr(api_key))

browser = Browser(
    config=BrowserConfig(
        browser_binary_path=browser_path,
        chrome_remote_debugging_port=9223,
        extra_browser_args=[
            f"--user-data-dir={browser_data_path}",
        ],
        headless=False,
        # _force_keep_browser_alive=True,
    )
)

async def browser_automation(args: Dict[str, Any]) -> str:
    task = args.get("task")
    if not task:
        return "Error: Missing 'task' argument for browser automation."

    try:
        agent = Agent(
            task=task,
            llm=llm,
            max_actions_per_step=4,
            browser=browser,
            extend_system_message='',
            planner_llm=planner_llm,
            extend_planner_system_message='',
            use_vision_for_planner=False,
            planner_interval=4
        )

        history = await agent.run(max_steps=25)
        if history.is_done():
            return history.final_result()
        elif history.has_errors():
            return f"Browser automation failed: {history.errors()}"
        else:
            return "Browser automation completed but no final result was produced."
    except Exception as e:
        return f"Browser automation failed: {str(e)}"


if __name__ == '__main__':
    asyncio.run(browser_automation(args={"task": "go to google maps and find the exact distance between oscar city and trident academy of technology infocity"}))