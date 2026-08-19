"""Unit tests for src/vortex/mail.py.

MailAgent's real Gmail API client is never constructed here - _ensure_started
is monkeypatched to inject a fake `_service` directly (a small chain of Mock
objects matching the exact googleapiclient call shape MailAgent uses:
.users().messages().list/.get/.send().execute()), so these tests exercise
MailAgent's own logic (which fields it reads, how it builds requests, how it
threads a reply) without ever running the OAuth flow or importing the real
google-api packages.
"""
import base64
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, __file__.rsplit('tests', 1)[0] + 'src')

from vortex.mail import MailAgent, _extract_plain_text


def _b64(text):
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii')


# ---------- _extract_plain_text (pure) ----------

def test_extract_plain_text_simple_message():
    payload = {'mimeType': 'text/plain', 'body': {'data': _b64('hello world')}}
    assert _extract_plain_text(payload) == 'hello world'


def test_extract_plain_text_multipart_finds_the_plain_part():
    payload = {
        'mimeType': 'multipart/alternative',
        'parts': [
            {'mimeType': 'text/html', 'body': {'data': _b64('<p>hi</p>')}},
            {'mimeType': 'text/plain', 'body': {'data': _b64('hi')}},
        ],
    }
    assert _extract_plain_text(payload) == 'hi'


def test_extract_plain_text_nested_multipart():
    payload = {
        'mimeType': 'multipart/mixed',
        'parts': [{
            'mimeType': 'multipart/alternative',
            'parts': [{'mimeType': 'text/plain', 'body': {'data': _b64('nested body')}}],
        }],
    }
    assert _extract_plain_text(payload) == 'nested body'


def test_extract_plain_text_html_only_returns_empty_string():
    payload = {'mimeType': 'text/html', 'body': {'data': _b64('<p>only html</p>')}}
    assert _extract_plain_text(payload) == ''


# ---------- MailAgent construction (lazy, no side effects) ----------

def test_construction_does_not_touch_the_network_or_filesystem(tmp_path):
    """Matches browser.py's BrowserAgent contract exactly - constructing
    this must be as safe as constructing everything else Vortex() builds."""
    agent = MailAgent(
        credentials_path=str(tmp_path / 'nonexistent_credentials.json'),
        token_path=str(tmp_path / 'nonexistent_token.json'), max_results=5)
    assert agent._service is None


@pytest.fixture
def agent_with_fake_service(monkeypatch):
    agent = MailAgent(credentials_path='unused', token_path='unused', max_results=5)
    fake_service = MagicMock()
    monkeypatch.setattr(agent, '_ensure_started', lambda: setattr(agent, '_service', fake_service))
    return agent, fake_service


# ---------- list_unread ----------

def test_list_unread_returns_sender_subject_snippet(agent_with_fake_service):
    agent, service = agent_with_fake_service
    service.users().messages().list().execute.return_value = {
        'messages': [{'id': 'm1'}, {'id': 'm2'}]}
    service.users().messages().get().execute.side_effect = [
        {'payload': {'headers': [{'name': 'From', 'value': 'a@x.com'}, {'name': 'Subject', 'value': 'Hi'}]},
         'snippet': 'first snippet'},
        {'payload': {'headers': [{'name': 'From', 'value': 'b@x.com'}, {'name': 'Subject', 'value': 'Yo'}]},
         'snippet': 'second snippet'},
    ]
    result = agent.list_unread()
    assert result == [
        {'id': 'm1', 'sender': 'a@x.com', 'subject': 'Hi', 'snippet': 'first snippet'},
        {'id': 'm2', 'sender': 'b@x.com', 'subject': 'Yo', 'snippet': 'second snippet'},
    ]


def test_list_unread_missing_headers_degrade_to_placeholders(agent_with_fake_service):
    agent, service = agent_with_fake_service
    service.users().messages().list().execute.return_value = {'messages': [{'id': 'm1'}]}
    service.users().messages().get().execute.return_value = {'payload': {'headers': []}, 'snippet': ''}
    result = agent.list_unread()
    assert result == [{'id': 'm1', 'sender': '(unknown sender)', 'subject': '(no subject)', 'snippet': ''}]


def test_list_unread_empty_inbox_returns_empty_list(agent_with_fake_service):
    agent, service = agent_with_fake_service
    service.users().messages().list().execute.return_value = {}
    assert agent.list_unread() == []


# ---------- get_email_body ----------

def test_get_email_body_extracts_plain_text(agent_with_fake_service):
    agent, service = agent_with_fake_service
    service.users().messages().get().execute.return_value = {
        'payload': {'mimeType': 'text/plain', 'body': {'data': _b64('the body text')}}}
    assert agent.get_email_body('m1') == 'the body text'


# ---------- send_reply ----------

def test_send_reply_adds_re_prefix_and_threading_headers(agent_with_fake_service):
    agent, service = agent_with_fake_service
    service.users().messages().get().execute.return_value = {
        'threadId': 't1',
        'payload': {'headers': [
            {'name': 'From', 'value': 'sender@x.com'},
            {'name': 'Subject', 'value': 'Original Subject'},
            {'name': 'Message-ID', 'value': '<abc123@x.com>'},
        ]},
    }
    agent.send_reply('m1', 'my reply body')
    send_call = service.users().messages().send
    _, kwargs = send_call.call_args
    assert kwargs['userId'] == 'me'
    assert kwargs['body']['threadId'] == 't1'
    raw_bytes = base64.urlsafe_b64decode(kwargs['body']['raw'])
    raw_text = raw_bytes.decode('utf-8')
    assert 'Subject: Re: Original Subject' in raw_text
    assert 'To: sender@x.com' in raw_text
    assert 'In-Reply-To: <abc123@x.com>' in raw_text
    assert 'my reply body' in raw_text


def test_send_reply_does_not_double_prefix_an_existing_re(agent_with_fake_service):
    agent, service = agent_with_fake_service
    service.users().messages().get().execute.return_value = {
        'threadId': 't1',
        'payload': {'headers': [
            {'name': 'From', 'value': 'sender@x.com'},
            {'name': 'Subject', 'value': 'Re: Already A Reply'},
        ]},
    }
    agent.send_reply('m1', 'body')
    kwargs = service.users().messages().send.call_args.kwargs
    raw_text = base64.urlsafe_b64decode(kwargs['body']['raw']).decode('utf-8')
    assert 'Subject: Re: Already A Reply' in raw_text
    assert 'Re: Re:' not in raw_text
