filepath = "/Users/jessy/Documents/Codex/2026-10-16/hey-i-have-local/outputs/multi_ai_builder/config.py"

with open(filepath, "r") as f:
    content = f.read()

# Change coder model from deepseek-v4.1-flash-uncensored back to qwen2.5-coder:7b
# (qwen2.5-coder:7b is not in MODELS yet, so we need to add it)
content = content.replace(
    '"coder": "deepseek-v4.1-flash-uncensored",',
    '"coder": "qwen2.5-coder:7b",'
)

with open(filepath, "w") as f:
    f.write(content)
print("Changed coder model from deepseek-v4.1-flash-uncensored to qwen2.5-coder:7b")