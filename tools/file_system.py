import os
from pathlib import Path
import logging
import winreg
# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ALLOWED_READ_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log", ".py", ".js",
    ".html", ".css", ".xml", ".yaml", ".yml"
}
MAX_READ_CHARS = 10000 # Max characters to read to prevent memory issues

# --- Directory Setup Functions ---

def _get_desktop_path() -> Path | None:
    """Tries to determine the user's visible Desktop path using the Windows Registry."""
    desktop_path = None # Initial value
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    value_name = "Desktop"

    try:
        # --- Windows Registry Method ---
        # Access the relevant registry key (uses variables defined above)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)

        # Read the 'Desktop' value
        raw_path, _ = winreg.QueryValueEx(key, value_name)
        expanded_path = os.path.expandvars(raw_path) # Important step!
        desktop_path = Path(expanded_path) # Overwrite initial None

        winreg.CloseKey(key)
        logger.info(f"Determined Desktop path via Registry: {desktop_path}")

    except FileNotFoundError:
        # Now key_path and value_name are guaranteed to be defined here
        logger.warning(f"Registry key or value for Desktop not found ({key_path} -> {value_name}). Falling back.")
        desktop_path = None # Ensure it's None if registry lookup fails

    except OSError as e:
        # Catches potential permission errors or other OS issues accessing registry
        logger.error(f"Error accessing Windows Registry for Desktop path: {e}", exc_info=True)
        desktop_path = None # Ensure it's None on error

    except Exception as e:
        # Catch any other unexpected errors during registry access
        logger.error(f"Unexpected error reading Desktop path from Registry: {e}", exc_info=True)
        desktop_path = None

    # --- Fallback to Standard Method (If Registry Fails) ---
    if desktop_path is None:
        try:
            fallback_path = Path.home() / "Desktop"
            if fallback_path.is_dir():
                desktop_path = fallback_path
                logger.warning(f"Using fallback Desktop path: {desktop_path}")
            else:
                 logger.warning(f"Fallback Desktop path '{fallback_path}' not found or not a directory.")
                 # Keep desktop_path as None
        except Exception as fallback_e:
            logger.error(f"Could not determine user's home directory or Desktop path via fallback: {fallback_e}", exc_info=True)
            # Keep desktop_path as None


    # --- Final Check and Return ---
    if desktop_path and desktop_path.is_dir():
        return desktop_path
    elif desktop_path: # Path obtained but wasn't a directory
         logger.warning(f"Determined path '{desktop_path}' exists but is not a directory. Treating as unavailable.")
         return None
    else: # No path determined or validated
        logger.error("CRITICAL: Could not determine Desktop path via Registry or fallback.")
        return None

def _initialize_directory(dir_name: str, desktop_path: Path | None) -> Path | None:
    """Initializes a specific directory (workspace or ingest), returns its absolute path or None."""
    if desktop_path:
        target_dir = desktop_path / dir_name
        logger.info(f"Targeting {dir_name} directory on Desktop: {target_dir}")
    else:
        # Fallback if Desktop path is unavailable
        target_dir = Path(f"./{dir_name}").resolve()
        logger.warning(f"Could not reliably determine Desktop path. Falling back to local {dir_name} directory: {target_dir}")

    try:
        # Create the directory if it doesn't exist. parents=True ensures intermediate dirs are also created.
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured {dir_name} directory exists: {target_dir}")
        return target_dir.resolve() # Return the absolute, resolved path
    except OSError as e:
        logger.error(f"CRITICAL: Could not create or access {dir_name} directory '{target_dir}': {e}", exc_info=True)
        return None # Indicate critical failure

# --- Global Directory Variables ---
_desktop = _get_desktop_path()
AI_WORKSPACE_DIR = _initialize_directory("ai_workspace", _desktop)


# --- Helper Function for Path Validation (Against a specific base directory) ---

