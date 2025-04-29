import time
import pyautogui
from typing import Dict

def open_app(args: Dict[str, str]) -> str:
    """Opens the specified application using a Win+type+Enter approach.
       Expects args dictionary with 'app_name' key.
    """
    app_name = args.get("app_name")
    if not app_name:
        return "Error: Missing 'app_name' argument for the app tool."
    try:
        pyautogui.press('win')
        time.sleep(0.5)
        pyautogui.write(app_name, interval=0.1)
        time.sleep(0.5)
        pyautogui.press('enter')
        return f"Attempted to open application: {app_name}"
    except Exception as e:
        return f"Error opening application {app_name}: {str(e)}"

