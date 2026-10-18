filepath = "/Users/jessy/Documents/Codex/2026-10-16/hey-i-have-local/outputs/multi_ai_builder/AGENTS.md"

with open(filepath, "r") as f:
    content = f.read()

# Replace \\n\\n (backslash-n backslash-n) with \n\n (newline newline)
content = content.replace("\\\\n\\\\n", "\\n\\n")

with open(filepath, "w") as f:
    f.write(content)

print("Fixed AGENTS.md line 195")