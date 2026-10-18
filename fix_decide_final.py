filepath = "/Users/jessy/Documents/Codex/2026-10-16/hey-i-have-local/outputs/multi_ai_builder/agents.py"

with open(filepath, "r") as f:
    content = f.read()

# Fix decision_proposal function - fix escaped newlines
content = content.replace(
    'input_text = "Request: " + request + "\\n\\n"',
    'input_text = "Request: " + request + "\\n\\n"'
)
content = content.replace(
    'input_text += "  - " + item.get("path", "unknown") + ": " + item.get("content", "")[:500] + "\\n"',
    'input_text += "  - " + item.get("path", "unknown") + ": " + item.get("content", "")[:500] + "\\n"'
)
content = content.replace(
    'input_text += "\\nSummary: " + proposal.get("summary", "") + "\\n"',
    'input_text += "\\nSummary: " + proposal.get("summary", "") + "\\n"'
)
content = content.replace(
    'input_text += "\\nReviewer: " + review[:1000]',
    'input_text += "\\nReviewer: " + review[:1000]'
)
content = content.replace(
    'input_text += "\\nSecurity: " + security[:1000]',
    'input_text += "\\nSecurity: " + security[:1000]'
)

with open(filepath, "w") as f:
    f.write(content)
print("Fixed decision_proposal function")