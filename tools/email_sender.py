import re
import base64
import pickle
import asyncio
import logging
from typing import Dict, Any, List
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from pathlib import Path


from .file_system import _resolve_and_validate_path, AI_WORKSPACE_DIR

logger = logging.getLogger(__name__)

# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.send',
          'https://www.googleapis.com/auth/gmail.readonly']


def _get_gmail_service_sync():
    """Synchronous function to get authenticated Gmail API service."""
    creds = None
    tools_dir = Path(__file__).parent.resolve()
    token_path = tools_dir / 'token.pickle'
    credentials_path = tools_dir / 'credentials.json'

    if token_path.exists():
        try:
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
        except (pickle.UnpicklingError, EOFError, FileNotFoundError) as e:
             logger.warning(f"Failed to load token.pickle: {e}. Will re-authenticate.")
             creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Gmail token refreshed.")
            except Exception as e:
                logger.warning(f"Failed to refresh token: {e}. Will re-authenticate.")
                creds = None
        else:
            if not credentials_path.exists():
                logger.error(f"CRITICAL: credentials.json not found at {credentials_path}")
                return None, "Error: credentials.json file not found in tools directory. Cannot authenticate."

            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)
                logger.info("Gmail authentication successful.")
            except Exception as e:
                 logger.error(f"Error during Gmail authentication flow: {e}", exc_info=True)
                 return None, f"Error during authentication flow: {str(e)}"

        try:
            with open(token_path, 'wb') as token:
                pickle.dump(creds, token)
        except Exception as e:
            logger.error(f"Failed to save token.pickle: {e}", exc_info=True)

    try:
        service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail service built successfully.")
        return service, None
    except Exception as e:
        logger.error(f"Error building Gmail service: {str(e)}", exc_info=True)
        return None, f"Error building Gmail service: {str(e)}"


def parse_email_command(command: str) -> Dict[str, Any]:
    """Parse the email command string (key:value;) into components."""
    email_data = {
        "to": [], "cc": [], "bcc": [], "subject": "", "body": "",
        "raw_attachments": [],
        "read": False, "query": "", "limit": 5
    }
    pattern = r"(\w+)\s*:\s*((?:[^;']*(?:'(?:\\.|[^'])*')?)*)"
    matches = re.findall(pattern, command)

    for key, value in matches:
        key = key.strip().lower()
        value = value.strip().strip("'")

        if not value: continue

        if key == 'to':
            email_data['to'] = [email.strip() for email in value.split(',')]
        elif key == 'cc':
            email_data['cc'] = [email.strip() for email in value.split(',')]
        elif key == 'bcc':
            email_data['bcc'] = [email.strip() for email in value.split(',')]
        elif key == 'subject':
            email_data['subject'] = value
        elif key == 'body':
            email_data['body'] = value
        elif key == 'attach':
            email_data['raw_attachments'] = [path.strip() for path in value.split(',')]
        elif key == 'read' and value.lower() == 'true':
            email_data['read'] = True
        elif key == 'query':
            email_data['query'] = value
        elif key == 'limit' and value.isdigit():
            email_data['limit'] = int(value)

    return email_data


def create_message_with_attachments(
    sender: str,
    to: List[str],
    cc: List[str],
    bcc: List[str],
    subject: str,
    body: str,
    validated_attachments: List[Path]
) -> Dict[str, Any]:
    """Create a message with attachments using validated paths for the Gmail API."""
    message = MIMEMultipart()
    message['from'] = sender
    message['to'] = ', '.join(to)
    if cc: message['cc'] = ', '.join(cc)
    if bcc: message['bcc'] = ', '.join(bcc)
    message['subject'] = subject

    message.attach(MIMEText(body, 'plain', 'utf-8'))

    for file_path_obj in validated_attachments:
        try:
            with open(file_path_obj, 'rb') as file:
                part = MIMEApplication(file.read(), Name=file_path_obj.name)
            part['Content-Disposition'] = f'attachment; filename="{file_path_obj.name}"'
            message.attach(part)
            logger.info(f"Attached file: {file_path_obj.name}")
        except FileNotFoundError:
             logger.error(f"Attachment file not found during creation (unexpected): {file_path_obj}")
             raise Exception(f"Error attaching file {file_path_obj.name}: File not found (unexpected).")
        except Exception as e:
            logger.error(f"Error reading or attaching file {file_path_obj}: {str(e)}", exc_info=True)
            raise Exception(f"Error attaching file {file_path_obj.name}: {str(e)}")

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {'raw': raw_message}

