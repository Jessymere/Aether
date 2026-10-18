filepath = "/Users/jessy/Documents/Codex/2026-10-16/hey-i-have-local/outputs/multi_ai_builder/agents.py"

with open(filepath, "r") as f:
    content = f.read()

# Fix _ollama_config_for_model to return EXPLABS_URL instead of OLLAMA_URL
# for models registered in EXPLABS_MODELS
content = content.replace(
    '    for _role, slug in EXPLABS_MODELS.items():\n        if model == slug:\n            return OLLAMA_URL, EXPLABS_API_KEY',
    '    for _role, slug in EXPLABS_MODELS.items():\n        if model == slug:\n            return EXPLABS_URL, EXPLABS_API_KEY'
)

with open(filepath, "w") as f:
    f.write(content)
print("Fixed config.py: _ollama_config_for_model now returns EXPLABS_URL for EXPLABS_MODELS models")