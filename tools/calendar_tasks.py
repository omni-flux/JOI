import re
import pickle
import asyncio
import logging
import datetime
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path

from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

# Define required scopes
SCOPES_CALENDAR_READONLY = ['https://www.googleapis.com/auth/calendar.readonly']
SCOPES_CALENDAR_EVENTS = ['https://www.googleapis.com/auth/calendar.events']
SCOPES_TASKS_READONLY = ['https://www.googleapis.com/auth/tasks.readonly']
SCOPES_TASKS = ['https://www.googleapis.com/auth/tasks']

# Mapping actions to required scopes (Reduced Set)
ACTION_SCOPES = {
    'list_events': SCOPES_CALENDAR_READONLY,
    'create_event': SCOPES_CALENDAR_EVENTS,
    'update_event': SCOPES_CALENDAR_EVENTS,
    'delete_event': SCOPES_CALENDAR_EVENTS,
    'list_tasklists': SCOPES_TASKS_READONLY,
    'list_tasks': SCOPES_TASKS_READONLY,
    'create_task': SCOPES_TASKS,
    'update_task': SCOPES_TASKS,
    'delete_task': SCOPES_TASKS,
}


# --- Generalized Authentication ---
def _get_google_service_sync(
        service_name: str,
        api_version: str,
        scopes: List[str]
) -> Tuple[Optional[Resource], Optional[str]]:
    """
    Synchronous function to get an authenticated Google API service.
    """
    creds = None
    tools_dir = Path(__file__).parent.resolve()
    token_filename = f'token_{service_name}_{api_version}.pickle'
    token_path = tools_dir / token_filename
    credentials_path = tools_dir / 'credentials.json'

    logger.debug(f"Looking for token at: {token_path}")
    if token_path.exists():
        try:
            with open(token_path, 'rb') as token:
                loaded_creds = pickle.load(token)
                if isinstance(loaded_creds, Credentials):  # Basic check
                    creds = loaded_creds
                    logger.debug(f"Loaded credentials from {token_filename}")
                    if not all(s in creds.scopes for s in scopes):
                        logger.warning(
                            f"Token {token_filename} missing required scopes. Need {scopes}, have {creds.scopes}. Re-authenticating.")
                        creds = None
                else:
                    logger.warning(f"Invalid data in {token_filename}. Will re-authenticate.")
                    creds = None
        except (pickle.UnpicklingError, EOFError, FileNotFoundError, AttributeError, Exception) as e:
            logger.warning(f"Failed to load or validate {token_filename}: {e}. Will re-authenticate.")
            creds = None

    if not creds or not creds.valid:
        needs_reauth = True
        if creds and creds.expired and creds.refresh_token:
            logger.info(f"Attempting to refresh token for {service_name} scopes: {scopes}...")
            try:
                creds.refresh(Request())
                logger.info(f"{service_name} token refreshed successfully.")
                if not all(s in creds.scopes for s in scopes):
                    logger.warning(
                        f"Refreshed token still missing required scopes. Need {scopes}, have {creds.scopes}. Re-authenticating.")
                    needs_reauth = True
                else:
                    needs_reauth = False
            except Exception as e:
                logger.warning(f"Failed to refresh {service_name} token: {e}. Will re-authenticate.")
                creds = None

        if needs_reauth:
            logger.info(f"Performing OAuth flow for {service_name} with scopes: {scopes}")
            if not credentials_path.exists():
                logger.error(f"CRITICAL: credentials.json not found at {credentials_path}")
                return None, "Error: credentials.json file not found. Cannot authenticate."
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
                creds = flow.run_local_server(port=0)
                logger.info(
                    f"{service_name} authentication successful with scopes: {creds.scopes if creds else 'None'}")
            except Exception as e:
                logger.error(f"Error during {service_name} authentication flow: {e}", exc_info=True)
                return None, f"Error during {service_name} authentication flow: {str(e)}"
            if creds:  # Only save if authentication was successful
                try:
                    with open(token_path, 'wb') as token:
                        pickle.dump(creds, token)
                    logger.info(f"Saved new credentials to {token_filename}")
                except Exception as e:
                    logger.error(f"Failed to save {token_filename}: {e}", exc_info=True)

    if not creds:  # If creds are still None after all attempts
        return None, f"Failed to obtain valid credentials for {service_name}."

    try:
        service: Resource = build(service_name, api_version, credentials=creds, cache_discovery=False)
        logger.info(f"{service_name} service (v{api_version}) built successfully.")
        return service, None
    except HttpError as e:
        logger.error(f"HTTP error building {service_name} service: {e.resp.status} {e.content}", exc_info=True)
        error_content = e.content.decode('utf-8', 'ignore') if e.content else str(e)
        return None, f"Error building {service_name} service (HTTP {e.resp.status}): {error_content}"
    except Exception as e:
        logger.error(f"Error building {service_name} service: {str(e)}", exc_info=True)
        return None, f"Error building {service_name} service: {str(e)}"