# --- Email Reading ---
def _read_emails_sync(service, query: str, limit: int = 5) -> str:
    """Synchronous function to read emails from Gmail based on query."""
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=limit).execute()
        messages = results.get('messages', [])

        if not messages:
            return f"No emails found matching query: {query}"

        email_summaries = []
        fetch_limit = min(len(messages), limit, 10)
        logger.info(f"Found {len(messages)} emails matching query, fetching details for {fetch_limit}.")

        for message_info in messages[:fetch_limit]:
            try:
                msg = service.users().messages().get(userId='me', id=message_info['id'], format='metadata', metadataHeaders=['Subject', 'From', 'Date']).execute()
                headers = msg.get('payload', {}).get('headers', [])
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
                date = next((h['value'] for h in headers if h['name'].lower() == 'date'), 'Unknown Date')
                snippet = msg.get('snippet', 'No preview available.')
                snippet = snippet.replace('\r\n', ' ').replace('\n', ' ').strip()
                snippet = (snippet[:150] + '...') if len(snippet) > 150 else snippet
                email_summaries.append(f"From: {sender}\nDate: {date}\nSubject: {subject}\nPreview: {snippet}\n")
            except Exception as detail_err:
                 logger.warning(f"Could not fetch/parse details for message ID {message_info.get('id')}: {detail_err}")
                 email_summaries.append(f"[Could not fetch details for one email]\n")


        output = f"Found {len(messages)} email(s) matching query '{query}'. Summaries for the first {len(email_summaries)}:\n\n" + "\n---\n".join(email_summaries)
        if len(messages) > len(email_summaries):
            output += f"\n[Note: More emails found but only {len(email_summaries)} summaries are shown.]"
        return output

    except Exception as e:
        logger.error(f"Error reading emails with query '{query}': {str(e)}", exc_info=True)
        return f"Error reading emails: {str(e)}"

# --- Main Tool Function ---
async def send_email(args: Dict[str, Any]) -> str:
    """
    (Async) Sends or reads emails using the Gmail API based on a command string.

    Args:
        args (Dict[str, Any]): A dictionary containing the arguments.
                               Expected key: 'command_string' with the key:value; format.

    Returns:
        str: Result of the email operation (success message or error).
    """
    loop = asyncio.get_running_loop()

    command = args.get("command_string")
    if not command or not isinstance(command, str):
        return "Error: Missing or invalid 'command_string' argument for email tool."

    try:
        email_data = parse_email_command(command)
        service, error = await loop.run_in_executor(None, _get_gmail_service_sync)
        if not service:
            return error

        if email_data['read']:
            query = email_data['query']
            limit = email_data['limit']
            if not query:
                return "Error: 'query:' key is required when 'read:true' is specified in command_string."
            logger.info(f"Reading emails with query: '{query}', limit: {limit}")
            result = await loop.run_in_executor(None, lambda: _read_emails_sync(service, query, limit))
            return result

        if not email_data['to']:
            return "Error: No recipients specified in command_string. Use 'to:email@example.com'"

        validated_attachment_paths: List[Path] = []
        if email_data['raw_attachments']:
             if AI_WORKSPACE_DIR is None:
                 return "Error: Cannot process attachments because AI Workspace directory is not available."
             for rel_path_str in email_data['raw_attachments']:
                 validated_path = _resolve_and_validate_path(rel_path_str, AI_WORKSPACE_DIR)
                 if not validated_path:
                     return f"Error: Attachment path '{rel_path_str}' is invalid or outside the allowed ai_workspace."
                 if not validated_path.is_file():
                     return f"Error: Attachment path '{rel_path_str}' does not point to a file in the ai_workspace."
                 validated_attachment_paths.append(validated_path)
                 logger.info(f"Attachment path validated: {validated_path}")

        try:
             user_info = await loop.run_in_executor(None, lambda: service.users().getProfile(userId='me').execute())
             sender_email = user_info['emailAddress']
        except Exception as e:
             logger.error(f"Failed to get user profile (sender email): {e}", exc_info=True)
             return f"Error: Could not retrieve sender email address from Gmail profile: {str(e)}"

        try:
             logger.info(f"Preparing email to: {email_data['to']} with {len(validated_attachment_paths)} attachments.")
             message_body = await loop.run_in_executor(
                 None,
                 lambda: create_message_with_attachments(
                     sender_email, email_data['to'], email_data['cc'], email_data['bcc'],
                     email_data['subject'], email_data['body'], validated_attachment_paths
                 )
             )
             logger.info("Sending email via Gmail API...")
             send_response = await loop.run_in_executor(
                 None,
                 lambda: service.users().messages().send(userId='me', body=message_body).execute()
             )
             logger.info(f"Email sent successfully. Response ID: {send_response.get('id')}")
        except Exception as e:
             logger.error(f"Error during email creation or sending: {e}", exc_info=True)
             return f"Error creating or sending email: {str(e)}"

        recipient_count = len(email_data['to'])
        cc_count = len(email_data['cc'])
        attachment_count = len(validated_attachment_paths)
        success_msg = f"Email sent successfully via {sender_email} to {recipient_count} recipient(s)"
        if cc_count > 0: success_msg += f", {cc_count} CC recipient(s)"
        if attachment_count > 0: success_msg += f" with {attachment_count} attachment(s) from the workspace"
        success_msg += "."
        return success_msg

    except Exception as e:
        logger.error(f"Unexpected error in send_email tool: {str(e)}", exc_info=True)
        return f"Error processing email command: {str(e)}"