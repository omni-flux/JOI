import time
import pyautogui

def open_app(app_name: str) -> str:
    """Opens the specified application using a Win+type+Enter approach."""
    try:
        pyautogui.press('win')
        time.sleep(0.5)
        pyautogui.write(app_name, interval=0.1)
        time.sleep(0.5)
        pyautogui.press('enter')
        return f"Attempted to open application: {app_name}"
    except Exception as e:
        return f"Error opening application {app_name}: {str(e)}"