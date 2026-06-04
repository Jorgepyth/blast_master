import sys
import os
from InquirerPy import inquirer
from InquirerPy.base.control import Choice

class ReloadChoicesException(Exception):
    pass

prompt = inquirer.select(
    message="Test reload >",
    choices=[Choice("1", name="One"), Choice("2", name="Two")],
    pointer=">",
    qmark=""
)

@prompt.register_kb("c-r")
def _reload(event):
    event.app.exit(exception=ReloadChoicesException("Reload"))

try:
    print("Press Ctrl+R now.")
    prompt.execute()
except ReloadChoicesException:
    print("Caught ReloadChoicesException!")