# --- Command Parsing ---
def parse_calendar_tasks_command(command: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    command_data = {}
    pattern = r"(\w+)\s*:\s*(?:(?:'((?:[^'\\]|\\.)*)')|(?:\"((?:[^\"\\]|\\.)*)\")|([^;\s]*))"
    matches = re.findall(pattern, command)

    for key, sq_val, dq_val, non_quoted_val in matches:
        key = key.strip().lower()
        if sq_val:
            value = sq_val.replace("\\'", "'")
        elif dq_val:
            value = dq_val.replace('\\"', '"')
        else:
            value = non_quoted_val.strip()

        if value:
            command_data[key] = value
        elif key in command_data:
            command_data[key] = ""

    action = command_data.get('action')
    if not action: return None, "Error: Missing 'action:' in command string."
    action = action.lower()
    command_data['action'] = action

    error_msg = None
    try:
        def get_cmd_data(k, default=None):
            return command_data.get(k, default)

        def check_req(field):
            return field in command_data

        if action == 'list_events':
            if 'limit' in command_data: command_data['limit'] = int(get_cmd_data('limit'))
            if 'days' in command_data: command_data['days'] = int(get_cmd_data('days')); assert command_data['days'] > 0
        elif action == 'create_event':
            if not all(check_req(f) for f in ['summary', 'start', 'end']):
                error_msg = "Error: Missing required field(s) for create_event: summary, start, end."
            else:
                start_dt = datetime.datetime.fromisoformat(get_cmd_data('start', '').replace('Z', '+00:00'))
                end_dt = datetime.datetime.fromisoformat(get_cmd_data('end', '').replace('Z', '+00:00'))
                assert end_dt > start_dt
        elif action == 'update_event':
            if not check_req('event_id'):
                error_msg = "Error: Missing 'event_id' for update_event."
            elif not any(k in command_data for k in ['summary', 'start', 'end', 'description']):
                error_msg = "Error: update_event requires at least one field to update."
            else:
                start_str = get_cmd_data('start')
                end_str = get_cmd_data('end')
                start_dt = datetime.datetime.fromisoformat(start_str.replace('Z', '+00:00')) if start_str else None
                end_dt = datetime.datetime.fromisoformat(end_str.replace('Z', '+00:00')) if end_str else None
                if start_dt and end_dt: assert end_dt > start_dt
        elif action == 'delete_event':
            if not check_req('event_id'): error_msg = "Error: Missing 'event_id' for delete_event."
        elif action == 'list_tasklists':
            if 'limit' in command_data: command_data['limit'] = int(get_cmd_data('limit'))
        elif action == 'list_tasks':
            if not check_req('tasklist_id'): error_msg = "Error: Missing 'tasklist_id' for list_tasks."
            if 'limit' in command_data: command_data['limit'] = int(get_cmd_data('limit'))
            if 'show_completed' in command_data: command_data['show_completed'] = get_cmd_data('show_completed',
                                                                                               '').lower() == 'true'
        elif action == 'create_task':
            if not check_req('tasklist_id'):
                error_msg = "Error: Missing 'tasklist_id' for create_task."
            elif not check_req('title'):
                error_msg = "Error: Missing 'title' for create_task."
            elif 'due' in command_data:
                datetime.datetime.fromisoformat(get_cmd_data('due', '').replace('Z', '+00:00'))
        elif action == 'update_task':
            if not check_req('tasklist_id'):
                error_msg = "Error: Missing 'tasklist_id' for update_task."
            elif not check_req('task_id'):
                error_msg = "Error: Missing 'task_id' for update_task."
            elif not any(k in command_data for k in ['title', 'notes', 'due', 'status']):
                error_msg = "Error: update_task requires at least one field to update."
            else:
                if 'status' in command_data and get_cmd_data('status', '').lower() not in ['needsaction', 'completed']:
                    error_msg = "Error: Invalid 'status' value. Use 'needsAction' or 'completed'."
                if 'due' in command_data and get_cmd_data('due'):
                    datetime.datetime.fromisoformat(get_cmd_data('due', '').replace('Z', '+00:00'))
        elif action == 'delete_task':
            if not check_req('tasklist_id'):
                error_msg = "Error: Missing 'tasklist_id' for delete_task."
            elif not check_req('task_id'):
                error_msg = "Error: Missing 'task_id' for delete_task."
        elif action not in ACTION_SCOPES:
            valid_actions = list(ACTION_SCOPES.keys())
            error_msg = f"Error: Unknown or unsupported action '{action}'. Supported: {', '.join(valid_actions)}."

    except (ValueError, AssertionError, Exception) as e:
        error_msg = f"Error parsing parameters for action '{action}': {e}. Check format/values (use strict ISO 8601 dates)."

    if error_msg:
        logger.warning(f"Command parsing failed: {error_msg}. Command: '{command}'")
        return None, error_msg

    logger.info(f"Parsed calendar/tasks command: {command_data}")
    return command_data, None


# --- Formatting Helper Functions ---
def _format_datetime_str(iso_str: Optional[str], is_all_day: bool = False) -> str:
    if not iso_str: return "N/A"
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        tz_str = dt.strftime('%Z')
        tz_offset = dt.strftime('%z')
        if is_all_day: return dt.strftime('%Y-%m-%d')
        tz_display = f" {tz_str}" if tz_str and tz_str != tz_offset else f" {tz_offset}" if tz_offset else ""
        return dt.strftime('%Y-%m-%d %H:%M:%S') + tz_display
    except ValueError:
        return iso_str


def _format_event_details(event: Dict[str, Any]) -> str:
    summary = event.get('summary', 'No Title')
    start_data = event.get('start', {})
    end_data = event.get('end', {})
    start_str = start_data.get('dateTime', start_data.get('date'))
    end_str = end_data.get('dateTime', end_data.get('date'))
    is_all_day = 'date' in start_data and 'dateTime' not in start_data
    event_id = event.get('id', 'N/A')
    description = event.get('description', '')
    link = event.get('htmlLink', '')
    start_formatted = _format_datetime_str(start_str, is_all_day)
    end_formatted = _format_datetime_str(end_str, is_all_day)

    if is_all_day and end_str and start_str:
        try:
            end_dt_exclusive = datetime.date.fromisoformat(end_str)
            start_dt = datetime.date.fromisoformat(start_str)
            end_dt_inclusive = end_dt_exclusive - datetime.timedelta(days=1)
            if end_dt_inclusive >= start_dt:  # Use >= for single all-day events
                end_formatted = end_dt_inclusive.strftime('%Y-%m-%d') if end_dt_inclusive > start_dt else ""
            else:  # Should not happen if end date is after start date
                end_formatted = ""
        except ValueError:
            pass

    time_info = f"{start_formatted}"
    if end_formatted and not (
            is_all_day and start_formatted == end_formatted):  # Avoid "Date to Date" for single all-day
        time_info += f" to {end_formatted}"
    if is_all_day: time_info += " (All day)"

    details = f"Event: '{summary}'\n  Time: {time_info}\n"
    if description: details += f"  Desc: {description[:100]}{'...' if len(description) > 100 else ''}\n"
    details += f"  ID: {event_id}\n"
    if link: details += f"  Link: {link}\n"
    return details


def _format_task_details(task: Dict[str, Any]) -> str:
    title = task.get('title', 'Untitled Task')
    notes = task.get('notes', '')
    due_str = task.get('due')
    status = task.get('status', 'needsAction')
    task_id = task.get('id', 'N/A')
    due_info = f" (Due: {_format_datetime_str(due_str)})" if due_str else ""
    details = f"Task: '{title}' [{status}]{due_info}\n"
    if notes: details += f"  Notes: {notes[:100]}{'...' if len(notes) > 100 else ''}\n"
    details += f"  ID: {task_id}\n"
    return details


def _format_tasklist_details(tasklist: Dict[str, Any]) -> str:
    title = tasklist.get('title', 'Untitled List')
    list_id = tasklist.get('id', 'N/A')
    return f"List: '{title}' (ID: {list_id})\n"


# --- Internal API Call Functions ---
async def _list_events(service: Resource, params: Dict[str, Any]) -> str:
    try:
        limit = params.get('limit', 10)
        days_ahead = params.get('days')
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        time_min_iso = now_utc.isoformat()
        time_max_iso = (now_utc + datetime.timedelta(days=days_ahead)).isoformat() if days_ahead else None
        period_desc = f"next {limit} upcoming event(s)"
        if days_ahead: period_desc = f"event(s) for the next {days_ahead} days (limit {limit})"

        # Using getattr to appease linters about dynamic methods
        events_service = getattr(service, 'events')()
        request = events_service.list(
            calendarId='primary', timeMin=time_min_iso, timeMax=time_max_iso,
            maxResults=limit, singleEvents=True, orderBy='startTime'
        )
        events_result = request.execute()

        events = events_result.get('items', [])
        if not events: return "No upcoming events found matching the criteria."
        output = f"Found {len(events)} {period_desc}:\n---\n"
        output += "---\n".join([_format_event_details(event) for event in events])
        return output.strip()
    except HttpError as e:
        return f"Error listing events: {e.resp.status} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error listing events: {e}", exc_info=True); return f"Error listing events: {str(e)}"


