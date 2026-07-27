import re

with open("cli/main.py", "r") as f:
    lines = f.readlines()

new_lines = []
for idx, line in enumerate(lines):
    # Remove rev_text.append for confirmation_5m_15m
    if 'rev_text.append(f"5m/15m Confirmation:' in line:
        pass # Skip
    # Remove rev_text.append for confirmation_params
    elif 'rev_text.append(f"Conf. Params:' in line:
        # Also inject the new summary for motor B if we are in the main flow (line ~2794). 
        # But wait, we can't just inject anywhere. Let's do string replacement instead.
        pass
    else:
        new_lines.append(line)

with open("cli/main.py", "w") as f:
    f.writelines(new_lines)
