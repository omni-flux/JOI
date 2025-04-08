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

# Import necessary components from file_system for validation
from .file_system import _resolve_and_validate_path, AI_WORKSPACE_DIR

logger = logging.getLogger(__name__)

# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.send',
          'https://www.googleapis.com/auth/gmail.readonly']

# --- Authentication ---

def _get_gmail_service_sync():
    """Synchronous function to get authenticated Gmail API service."""
    creds = None
    # Construct paths relative to this file's location
    tools_dir = Path(__file__).parent.resolve()
    token_path = tools_dir / 'token.pickle'
    credentials_path = tools_dir / 'credentials.json'

    if token_path.exists():
        try:
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
        except (pickle.UnpicklingError, EOFError, FileNotFoundError) as e:
             logger.warning(f"Failed to load token.pickle: {e}. Will re-authenticate.")
             creds = None # Force re-authentication


    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Gmail token refreshed.")
            except Exception as e:
                logger.warning(f"Failed to refresh token: {e}. Will re-authenticate.")
                creds = None # Force re-authentication
        else:
            if not credentials_path.exists():
                logger.error(f"CRITICAL: credentials.json not found at {credentials_path}")
                return None, "Error: credentials.json file not found in tools directory. Cannot authenticate."

            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                # Run flow in a way that doesn't block asyncio loop if possible
                # For simplicity here, assuming it's run initially or handled appropriately
                creds = flow.run_local_server(port=0)
                logger.info("Gmail authentication successful.")
            except Exception as e:
                 logger.error(f"Error during Gmail authentication flow: {e}", exc_info=True)
                 return None, f"Error during authentication flow: {str(e)}"

        # Save the credentials for the next run
        try:
            with open(token_path, 'wb') as token:
                pickle.dump(creds, token)
        except Exception as e:
            logger.error(f"Failed to save token.pickle: {e}", exc_info=True)
            # Continue anyway, as authentication might have succeeded for this session


    try:
        service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail service built successfully.")
        return service, None
    except Exception as e:
        logger.error(f"Error building Gmail service: {str(e)}", exc_info=True)
        return None, f"Error building Gmail service: {str(e)}"

# --- Command Parsing ---

def parse_email_command(command: str) -> Dict[str, Any]:
    """Parse the email command string into components. Stores raw attachment paths."""
    email_data = {
        "to": [], "cc": [], "bcc": [], "subject": "", "body": "",
        "raw_attachments": [], # Store raw paths provided by AI here
        "read": False, "query": "", "limit": 5
    }
    # Use simplified regex to find key:value pairs separated by ';'
    # Allows values to contain ':' but not at the start after a ';'
    pattern = r"(\w+)\s*:\s*((?:[^;']*(?:'(?:\\.|[^'])*')?)*)"
    matches = re.findall(pattern, command)

    for key, value in matches:
        key = key.strip().lower()
        value = value.strip().strip("'") # Remove potential wrapping quotes

        if not value: continue # Skip empty values

        if key == 'to':
            email_data['to'] = [email.strip() for email in value.split(',')]
        elif key == 'cc':
            email_data['cc'] = [email.strip() for email in value.split(',')]
        elif key == 'bcc':
            email_data['bcc'] = [email.strip() for email in value.split(',')]
        elif key == 'subject':
            email_data['subject'] = value
        elif key == 'body':
            email_data['body'] = value # Keep body as is, including newlines potentially
        elif key == 'attach':
            # Store raw paths, validation happens later
            email_data['raw_attachments'] = [path.strip() for path in value.split(',')]
        elif key == 'read' and value.lower() == 'true':
            email_data['read'] = True
        elif key == 'query':
            email_data['query'] = value
        elif key == 'limit' and value.isdigit():
            email_data['limit'] = int(value)

    return email_data

