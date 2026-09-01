"""docs/REFACTOR_PLAN.md Step 6: actually invokes the capability an Intent
(core/intent_router.py) names - the "run" half of execute()'s old fused
"classify + run" chain. Every handler body below is a direct, unchanged port
of main.py's old `h_*` closures, just taking `self.host` (the Vortex
instance) explicitly instead of closing over `self` implicitly, and an
Intent's typed fields instead of a raw regex match object's `.group(N)`.

Unlike intent_router.route(), this module is NOT pure by design - invoking
a capability is exactly the side effect the split exists to isolate
classification from (see intent_router.py's module docstring)."""
import datetime

from . import intent_router as intents
from . import personality
from .. import files as fileops
from .. import popup
from .. import screen as screen_reader


def _short_sender(sender):
    """"John Doe <john@example.com>" -> "John Doe" - the display name is
    what's worth speaking aloud, not the raw address."""
    return sender.split('<')[0].strip() or sender


class CapabilityRegistry:
    def __init__(self, host):
        self.host = host
        self._handlers = {
            intents.ShutdownVortex: self._shutdown_vortex,
            intents.StandDown: self._stand_down,
            intents.SetPersonalityMode: self._set_personality_mode,
            intents.SpeakTime: self._speak_time,
            intents.SpeakDate: self._speak_date,
            intents.CloseAllPrompt: self._close_all_prompt,
            intents.RestartPrompt: self._restart_prompt,
            intents.ShutdownPrompt: self._shutdown_prompt,
            intents.Lock: self._lock,
            intents.CloseBrowser: self._close_browser,
            intents.ReadPage: self._read_page,
            intents.ReadScreen: self._read_screen,
            intents.CheckEmail: self._check_email,
            intents.ReplyToEmailPrompt: self._reply_to_email_prompt,
            intents.RecallMemory: self._recall_memory,
            intents.PlayYoutube: self._play_youtube,
            intents.SearchFiles: self._search_files,
            intents.WebSearch: self._web_search,
            intents.Browse: self._browse,
            intents.Click: self._click,
            intents.ListFiles: self._list_files,
            intents.DeleteFilePrompt: self._delete_file_prompt,
            intents.MoveFilePrompt: self._move_file_prompt,
            intents.CopyFile: self._copy_file,
            intents.RenameFilePrompt: self._rename_file_prompt,
            intents.CloseApp: self._close_app,
            intents.OpenTarget: self._open_target,
            intents.DocumentQuestion: self._document_question,
            intents.SummarizeDocument: self._summarize_document,
            intents.ReadDocument: self._read_document,
        }
        # intents.Unhandled has no handler here on purpose - main.py's
        # execute() routes it straight to the LLM fallback itself, the same
        # way it always has, without going through dispatch() at all.
        assert set(self._handlers) == set(intents.ALL_INTENT_TYPES) - {intents.Unhandled}, (
            'every routable Intent type needs a handler here')

    def dispatch(self, intent):
        self._handlers[type(intent)](intent)

    # ---------- handlers (verbatim ports of the old h_* closures) ----------

    def _shutdown_vortex(self, intent):
        self.host.speak('Shutting down. See you soon Boss.')
        self.host.stop()

    def _stand_down(self, intent):
        """No self.host.speak() call anywhere in this method, on purpose -
        the feature spec is explicit that this transition must be silent.
        Ending the process's own active_session() loop happens via a
        dedicated Event (voice/session.py's Session.end_session_now), not by
        touching barge_in's speaking/stop_speaking - see that Event's own
        docstring for why reusing barge-in's Events here would risk the kind
        of latency regression Step 3 already fixed once."""
        self.host.session.end_session_now.set()

    def _set_personality_mode(self, intent):
        mode = personality.PersonalityMode(intent.mode)
        self.host.personality_mode = mode
        self.host.speak(f'Switched to {mode.value.capitalize()} mode.')
        # Entering DEMO specifically also gives the actual self-demonstration
        # content (Vortex.demonstrate_self) - switching mode alone only
        # changes the *tone* of future answers, but "demonstrate yourself" is
        # a request for real content (capabilities/history/plans/drawbacks),
        # not just an acknowledgment. Every other mode switch stays silent
        # beyond the confirmation above - this is deliberately DEMO-only.
        if mode is personality.PersonalityMode.DEMO:
            self.host.demonstrate_self()

    def _speak_time(self, intent):
        self.host.speak(f"The current time is {datetime.datetime.now().strftime('%I:%M %p')}")

    def _speak_date(self, intent):
        self.host.speak(f"Today's date is {datetime.datetime.now().strftime('%d %B %Y')}")

    def _close_all_prompt(self, intent):
        self.host.awaiting_confirmation = {'action': 'close_all'}
        self.host.audit.record('close_all', 'all_non_protected_processes', 'prompted')
        self.host.speak('This may close unsaved work. Should I proceed?')

    def _restart_prompt(self, intent):
        self.host.awaiting_confirmation = {'action': 'restart'}
        self.host.audit.record('restart', 'system', 'prompted')
        self.host.speak('Should I restart the system now Boss?')

    def _shutdown_prompt(self, intent):
        self.host.awaiting_confirmation = {'action': 'shutdown'}
        self.host.audit.record('shutdown', 'system', 'prompted')
        self.host.speak('Should I shut down the system now Boss?')

    def _lock(self, intent):
        self.host.lock_system()

    def _close_browser(self, intent):
        self.host.browser.close()
        self.host.speak('Closed the browser.')

    def _read_page(self, intent):
        self.host.speak(self.host.browser.read_page())

    def _read_screen(self, intent):
        # See main.py's pre-Step-6 comment: this speaks raw OCR'd screen
        # text directly, not an LLM summary of it - accepted as correct
        # (reading what's actually there), not truncated silently.
        text, err = screen_reader.read_screen_text()
        if err:
            self.host.speak(err)
            return
        self.host.speak(text)

    def _check_email(self, intent):
        emails = self._list_unread_or_speak_error()
        if emails is None:
            return
        if not emails:
            self.host.speak('No unread emails.')
            return
        summaries = [f"{_short_sender(e['sender'])}: {e['subject']}" for e in emails]
        plural = 's' if len(emails) != 1 else ''
        self.host.speak(f"You have {len(emails)} unread email{plural}: " + '; '.join(summaries))

    def _reply_to_email_prompt(self, intent):
        """Drafts a reply via the LLM and speaks it for confirmation - never
        sends. The actual send only happens from handle_confirmation() once
        the user says yes, exactly like delete/move/rename file ops."""
        emails = self._list_unread_or_speak_error()
        if emails is None:
            return
        target = intent.target.lower()
        matches = [e for e in emails if target in e['sender'].lower() or target in e['subject'].lower()]
        if not matches:
            self.host.speak(f"I couldn't find an unread email matching {intent.target}.")
            return
        if len(matches) > 1:
            self.host.speak(
                f"I found {len(matches)} unread emails matching {intent.target} - please be more specific.")
            return
        email = matches[0]
        try:
            body = self.host.mail.get_email_body(email['id'])
        except Exception as e:
            self.host.log(f'Email body fetch failed: {e}')
            self.host.speak("I found that email but couldn't read its contents.")
            return
        draft = self.host.draft_email_reply(body, intent.instruction)
        if not draft.strip():
            self.host.speak("I couldn't draft a reply for that.")
            return
        self.host.awaiting_confirmation = {
            'action': 'send_email_reply', 'message_id': email['id'], 'body': draft,
            'to': email['sender'], 'subject': email['subject']}
        self.host.audit.record('send_email_reply', email['id'], 'prompted', to=email['sender'])
        self.host.speak(f"Here's my draft reply: {draft} Should I send it?")

    def _list_unread_or_speak_error(self):
        """Shared by _check_email/_reply_to_email_prompt - returns None (and
        has already spoken the reason) on any failure, so callers can just
        check for that instead of duplicating the same two except clauses."""
        try:
            return self.host.mail.list_unread()
        except FileNotFoundError:
            self.host.speak(
                "Email isn't set up yet - I need a Gmail credentials file. Check the README for how to connect it.")
            return None
        except Exception as e:
            self.host.log(f'Email check failed: {e}')
            self.host.speak("I couldn't reach Gmail right now.")
            return None

    def _recall_memory(self, intent):
        self.host.recall_memory(intent.query)

    def _play_youtube(self, intent):
        self.host.speak(self.host.browser.play_youtube(intent.query))

    def _search_files(self, intent):
        # Speaks each match as "name, in Location", not a full path - see
        # main.py's pre-Step-6 comment for why.
        matches = fileops.search_files(intent.query)
        if not matches:
            self.host.speak(f"I couldn't find any files matching {intent.query}.")
            return
        popup.show_file_popup(matches[:20], title='Search results')
        shown = ', '.join(f'{p.name}, in {p.parent.name}' for p in matches[:5])
        plural = 's' if len(matches) != 1 else ''
        self.host.speak(f'I found {len(matches)} matching file{plural}: {shown}.')

    def _web_search(self, intent):
        self.host.speak(self.host.browser.search(intent.query))

    def _browse(self, intent):
        self.host.speak(self.host.browser.open(intent.target))

    def _click(self, intent):
        self.host.speak(self.host.browser.click_text(intent.text))

    def _list_files(self, intent):
        entries, err = fileops.list_files(intent.dir_name)
        if err:
            self.host.speak(err)
            return
        if not entries:
            self.host.speak('No files found.')
            return
        popup.show_file_popup(entries[:50], title='Files')
        if intent.dir_name:
            shown = ', '.join(e['name'] for e in entries[:10])
        else:
            shown = ', '.join(f"{e['name']}, in {e['location']}" for e in entries[:10])
        more = ', and more.' if len(entries) > 10 else '.'
        plural = 's' if len(entries) != 1 else ''
        self.host.speak(f'Found {len(entries)} file{plural}: {shown}{more}')

    def _delete_file_prompt(self, intent):
        path = fileops.resolve_file(intent.filename)
        if not path:
            self.host.speak(f"I couldn't find a file called {intent.filename}.")
            return
        self.host.awaiting_confirmation = {'action': 'delete_file', 'path': str(path)}
        self.host.audit.record('delete_file', str(path), 'prompted')
        self.host.speak(f'This will move {path.name} to the Recycle Bin. Should I proceed?')

    def _move_file_prompt(self, intent):
        path = fileops.resolve_file(intent.filename)
        if not path:
            self.host.speak(f"I couldn't find a file called {intent.filename}.")
            return
        dest_dir = fileops.resolve_dir(intent.dest_name)
        if not dest_dir:
            self.host.speak(
                f"I can only move files between Desktop, Documents, and Downloads - not {intent.dest_name}.")
            return
        self.host.awaiting_confirmation = {
            'action': 'move_file', 'path': str(path), 'dest_dir': str(dest_dir)}
        self.host.audit.record('move_file', str(path), 'prompted', dest=str(dest_dir))
        self.host.speak(f'This will move {path.name} to {intent.dest_name}. Should I proceed?')

    def _copy_file(self, intent):
        path = fileops.resolve_file(intent.filename)
        if not path:
            self.host.speak(f"I couldn't find a file called {intent.filename}.")
            return
        dest_dir = fileops.resolve_dir(intent.dest_name)
        if not dest_dir:
            self.host.speak(
                f"I can only copy files between Desktop, Documents, and Downloads - not {intent.dest_name}.")
            return
        try:
            dest = fileops.copy_file(path, dest_dir)
            self.host.audit.record('copy_file', str(path), 'executed', dest=str(dest))
            self.host.speak(f'Copied {path.name} to {intent.dest_name}.')
        except FileExistsError:
            self.host.audit.record('copy_file', str(path), 'failed', reason='destination_exists')
            self.host.speak(f"A file named {path.name} already exists in {intent.dest_name}. I won't overwrite it.")
        except fileops.PathNotAllowedError:
            self.host.audit.record('copy_file', str(path), 'failed', reason='path_not_allowed')
            self.host.speak("I can't copy that - it's outside the folders I'm allowed to touch.")
        except OSError as e:
            self.host.audit.record('copy_file', str(path), 'failed', reason=str(e))
            self.host.speak(f"I couldn't copy that file: {e}")

    def _rename_file_prompt(self, intent):
        path = fileops.resolve_file(intent.filename)
        if not path:
            self.host.speak(f"I couldn't find a file called {intent.filename}.")
            return
        self.host.awaiting_confirmation = {
            'action': 'rename_file', 'path': str(path), 'new_name': intent.new_name}
        self.host.audit.record('rename_file', str(path), 'prompted', new_name=intent.new_name)
        self.host.speak(f'This will rename {path.name} to {intent.new_name}. Should I proceed?')

    def _close_app(self, intent):
        self.host.close_named_app(intent.target)

    def _open_target(self, intent):
        self.host.open_target(intent.target)

    def _document_question(self, intent):
        self.host.answer_document_question(intent.doc_name, intent.question)

    def _summarize_document(self, intent):
        self.host.summarize_document(intent.doc_name)

    def _read_document(self, intent):
        self.host.summarize_document(intent.doc_name)