async def _create_event(service: Resource, params: Dict[str, Any]) -> str:
    summary = params.get('summary', '')  # Use .get for safety, though parser should ensure
    start_iso = params.get('start', '')
    end_iso = params.get('end', '')
    description = params.get('description')
    try:
        start_dt = datetime.datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
        end_dt = datetime.datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
        is_all_day = 'T' not in start_iso and 'T' not in end_iso
        start_payload, end_payload = {}, {}

        if is_all_day:
            start_payload = {'date': start_dt.strftime('%Y-%m-%d')}
            end_payload = {'date': end_dt.strftime('%Y-%m-%d')}
        else:
            tz_start = start_dt.tzinfo or datetime.timezone.utc
            tz_end = end_dt.tzinfo or datetime.timezone.utc
            start_payload = {'dateTime': start_dt.isoformat(), 'timeZone': str(tz_start)}
            end_payload = {'dateTime': end_dt.isoformat(), 'timeZone': str(tz_end)}

        event_body = {'summary': summary, 'start': start_payload, 'end': end_payload}
        if description: event_body['description'] = description

        events_service = getattr(service, 'events')()
        request = events_service.insert(calendarId='primary', body=event_body)
        created_event = request.execute()
        return f"Successfully created event:\n{_format_event_details(created_event)}"
    except HttpError as e:
        return f"Error creating event: {e.resp.status} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error creating event: {e}", exc_info=True); return f"Error creating event: {str(e)}"


