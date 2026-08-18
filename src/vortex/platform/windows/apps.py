"""docs/REFACTOR_PLAN.md Step 5: the Windows-specific name -> executable/URL
tables, moved as-is (not redesigned) from main.py's native_apps/web_apps."""

NATIVE_APPS = {
    'outlook': 'outlook.exe', 'chrome': 'chrome.exe', 'edge': 'msedge.exe',
    'vscode': 'code.exe', 'vs code': 'code.exe', 'visual studio code': 'code.exe',
    'notepad': 'notepad.exe', 'calculator': 'calc.exe', 'paint': 'mspaint.exe',
    'whatsapp': 'whatsapp.exe', 'teams': 'teams.exe', 'spotify': 'spotify.exe'
}

WEB_APPS = {
    'youtube': 'https://youtube.com', 'gmail': 'https://mail.google.com',
    'github': 'https://github.com', 'chatgpt': 'https://chatgpt.com',
    'google': 'https://google.com', 'whatsapp': 'https://web.whatsapp.com'
}