def _resolve_and_validate_path(relative_path_str: str, base_dir: Path) -> Path | None:
    """
    Resolves a relative path against the specified base directory (WORKSPACE or INGEST)
    and validates it to prevent access outside that specific directory.

    Args:
        relative_path_str: The relative path string provided by the AI.
        base_dir: The absolute Path object of the base directory (AI_WORKSPACE_DIR or AI_INGEST_DIR).

    Returns:
        A resolved and validated absolute Path object if safe within the base_dir, otherwise None.
    """
    if base_dir is None:  # Should only be AI_WORKSPACE_DIR now
        logger.error(f"AI Workspace directory is not available for validation.")
        return None

    if not relative_path_str:
        logger.warning(f"Attempted operation with empty path string within {base_dir}.")
        return None

    try:
        # Clean the input path string (e.g., remove leading/trailing whitespace)
        clean_relative_path = Path(relative_path_str.strip())

        # Disallow absolute paths provided by the AI and path traversal attempts using '..'
        if clean_relative_path.is_absolute() or ".." in clean_relative_path.parts:
             logger.warning(f"Disallowed absolute path or traversal component '..' in: '{relative_path_str}' within {base_dir}")
             return None

        # Join with base and resolve to an absolute path (.resolve() handles ., .., symlinks)
        resolved_path = (base_dir / clean_relative_path).resolve()

        # *** MAJOR SECURITY CHECK ***
        # Ensure the resolved absolute path is ACTUALLY inside the specified base directory.
        if base_dir not in resolved_path.parents and resolved_path != base_dir:
            logger.warning(
                f"Attempt to access path '{resolved_path}' which is outside the AI Workspace directory '{base_dir}'. Input was: '{relative_path_str}'")
            return None

        # If all checks pass, return the safe, absolute path
        return resolved_path

    except Exception as e:
        # Catch potential errors during Path object creation or resolution
        logger.error(f"Error resolving or validating path '{relative_path_str}' against base '{base_dir}': {e}", exc_info=True)
        return None

# --- WORKSPACE Tool Functions (Operate ONLY within AI_WORKSPACE_DIR) ---

