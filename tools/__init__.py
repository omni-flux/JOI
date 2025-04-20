from .app_control import open_app
from .web_search import search_and_crawl
from .system_info import system_info
from .tool_registry import ToolRegistry

# --- Import File System Tool Functions ---
from .file_system import (
    list_directory,
    read_file,
    write_file,
    create_directory,
    find_files
)

# --- Import NEW Email Tool Function ---
from .email_sender import send_email # ADD THIS LINE

# --- Create a shared tool registry instance ---
tool_registry = ToolRegistry()

# --- Register existing tools ---
tool_registry.register(
    name="app",
    function=open_app,
    pattern=r'\[OPEN_APP:\s*([^\]]+)\]',
    priority=10
)
tool_registry.register(
    name="search",
    function=search_and_crawl,
    pattern=r'\[SEARCH:\s*([^\]]+)\]',
    priority=20
)
tool_registry.register(
    name="sysinfo",
    function=system_info,
    pattern=r'\[SYSINFO:\s*([^\]]*)\]',
    priority=30
)

# --- Register File System Tools ---
tool_registry.register(
    name="fs_list",
    function=list_directory,
    pattern=r'\[FS_LIST:\s*([^\]]+)\]',
    priority=40
)
tool_registry.register(
    name="fs_read",
    function=read_file,
    pattern=r'\[FS_READ:\s*([^\]]+)\]',
    priority=41
)
tool_registry.register(
    name="fs_write",
    function=write_file,
    pattern=r'\[FS_WRITE:\s*([^\]]+)\]', # Captures 'path | content'
    priority=42
)
tool_registry.register(
    name="fs_mkdir",
    function=create_directory,
    pattern=r'\[FS_MKDIR:\s*([^\]]+)\]',
    priority=43
)
tool_registry.register(
    name="fs_find",
    function=find_files,
    pattern=r'\[FS_FIND:\s*([^\]]+)\]', # Captures 'start_path | pattern'
    priority=44
)

# --- Register Email Tool ---
tool_registry.register(
    name="email",
    function=send_email,
    pattern=r'\[EMAIL:\s*([^\]]+)\]',
    priority=50
)

__all__ = ['tool_registry']