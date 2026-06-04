import sys
import os
from InquirerPy import inquirer
from InquirerPy.base.control import Choice

prompt = inquirer.select(
    message="Test keys >",
    choices=[Choice("1", name="One")],
)

@prompt.register_kb("c-r")
def _cr(event):
    with open("scratch/keys.log", "a") as f:
        f.write("c-r pressed\n")

@prompt.register_kb("c-e")
def _ce(event):
    with open("scratch/keys.log", "a") as f:
        f.write("c-e pressed\n")
        event.app.exit(result="c-e")

prompt.execute()
