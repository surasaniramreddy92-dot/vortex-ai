"""Gmail-backed email: check unread mail, draft replies, send only on
explicit confirmation.

Mirrors browser.py's lazy-construction contract exactly: MailAgent() at
construction time does nothing but store config - no network call, no
OAuth flow, no import of the google-api packages even, so constructing a
Vortex() (which every existing test does) stays exactly as safe as it was
before this module existed, whether or not the `mail` extra is even
installed. The actual Gmail API client, and the one-time OAuth consent
flow it may need to run in a real browser, only happen inside
_ensure_started(), on first real use.

Every google-api-python-client/google-auth import is local to
_ensure_started() (not at module level) for the same reason browser.py
imports playwright locally inside _ensure_started() instead of at the top
of the file - `from .mail import MailAgent` must not raise just because
the `mail` extra isn't installed; only actually trying to check/reply to
email should.

Least-privilege scopes: gmail.readonly + gmail.send, not the broader
gmail.modify - VORTEX never needs to delete, archive, or label anything,
only read and send, so the OAuth consent screen a user approves genuinely
reflects what this integration can do.
"""
import base64
import os
from email.mime.text import MIMEText

_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
]


def _get_credentials(credentials_path, token_path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, _SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Opens a real browser window for one-time consent - only
            # reached the first time, or if the cached token is unusable
            # and unrefreshable (e.g. access revoked).
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, _SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())
    return creds


def _extract_plain_text(payload):
    """Gmail messages are MIME trees that can nest text/plain and text/html
    parts side by side - walk for the first text/plain part rather than
    assuming payload['body'] is populated directly (only true for simple,
    non-multipart messages)."""
    if payload.get('mimeType') == 'text/plain' and payload.get('body', {}).get('data'):
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
    for part in payload.get('parts', []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return ''


class MailAgent:
    def __init__(self, credentials_path, token_path, max_results=5):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.max_results = max_results
        self._service = None

    def _ensure_started(self):
        if self._service is not None:
            return
        from googleapiclient.discovery import build
        creds = _get_credentials(self.credentials_path, self.token_path)
        self._service = build('gmail', 'v1', credentials=creds)

    def list_unread(self):
        """Returns [{'id', 'sender', 'subject', 'snippet'}, ...], most
        recent first, capped at self.max_results."""
        self._ensure_started()
        results = self._service.users().messages().list(
            userId='me', labelIds=['UNREAD'], maxResults=self.max_results).execute()
        out = []
        for m in results.get('messages', []):
            msg = self._service.users().messages().get(
                userId='me', id=m['id'], format='metadata',
                metadataHeaders=['From', 'Subject']).execute()
            headers = {h['name']: h['value'] for h in msg['payload']['headers']}
            out.append({
                'id': m['id'],
                'sender': headers.get('From', '(unknown sender)'),
                'subject': headers.get('Subject', '(no subject)'),
                'snippet': msg.get('snippet', ''),
            })
        return out

    def get_email_body(self, message_id):
        """Returns the plain-text body of one message (empty string if it
        genuinely has none - an HTML-only message with no text/plain part,
        which does happen)."""
        self._ensure_started()
        msg = self._service.users().messages().get(
            userId='me', id=message_id, format='full').execute()
        return _extract_plain_text(msg['payload'])

    def send_reply(self, message_id, body):
        """Sends `body` as a reply in the same thread as message_id, to
        whoever sent it, with a Re: subject and proper In-Reply-To/
        References headers so mail clients thread it correctly."""
        self._ensure_started()
        original = self._service.users().messages().get(
            userId='me', id=message_id, format='metadata',
            metadataHeaders=['From', 'Subject', 'Message-ID']).execute()
        headers = {h['name']: h['value'] for h in original['payload']['headers']}
        subject = headers.get('Subject', '')
        if not subject.lower().startswith('re:'):
            subject = f'Re: {subject}'
        original_message_id_header = headers.get('Message-ID', '')

        mime_msg = MIMEText(body)
        mime_msg['To'] = headers.get('From', '')
        mime_msg['Subject'] = subject
        if original_message_id_header:
            mime_msg['In-Reply-To'] = original_message_id_header
            mime_msg['References'] = original_message_id_header

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode('ascii')
        self._service.users().messages().send(
            userId='me', body={'raw': raw, 'threadId': original.get('threadId')}).execute()