async def _update_event(service: Resource, params: Dict[str, Any]) -> str:
    event_id = params.get('event_id', '')
    try:
        events_service = getattr(service, 'events')()
        request_get = events_service.get(calendarId='primary', eventId=event_id)
        existing_event = request_get.execute()

        update_needed = False
        current_summary = existing_event.get('summary')
        new_summary = params.get('summary')
        if new_summary is not None and new_summary != current_summary:
            existing_event['summary'] = new_summary
            update_needed = True

        current_desc = existing_event.get('description')
        new_desc = params.get('description')
        if new_desc is not None and new_desc != current_desc:
            existing_event['description'] = new_desc
            update_needed = True

        start_iso, end_iso = params.get('start'), params.get('end')
        if start_iso or end_iso:
            current_start_data = existing_event.get('start', {})
            current_end_data = existing_event.get('end', {})
            current_start_str = current_start_data.get('dateTime', current_start_data.get('date'))
            current_end_str = current_end_data.get('dateTime', current_end_data.get('date'))

            new_start_iso = start_iso or current_start_str
            new_end_iso = end_iso or current_end_str

            if new_start_iso and new_end_iso:  # Ensure both are present if any is being changed
                new_start_dt = datetime.datetime.fromisoformat(new_start_iso.replace('Z', '+00:00'))
                new_end_dt = datetime.datetime.fromisoformat(new_end_iso.replace('Z', '+00:00'))
                assert new_end_dt > new_start_dt
                is_all_day = 'T' not in new_start_iso and 'T' not in new_end_iso
                start_payload_new, end_payload_new = {}, {}

                if is_all_day:
                    start_payload_new = {'date': new_start_dt.strftime('%Y-%m-%d')}
                    end_payload_new = {'date': new_end_dt.strftime('%Y-%m-%d')}
                else:
                    tz_start = new_start_dt.tzinfo or datetime.timezone.utc
                    tz_end = new_end_dt.tzinfo or datetime.timezone.utc
                    start_payload_new = {'dateTime': new_start_dt.isoformat(), 'timeZone': str(tz_start)}
                    end_payload_new = {'dateTime': new_end_dt.isoformat(), 'timeZone': str(tz_end)}

                if existing_event.get('start') != start_payload_new or existing_event.get('end') != end_payload_new:
                    existing_event['start'] = start_payload_new
                    existing_event['end'] = end_payload_new
                    update_needed = True
            else:  # If only one of start/end provided, this is an invalid state for this simplified update
                logger.warning("Update event called with only one of start/end time. Both are needed to change time.")

        if not update_needed: return f"No changes detected for event ID {event_id}. Update aborted."

        request_update = events_service.update(calendarId='primary', eventId=event_id, body=existing_event)
        updated_event = request_update.execute()
        return f"Successfully updated event:\n{_format_event_details(updated_event)}"
    except HttpError as e:
        if e.resp and e.resp.status == 404: return f"Error: Event ID '{event_id}' not found for update."
        return f"Error updating event {event_id}: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error updating event {event_id}: {e}",
                     exc_info=True); return f"Error updating event {event_id}: {str(e)}"


