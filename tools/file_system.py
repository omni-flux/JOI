import os
from pathlib import Path
import logging
import winreg
from typing import Dict, Any # Import Dict and Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ALLOWED_READ_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log", ".py", ".js",
    ".html", ".css", ".xml", ".yaml", ".yml"
}
MAX_READ_CHARS = 10000

def _get_desktop_path() -> Path | None:
    """Tries to determine the user's visible Desktop path using the Windows Registry."""
    desktop_path = None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    value_name = "Desktop"

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        raw_path, _ = winreg.QueryValueEx(key, value_name)
        expanded_path = os.path.expandvars(raw_path) # Important step!
        desktop_path = Path(expanded_path)
        winreg.CloseKey(key)
        logger.info(f"Determined Desktop path via Registry: {desktop_path}")
    except FileNotFoundError:
        logger.warning(f"Registry key or value for Desktop not found ({key_path} -> {value_name}). Falling back.")
        desktop_path = None
    except OSError as e:
        logger.error(f"Error accessing Windows Registry for Desktop path: {e}", exc_info=True)
        desktop_path = None
    except Exception as e:
        logger.error(f"Unexpected error reading Desktop path from Registry: {e}", exc_info=True)
        desktop_path = None

    if desktop_path is None:
        try:
            fallback_path = Path.home() / "Desktop"
            if fallback_path.is_dir():
                desktop_path = fallback_path
                logger.warning(f"Using fallback Desktop path: {desktop_path}")
            else:
                 logger.warning(f"Fallback Desktop path '{fallback_path}' not found or not a directory.")
        except Exception as fallback_e:
            logger.error(f"Could not determine user's home directory or Desktop path via fallback: {fallback_e}", exc_info=True)

    if desktop_path and desktop_path.is_dir():
        return desktop_path
    elif desktop_path:
         logger.warning(f"Determined path '{desktop_path}' exists but is not a directory. Treating as unavailable.")
         return None
    else:
        logger.error("CRITICAL: Could not determine Desktop path via Registry or fallback.")
        return None

def _initialize_directory(dir_name: str, desktop_path: Path | None) -> Path | None:
    """Initializes a specific directory (workspace or ingest), returns its absolute path or None."""
    if desktop_path:
        target_dir = desktop_path / dir_name
        logger.info(f"Targeting {dir_name} directory on Desktop: {target_dir}")
    else:
        target_dir = Path(f"./{dir_name}").resolve()
        logger.warning(f"Could not reliably determine Desktop path. Falling back to local {dir_name} directory: {target_dir}")

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured {dir_name} directory exists: {target_dir}")
        return target_dir.resolve()
    except OSError as e:
        logger.error(f"CRITICAL: Could not create or access {dir_name} directory '{target_dir}': {e}", exc_info=True)
        return None

_desktop = _get_desktop_path()
AI_WORKSPACE_DIR = _initialize_directory("ai_workspace", _desktop)

def _resolve_and_validate_path(relative_path_str: str, base_dir: Path) -> Path | None:
    """
    Resolves a relative path against the specified base directory (WORKSPACE or INGEST)
    and validates it to prevent access outside that specific directory.
    """
    if base_dir is None:
        logger.error(f"AI Workspace directory is not available for validation.")
        return None
    if not relative_path_str:
        logger.warning(f"Attempted operation with empty path string within {base_dir}.")
        return None
    try:
        clean_relative_path = Path(relative_path_str.strip())
        if clean_relative_path.is_absolute() or ".." in clean_relative_path.parts:
             logger.warning(f"Disallowed absolute path or traversal component '..' in: '{relative_path_str}' within {base_dir}")
             return None
        resolved_path = (base_dir / clean_relative_path).resolve()
        if base_dir not in resolved_path.parents and resolved_path != base_dir:
            logger.warning(
                f"Attempt to access path '{resolved_path}' which is outside the AI Workspace directory '{base_dir}'. Input was: '{relative_path_str}'")
            return None
        return resolved_path
    except Exception as e:
        logger.error(f"Error resolving or validating path '{relative_path_str}' against base '{base_dir}': {e}", exc_info=True)
        return None