# --- Email Creation ---
# Takes validated Path objects for attachments
def create_message_with_attachments(
    sender: str,
    to: List[str],
    cc: List[str],
    bcc: List[str],
    subject: str,
    body: str,
    validated_attachments: List[Path] # Changed from List[str] to List[Path]
) -> Dict[str, Any]:
    """Create a message with attachments using validated paths for the Gmail API."""
    message = MIMEMultipart()
    message['from'] = sender
    message['to'] = ', '.join(to)
    if cc: message['cc'] = ', '.join(cc)
    if bcc: message['bcc'] = ', '.join(bcc) # Note: BCC usually handled by API, not header
    message['subject'] = subject

    message.attach(MIMEText(body, 'plain', 'utf-8')) # Specify plain text and utf-8

    for file_path_obj in validated_attachments: # Iterate over Path objects
        try:
            # Open file using the validated Path object
            with open(file_path_obj, 'rb') as file:
                part = MIMEApplication(file.read(), Name=file_path_obj.name)
            part['Content-Disposition'] = f'attachment; filename="{file_path_obj.name}"'
            message.attach(part)
            logger.info(f"Attached file: {file_path_obj.name}")
        except FileNotFoundError:
             # Should not happen if validation worked, but good to have a catch
             logger.error(f"Attachment file not found during creation (unexpected): {file_path_obj}")
             raise Exception(f"Error attaching file {file_path_obj.name}: File not found (unexpected).")
        except Exception as e:
            logger.error(f"Error reading or attaching file {file_path_obj}: {str(e)}", exc_info=True)
            raise Exception(f"Error attaching file {file_path_obj.name}: {str(e)}")

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {'raw': raw_message}

# --- Email Reading (Sync blocking call) ---
def _read_emails_sync(service, query: str, limit: int = 5) -> str:
    """Synchronous function to read emails from Gmail based on query."""
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=limit).execute()
        messages = results.get('messages', [])

        if not messages:
            return f"No emails found matching query: {query}"

        email_summaries = []
        # Limit fetching details to avoid too much output/time
        fetch_limit = min(len(messages), limit, 10) # Fetch details for up to 10 messages
        logger.info(f"Found {len(messages)} emails matching query, fetching details for {fetch_limit}.")

        for message_info in messages[:fetch_limit]:
            msg = service.users().messages().get(userId='me', id=message_info['id'], format='metadata', metadataHeaders=['Subject', 'From', 'Date']).execute()

            headers = msg.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
            date = next((h['value'] for h in headers if h['name'].lower() == 'date'), 'Unknown Date')

            snippet = msg.get('snippet', 'No preview available.')
            # Clean up snippet
            snippet = snippet.replace('\r\n', ' ').replace('\n', ' ').strip()
            snippet = (snippet[:150] + '...') if len(snippet) > 150 else snippet


            email_summaries.append(f"From: {sender}\nDate: {date}\nSubject: {subject}\nPreview: {snippet}\n")

        output = f"Found {len(messages)} email(s) matching query '{query}'. Summaries for the first {len(email_summaries)}:\n\n" + "\n---\n".join(email_summaries)
        if len(messages) > len(email_summaries):
            output += f"\n[Note: More emails found but only {len(email_summaries)} summaries are shown.]"
        return output

    except Exception as e:
        logger.error(f"Error reading emails with query '{query}': {str(e)}", exc_info=True)
        return f"Error reading emails: {str(e)}"