async def _delete_event(service: Resource, params: Dict[str, Any]) -> str:
    event_id = params.get('event_id', '')
    try:
        events_service = getattr(service, 'events')()
        request = events_service.delete(calendarId='primary', eventId=event_id)
        request.execute()
        return f"Successfully deleted event with ID: {event_id}."
    except HttpError as e:
        if e.resp and e.resp.status == 404: return f"Error: Event ID '{event_id}' not found for deletion."
        return f"Error deleting event {event_id}: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error deleting event {event_id}: {e}",
                     exc_info=True); return f"Error deleting event {event_id}: {str(e)}"


async def _list_tasklists(service: Resource, params: Dict[str, Any]) -> str:
    try:
        limit = params.get('limit', 20)
        tasklists_service = getattr(service, 'tasklists')()
        request = tasklists_service.list(maxResults=limit)
        results = request.execute()
        items = results.get('items', [])
        if not items: return "No task lists found."
        output = f"Found {len(items)} task list(s):\n---\n"
        output += "---\n".join([_format_tasklist_details(item) for item in items])
        return output.strip() + "\n(Use the ID with task actions like list_tasks, create_task etc.)"
    except HttpError as e:
        return f"Error listing task lists: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error listing task lists: {e}",
                     exc_info=True); return f"Error listing task lists: {str(e)}"


async def _list_tasks(service: Resource, params: Dict[str, Any]) -> str:
    tasklist_id = params.get('tasklist_id', '')
    limit = params.get('limit', 20)
    show_completed = params.get('show_completed', False)
    status_desc = "all" if show_completed else "active"
    try:
        tasks_service = getattr(service, 'tasks')()
        request = tasks_service.list(
            tasklist=tasklist_id, maxResults=limit,
            showCompleted=show_completed, showHidden=False
        )
        results = request.execute()
        items = results.get('items', [])
        if not items: return f"No {status_desc} tasks found in task list ID {tasklist_id}."
        output = f"Found {len(items)} {status_desc} task(s) in list ID {tasklist_id}:\n---\n"
        output += "---\n".join([_format_task_details(item) for item in items])
        return output.strip()
    except HttpError as e:
        if e.resp and e.resp.status == 404: return f"Error: Task list ID '{tasklist_id}' not found."
        return f"Error listing tasks for {tasklist_id}: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error listing tasks for {tasklist_id}: {e}",
                     exc_info=True); return f"Error listing tasks: {str(e)}"


async def _create_task(service: Resource, params: Dict[str, Any]) -> str:
    tasklist_id = params.get('tasklist_id', '')
    title = params.get('title', '')
    notes, due_iso = params.get('notes'), params.get('due')
    try:
        task_body = {'title': title, 'status': 'needsAction'}
        if notes: task_body['notes'] = notes
        if due_iso: task_body['due'] = due_iso

        tasks_service = getattr(service, 'tasks')()
        request = tasks_service.insert(tasklist=tasklist_id, body=task_body)
        created_task = request.execute()
        return f"Successfully created task:\n{_format_task_details(created_task)}"
    except HttpError as e:
        if e.resp and e.resp.status == 404: return f"Error: Task list ID '{tasklist_id}' not found."
        return f"Error creating task in list {tasklist_id}: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error creating task: {e}", exc_info=True); return f"Error creating task: {str(e)}"


