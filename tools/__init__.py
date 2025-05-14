from .app_control import open_app
from .web_search import search_and_crawl
from .system_info import system_info
from .email_sender import send_email
from .calendar_tasks import manage_calendar_tasks
from .browser_tool import browser_automation


from .file_system import (
    list_directory,
    read_file,
    write_file,
    create_directory,
    find_files
)
from .tool_registry import ToolRegistry

tool_registry = ToolRegistry()

tool_registry.register(
    name="app",
    function=open_app
)

tool_registry.register(
    name="search",
    function=search_and_crawl
)

# Register System Info
tool_registry.register(
    name="sysinfo",
    function=system_info
)

# Register File System Tools
tool_registry.register(
    name="fs_list",
    function=list_directory
)
tool_registry.register(
    name="fs_read",
    function=read_file
)
tool_registry.register(
    name="fs_write",
    function=write_file
)
tool_registry.register(
    name="fs_mkdir",
    function=create_directory
)
tool_registry.register(
    name="fs_find",
    function=find_files
)

# Register Email Tool
tool_registry.register(
    name="email",
    function=send_email
)

# Register the new Calendar/Tasks tool
tool_registry.register(
    name="calendar", # Tool name the AI will use
    function=manage_calendar_tasks
)

# Register Browser Automation Tool
tool_registry.register(
    name="browser",
    function=browser_automation
)

__all__ = ['tool_registry']