async def list_directory(relative_path_str: str) -> str:
    """Lists files/subdirs in a specified path *within the workspace*."""
    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."
    logger.info(f"Executing list_directory for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR) # Validate against WORKSPACE
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
            item_display_name = item.name # Just the name within the listed dir
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


async def read_file(relative_path_str: str) -> str:
    """Reads the content of a specified text file *within the workspace*."""
    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."
    logger.info(f"Executing read_file for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR) # Validate against WORKSPACE
    if not validated_path:
        return f"Error: Invalid or disallowed workspace path '{relative_path_str}'. Cannot read file."

    if not validated_path.exists():
        return f"Error: File '{relative_path_str}' does not exist within the workspace."
    if not validated_path.is_file():
        return f"Error: Path '{relative_path_str}' within the workspace is not a file."

    # Check file extension
    if validated_path.suffix.lower() not in ALLOWED_READ_EXTENSIONS:
        logger.warning(f"Attempt to read disallowed file type: {validated_path}")
        allowed_ext_str = ", ".join(sorted(list(ALLOWED_READ_EXTENSIONS)))
        return f"Error: Cannot read file '{relative_path_str}'. Only specific text-based files are allowed (extensions: {allowed_ext_str})."

    try:
        content = validated_path.read_text(encoding='utf-8')
        output_path_name = validated_path.relative_to(AI_WORKSPACE_DIR)
        if len(content) > MAX_READ_CHARS:
            truncated_content = content[:MAX_READ_CHARS]
            logger.info(f"Read file '{validated_path}' but truncated content from {len(content)} to {MAX_READ_CHARS} chars.")
            return f"Content of workspace file '{output_path_name}' (truncated to {MAX_READ_CHARS} characters):\n\n{truncated_content}\n\n[... File truncated ...]"
        else:
            logger.info(f"Successfully read file: {validated_path}")
            return f"Content of workspace file '{output_path_name}':\n\n{content}"
    except UnicodeDecodeError:
        logger.warning(f"Could not decode file '{validated_path}' as UTF-8.")
        return f"Error: Could not read file '{relative_path_str}' as UTF-8 text. It might be binary or have an incompatible encoding."
    except PermissionError:
         logger.warning(f"Permission denied reading file '{validated_path}'.")
         return f"Error: Permission denied trying to read file '{relative_path_str}'."
    except Exception as e:
        logger.error(f"Error reading file '{validated_path}': {e}", exc_info=True)
        return f"Error: Could not read file '{relative_path_str}' due to an unexpected error."


async def write_file(relative_path_str: str, content: str) -> str:
    """Writes (or overwrites) PLAIN TEXT content to a specified file *within the workspace*."""
    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."
    logger.info(f"Executing write_file for workspace path: '{relative_path_str}'")
    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR) # Validate against WORKSPACE
    if not validated_path:
        return f"Error: Invalid or disallowed workspace path '{relative_path_str}'. Cannot write file."

    if validated_path == AI_WORKSPACE_DIR:
         logger.warning(f"Attempt to write directly to workspace root rejected.")
         return f"Error: Cannot write directly to the root workspace directory. Please specify a filename."
    if validated_path.is_dir():
        logger.warning(f"Attempt to write file over existing directory: {validated_path}")
        return f"Error: Cannot write file. Path '{relative_path_str}' already exists as a directory in the workspace."

    parent_dir = validated_path.parent
    if not parent_dir.exists():
         logger.warning(f"Attempt to write file '{validated_path}' but parent directory does not exist.")
         parent_relative = parent_dir.relative_to(AI_WORKSPACE_DIR)
         return f"Error: Parent directory '{parent_relative}' does not exist in the workspace. Please create it first using [FS_MKDIR: {parent_relative}]."
    elif not parent_dir.is_dir():
        parent_relative = parent_dir.relative_to(AI_WORKSPACE_DIR)
        return f"Error: Cannot write file. The parent path '{parent_relative}' exists but is not a directory."

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


async def create_directory(relative_path_str: str) -> str:
    """Creates a new directory (including intermediate ones) *within the workspace*."""
    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."
    logger.info(f"Executing create_directory for workspace path: '{relative_path_str}'")
    if not relative_path_str or Path(relative_path_str.strip()) == Path('.'):
        return "Error: Cannot create directory with an empty name or just '.'."

    validated_path = _resolve_and_validate_path(relative_path_str, AI_WORKSPACE_DIR) # Validate against WORKSPACE
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


async def find_files(start_path_str: str, pattern: str) -> str:
    """Finds files matching a glob pattern recursively *within a specified workspace start path*."""
    if AI_WORKSPACE_DIR is None: return "Error: Workspace directory not available."
    logger.info(f"Executing find_files from workspace path '{start_path_str}' with pattern '{pattern}'")

    cleaned_pattern = pattern.strip()
    if not cleaned_pattern: return "Error: Search pattern cannot be empty."
    if cleaned_pattern.startswith(('/', '\\')) or ':' in cleaned_pattern:
        return "Error: Search pattern should be relative (e.g., '*.txt', 'reports/**/*.pdf')."

    validated_start_path = _resolve_and_validate_path(start_path_str, AI_WORKSPACE_DIR) # Validate against WORKSPACE
    if not validated_start_path:
        return f"Error: Invalid or disallowed workspace start path '{start_path_str}'. Cannot search."
    if not validated_start_path.is_dir():
        return f"Error: Workspace start path '{start_path_str}' is not a directory."

    try:
        found_items = list(validated_start_path.rglob(cleaned_pattern))
        found_files = [f for f in found_items if f.is_file()] # Filter out directories
        start_name = validated_start_path.relative_to(AI_WORKSPACE_DIR) if validated_start_path != AI_WORKSPACE_DIR else "."

        if not found_files:
            return f"No files found matching pattern '{pattern}' within workspace path '{start_name}'."

        relative_paths = sorted([str(f.relative_to(AI_WORKSPACE_DIR)) for f in found_files])
        logger.info(f"Found {len(relative_paths)} files matching '{pattern}' in '{start_name}'.")
        # List only relative paths for the AI
        return f"Files found matching '{pattern}' in workspace path '{start_name}':\n- " + "\n- ".join(relative_paths)
    except PermissionError:
         logger.warning(f"Permission denied during file search in '{validated_start_path}'.")
         return f"Error: Permission denied while searching for files in workspace path '{start_path_str}'."
    except Exception as e:
        logger.error(f"Error finding files in '{validated_start_path}' with pattern '{pattern}': {e}", exc_info=True)
        return f"Error: Could not search for files due to an unexpected error."