async def _update_task(service: Resource, params: Dict[str, Any]) -> str:
    tasklist_id = params.get('tasklist_id', '')
    task_id = params.get('task_id', '')
    try:
        update_body, updated_fields = {}, []
        if 'title' in params: update_body['title'] = params['title']; updated_fields.append('title')
        if 'notes' in params: update_body['notes'] = params['notes']; updated_fields.append('notes')
        if 'status' in params:
            update_body['status'] = params.get('status', '').lower()
            updated_fields.append('status')
            if update_body['status'] == 'needsaction': update_body[
                'completed'] = None  # API needs 'completed' to be null
        if 'due' in params:
            update_body['due'] = params['due'] if params.get('due') else None
            updated_fields.append('due')

        if not updated_fields: return f"No changes specified for updating task {task_id}."

        tasks_service = getattr(service, 'tasks')()
        request = tasks_service.patch(tasklist=tasklist_id, task=task_id, body=update_body)
        updated_task = request.execute()
        return f"Successfully updated task:\n{_format_task_details(updated_task)}"
    except HttpError as e:
        if e.resp and e.resp.status == 404: return f"Error: Task or Task List not found for update (Task: {task_id}, List: {tasklist_id})."
        return f"Error updating task {task_id}: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error updating task {task_id}: {e}",
                     exc_info=True); return f"Error updating task: {str(e)}"


async def _delete_task(service: Resource, params: Dict[str, Any]) -> str:
    tasklist_id = params.get('tasklist_id', '')
    task_id = params.get('task_id', '')
    try:
        tasks_service = getattr(service, 'tasks')()
        request = tasks_service.delete(tasklist=tasklist_id, task=task_id)
        request.execute()  # Returns 204 No Content on success
        return f"Successfully deleted task ID: {task_id} from list ID: {tasklist_id}."
    except HttpError as e:
        if e.resp and e.resp.status == 404: return f"Error: Task or Task List not found for deletion (Task: {task_id}, List: {tasklist_id})."
        return f"Error deleting task {task_id}: {e.resp.status if e.resp else 'Unknown'} - {e.content.decode('utf-8', 'ignore') if e.content else str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error deleting task {task_id}: {e}",
                     exc_info=True); return f"Error deleting task: {str(e)}"


# --- Main Tool Function ---
async def manage_calendar_tasks(args: Dict[str, Any]) -> str:
    command = args.get("command_string")
    if not command or not isinstance(command, str):
        return "Error: Missing or invalid 'command_string' argument for calendar tool."

    loop = asyncio.get_running_loop()
    try:
        parsed_params, error = parse_calendar_tasks_command(command)
        if error: return error
        if not parsed_params: return "Error: Failed to parse command."  # Should not happen if error is None

        action = parsed_params.get('action', '')  # Ensure action is present
        service_name = 'calendar' if 'event' in action else 'tasks'
        api_version = 'v3' if service_name == 'calendar' else 'v1'
        scopes = ACTION_SCOPES.get(action)
        if not scopes: return f"Error: Internal error - Action '{action}' has no defined scopes."

        logger.info(f"Requesting {service_name} service (v{api_version}) with scopes: {scopes} for action '{action}'")
        service, auth_error = await loop.run_in_executor(
            None, lambda: _get_google_service_sync(service_name, api_version, scopes)
        )
        if auth_error or not service: return auth_error or f"Error: Could not get Google {service_name.capitalize()} service."

        action_function_map = {
            'list_events': _list_events, 'create_event': _create_event,
            'update_event': _update_event, 'delete_event': _delete_event,
            'list_tasklists': _list_tasklists, 'list_tasks': _list_tasks,
            'create_task': _create_task, 'update_task': _update_task,
            'delete_task': _delete_task,
        }
        if action in action_function_map:
            result = await action_function_map[action](service, parsed_params)
        else:
            result = f"Error: Action '{action}' is not supported. Supported: {', '.join(ACTION_SCOPES.keys())}."
        return result
    except Exception as e:
        logger.error(f"Unexpected error in manage_calendar_tasks tool: {str(e)}", exc_info=True)
        return f"An unexpected error occurred processing the calendar/tasks command: {type(e).__name__}"