# --- Main Tool Function (Async) ---
async def send_email(command: str) -> str:
    """
    (Async) Send or read emails using the Gmail API via sync blocking calls in executor.
    Attachments MUST be specified via relative paths within the ai_workspace.

    Format for sending:
    to:recipient@example.com; subject:Hello; body:Email content; attach:relative/path/in/workspace.txt

    Format for reading:
    read:true; query:from:someone@example.com; limit:10
    """
    loop = asyncio.get_running_loop()

    try:
        email_data = parse_email_command(command)

        # --- Get Gmail Service (run sync function in thread pool) ---
        service, error = await loop.run_in_executor(None, _get_gmail_service_sync)
        if not service:
            return error

        # --- Reading Emails ---
        if email_data['read']:
            query = email_data['query']
            limit = email_data['limit']
            if not query:
                return "Error: 'query:' key is required when 'read:true' is specified."
            logger.info(f"Reading emails with query: '{query}', limit: {limit}")
            # Run sync read function in thread pool
            result = await loop.run_in_executor(None, lambda: _read_emails_sync(service, query, limit))
            return result

        # --- Sending Emails ---
        if not email_data['to']:
            return "Error: No recipients specified. Use 'to:email@example.com'"

        # --- Attachment Validation (CRITICAL SECURITY STEP) ---
        validated_attachment_paths: List[Path] = []
        if email_data['raw_attachments']:
             if AI_WORKSPACE_DIR is None:
                 return "Error: Cannot process attachments because AI Workspace directory is not available."

             for rel_path_str in email_data['raw_attachments']:
                 logger.info(f"Validating attachment path: '{rel_path_str}'")
                 validated_path = _resolve_and_validate_path(rel_path_str, AI_WORKSPACE_DIR)

                 if not validated_path:
                     logger.warning(f"Attachment path rejected (invalid/outside workspace): {rel_path_str}")
                     return f"Error: Attachment path '{rel_path_str}' is invalid or outside the allowed ai_workspace."
                 if not validated_path.is_file():
                     logger.warning(f"Attachment path rejected (not a file): {validated_path}")
                     return f"Error: Attachment path '{rel_path_str}' does not point to a file in the ai_workspace."

                 validated_attachment_paths.append(validated_path)
                 logger.info(f"Attachment path validated: {validated_path}")
        # --- End Attachment Validation ---


        # --- Get Sender Email (run sync function in thread pool) ---
        try:
             user_info = await loop.run_in_executor(None, lambda: service.users().getProfile(userId='me').execute())
             sender_email = user_info['emailAddress']
        except Exception as e:
             logger.error(f"Failed to get user profile (sender email): {e}", exc_info=True)
             return f"Error: Could not retrieve sender email address from Gmail profile: {str(e)}"


        # --- Create and Send Message (run sync functions in thread pool) ---
        try:
             logger.info(f"Preparing email to: {email_data['to']} with {len(validated_attachment_paths)} attachments.")
             # create_message is CPU-bound (MIME handling), might be okay outside executor,
             # but keep it sync for now with the rest of the Google API logic flow.
             message_body = await loop.run_in_executor(
                 None,
                 lambda: create_message_with_attachments(
                     sender_email,
                     email_data['to'],
                     email_data['cc'],
                     email_data['bcc'],
                     email_data['subject'],
                     email_data['body'],
                     validated_attachment_paths # Pass validated Path objects
                 )
             )

             logger.info("Sending email via Gmail API...")
             send_response = await loop.run_in_executor(
                 None,
                 lambda: service.users().messages().send(userId='me', body=message_body).execute()
             )
             logger.info(f"Email sent successfully. Response ID: {send_response.get('id')}")

        except Exception as e:
             # Catch errors from create_message or send API call
             logger.error(f"Error during email creation or sending: {e}", exc_info=True)
             return f"Error creating or sending email: {str(e)}"


        # --- Prepare Success Message ---
        recipient_count = len(email_data['to'])
        cc_count = len(email_data['cc'])
        bcc_count = len(email_data['bcc']) # Note: BCC recipients aren't usually confirmed by API response
        attachment_count = len(validated_attachment_paths)

        success_msg = f"Email sent successfully via {sender_email} to {recipient_count} recipient(s)"
        if cc_count > 0: success_msg += f", {cc_count} CC recipient(s)"
        # if bcc_count > 0: success_msg += f", {bcc_count} BCC recipient(s)" # Avoid confirming BCC
        if attachment_count > 0: success_msg += f" with {attachment_count} attachment(s) from the workspace"
        success_msg += "."

        return success_msg

    except Exception as e:
        # Catch-all for unexpected errors in the async function itself
        logger.error(f"Unexpected error in send_email tool: {str(e)}", exc_info=True)
        return f"Error processing email command: {str(e)}"