async def list_directory(args: Dict[str, Any]) -> str:
    """Lists files/sub-dirs in a specified path *within the workspace*."""
    relative_path_str = args.get("relative_path")
    if relative_path_str is None: # Check if key exists
        return "Error: Missing 'relative_path' argument for fs_list."
    relative_path_str = str(relative_path_str)

    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."

    logger.info(f"Executing list_directory for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR)
    if not validated_path:
        return f"Error: Invalid or disallowed workspace path '{relative_path_str}'."

    if not validated_path.exists():
        return f"Error: Workspace path '{relative_path_str}' does not exist."
    if not validated_path.is_dir():
        return f"Error: Workspace path '{relative_path_str}' is not a directory."

    try:
        items = []
        for item in validated_path.iterdir():
            prefix = "[D]" if item.is_dir() else "[F]"
            item_display_name = item.name
            items.append(f"{prefix} {item_display_name}")

        output_dir_name = validated_path.relative_to(AI_WORKSPACE_DIR) if validated_path != AI_WORKSPACE_DIR else "."
        if not items:
            return f"Workspace directory '{output_dir_name}' is empty."
        else:
            return f"Contents of workspace path '{output_dir_name}':\n- " + "\n- ".join(sorted(items))
    except PermissionError:
        logger.warning(f"Permission denied accessing directory '{validated_path}'.")
        return f"Error: Permission denied trying to list workspace directory '{relative_path_str}'."
    except Exception as e:
        logger.error(f"Error listing directory '{validated_path}': {e}", exc_info=True)
        return f"Error: Could not list workspace directory '{relative_path_str}' due to an unexpected error."


async def read_file(args: Dict[str, Any]) -> str:
    """Reads the content of a specified text file *within the workspace*."""
    relative_path_str = args.get("relative_path")
    if relative_path_str is None:
        return "Error: Missing 'relative_path' argument for fs_read."
    relative_path_str = str(relative_path_str)

    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."

    logger.info(f"Executing read_file for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR)
    if not validated_path:
        return f"Error: Invalid or disallowed workspace path '{relative_path_str}'. Cannot read file."

    if not validated_path.exists():
        return f"Error: File '{relative_path_str}' does not exist within the workspace."
    if not validated_path.is_file():
        return f"Error: Path '{relative_path_str}' within the workspace is not a file."

    if validated_path.suffix.lower() not in ALLOWED_READ_EXTENSIONS:
        logger.warning(f"Attempt to read disallowed file type: {validated_path}")
        allowed_ext_str = ", ".join(sorted(list(ALLOWED_READ_EXTENSIONS)))
        return f"Error: Cannot read file '{relative_path_str}'. Only specific text-based files are allowed (extensions: {allowed_ext_str})."

    try:
        with open(validated_path, 'r', encoding='utf-8') as f:
             content_part = f.read(MAX_READ_CHARS + 1)

        output_path_name = validated_path.relative_to(AI_WORKSPACE_DIR)

        if len(content_part) > MAX_READ_CHARS:
            truncated_content = content_part[:MAX_READ_CHARS]
            logger.info(f"Read file '{validated_path}' but truncated content at {MAX_READ_CHARS} chars.")
            return f"Content of workspace file '{output_path_name}' (truncated to {MAX_READ_CHARS} characters):\n\n{truncated_content}\n\n[... File truncated ...]"
        else:
            logger.info(f"Successfully read file: {validated_path}")
            return f"Content of workspace file '{output_path_name}':\n\n{content_part}"

    except UnicodeDecodeError:
        logger.warning(f"Could not decode file '{validated_path}' as UTF-8.")
        return f"Error: Could not read file '{relative_path_str}' as UTF-8 text. It might be binary or have an incompatible encoding."
    except PermissionError:
         logger.warning(f"Permission denied reading file '{validated_path}'.")
         return f"Error: Permission denied trying to read file '{relative_path_str}'."
    except Exception as e:
        logger.error(f"Error reading file '{validated_path}': {e}", exc_info=True)
        return f"Error: Could not read file '{relative_path_str}' due to an unexpected error."


async def write_file(args: Dict[str, Any]) -> str:
    """Writes (or overwrites) PLAIN TEXT content to a specified file *within the workspace*."""
    relative_path_str = args.get("relative_path")
    content = args.get("content") # Content can be None or empty string, check path first

    if relative_path_str is None:
        return "Error: Missing 'relative_path' argument for fs_write."
    # Check content type explicitly, allow empty string but not None if path is valid
    if content is None:
         return "Error: Missing 'content' argument for fs_write."

    relative_path_str = str(relative_path_str)
    content = str(content)

    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."

    logger.info(f"Executing write_file for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR)
    if not validated_path:
        return f"Error: Invalid or disallowed workspace path '{relative_path_str}'. Cannot write file."

    if validated_path == AI_WORKSPACE_DIR:
         logger.warning(f"Attempt to write directly to workspace root rejected.")
         return f"Error: Cannot write directly to the root workspace directory. Please specify a filename."
    if validated_path.is_dir():
        logger.warning(f"Attempt to write file over existing directory: {validated_path}")
        return f"Error: Cannot write file. Path '{relative_path_str}' already exists as a directory in the workspace."

    # Check parent directory exists
    parent_dir = validated_path.parent
    if not parent_dir.is_dir():
         if AI_WORKSPACE_DIR in parent_dir.parents or parent_dir == AI_WORKSPACE_DIR:
              try:
                  parent_dir.mkdir(parents=True, exist_ok=True)
                  logger.info(f"Created missing parent directory: {parent_dir}")
              except Exception as mkdir_e:
                   logger.error(f"Failed to create parent directory '{parent_dir}': {mkdir_e}", exc_info=True)
                   return f"Error: Parent directory '{parent_dir.relative_to(AI_WORKSPACE_DIR)}' does not exist and could not be created."
         else:
              # This case should ideally be caught by _resolve_and_validate_path, but double-check
              logger.warning(f"Attempt to write file '{validated_path}' with parent outside workspace rejected.")
              return f"Error: Cannot write file. Parent directory is outside the allowed workspace."


    try:
        validated_path.write_text(content, encoding='utf-8')
        logger.info(f"Successfully wrote {len(content)} characters to file: {validated_path}")
        output_path_name = validated_path.relative_to(AI_WORKSPACE_DIR)
        return f"Successfully wrote plain text content to workspace file '{output_path_name}'."
    except PermissionError:
         logger.warning(f"Permission denied writing to file '{validated_path}'.")
         return f"Error: Permission denied trying to write to file '{relative_path_str}'."
    except Exception as e:
        logger.error(f"Error writing file '{validated_path}': {e}", exc_info=True)
        return f"Error: Could not write to file '{relative_path_str}' due to an unexpected error."


async def create_directory(args: Dict[str, Any]) -> str:
    """Creates a new directory (including intermediate ones) *within the workspace*."""
    relative_path_str = args.get("relative_path")
    if relative_path_str is None:
        return "Error: Missing 'relative_path' argument for fs_mkdir."
    relative_path_str = str(relative_path_str).strip() # Clean input

    if not relative_path_str or Path(relative_path_str) == Path('.'):
        return "Error: Cannot create directory with an empty name or just '.'."

    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."

    logger.info(f"Executing create_directory for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR)
    if not validated_path:
        return f"Error: Invalid or disallowed workspace path '{relative_path_str}'. Cannot create directory."
    if validated_path == AI_WORKSPACE_DIR:
         return f"Error: Cannot explicitly create the root workspace directory."

    output_path_name = validated_path.relative_to(AI_WORKSPACE_DIR)
    if validated_path.exists():
        if validated_path.is_dir():
            return f"Workspace directory '{output_path_name}' already exists."
        else:
            return f"Error: Cannot create directory. Path '{output_path_name}' already exists as a file in the workspace."

    try:
        validated_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Successfully created directory: {validated_path}")
        return f"Successfully created workspace directory '{output_path_name}'."
    except PermissionError:
         logger.warning(f"Permission denied creating directory '{validated_path}'.")
         return f"Error: Permission denied trying to create directory '{relative_path_str}'."
    except Exception as e:
        logger.error(f"Error creating directory '{validated_path}': {e}", exc_info=True)
        return f"Error: Could not create directory '{relative_path_str}' due to an unexpected error."


async def find_files(args: Dict[str, Any]) -> str:
    """Finds files matching a glob pattern recursively *within a specified workspace start path*."""
    start_path_str = args.get("start_path")
    pattern = args.get("pattern")

    if start_path_str is None:
        return "Error: Missing 'start_path' argument for fs_find."
    if pattern is None:
        return "Error: Missing 'pattern' argument for fs_find."

    start_path_str = str(start_path_str)
    pattern = str(pattern).strip() # Clean pattern

    if not pattern:
        return "Error: Search pattern cannot be empty."
    # Basic check for absolute-like patterns or attempts to leave workspace via pattern
    if pattern.startswith(('/', '\\')) or ':' in pattern or '..' in pattern:
        return f"Error: Invalid search pattern '{pattern}'. Pattern should be relative and not contain '..'."

    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."

    logger.info(f"Executing find_files from workspace path '{start_path_str}' with pattern '{pattern}'")

    validated_start_path = _resolve_and_validate_path(start_path_str, AI_WORKSPACE_DIR)
    if not validated_start_path:
        return f"Error: Invalid or disallowed workspace start path '{start_path_str}'. Cannot search."
    if not validated_start_path.is_dir():
         output_start_name = validated_start_path.relative_to(AI_WORKSPACE_DIR) if AI_WORKSPACE_DIR in validated_start_path.parents else start_path_str
         return f"Error: Workspace start path '{output_start_name}' is not a directory."


    try:
        # Use rglob for recursive search
        found_items = list(validated_start_path.rglob(pattern))
        # Filter only files from the results
        found_files = [f for f in found_items if f.is_file()]
        # Get the display name for the start path
        start_name = validated_start_path.relative_to(AI_WORKSPACE_DIR) if validated_start_path != AI_WORKSPACE_DIR else "."

        if not found_files:
            return f"No files found matching pattern '{pattern}' within workspace path '{start_name}'."

        # Get relative paths from the AI_WORKSPACE_DIR root for clarity
        relative_paths = sorted([str(f.relative_to(AI_WORKSPACE_DIR)) for f in found_files])

        logger.info(f"Found {len(relative_paths)} files matching '{pattern}' in '{start_name}'.")
        # Limit the number of results shown to avoid overwhelming output
        MAX_FIND_RESULTS = 50
        if len(relative_paths) > MAX_FIND_RESULTS:
             output_paths = relative_paths[:MAX_FIND_RESULTS]
             output_paths.append(f"... ({len(relative_paths) - MAX_FIND_RESULTS} more)")
        else:
             output_paths = relative_paths

        return f"Files found matching '{pattern}' in workspace path '{start_name}':\n- " + "\n- ".join(output_paths)

    except PermissionError:
         logger.warning(f"Permission denied during file search in '{validated_start_path}'.")
         return f"Error: Permission denied while searching for files in workspace path '{start_path_str}'."
    except Exception as e:
        logger.error(f"Error finding files in '{validated_start_path}' with pattern '{pattern}': {e}", exc_info=True)
        return f"Error: Could not search for files due to an unexpected error."
