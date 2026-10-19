# Archive of One-Off Migration Scripts

This directory contains 20 one-off Python scripts that were used to migrate or fix the codebase during development. These scripts are not part of the shipped application and are kept for historical record.

Each script performed a specific modification to the codebase, such as fixing a function, updating a configuration, or correcting a file. After serving their purpose, they are no longer imported or used by any runtime module.

## List of Scripts

| Filename | Description |
|----------|-------------|
| append_deci.py | Appends a `decide_proposal` function to `agents.py` (using f-strings) |
| append_decider.py | Appends a `decide_proposal` function to `agents.py` (using string concatenation) |
| fix_agents.py | Fixes escaped newlines in `AGENTS.md` |
| fix_all.py | Replaces the `decide_proposal` function in `agents.py` with a corrected version |
| fix_config.py | Changes the coder model in `config.py` from `deepseek-v4.1-flash-uncensored` to `qwen2.5-coder:7b` |
| fix_decide.py | Replaces the `decide_proposal` function in `agents.py` |
| fix_decide2.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_decide3.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_decide_final.py | Replaces the `decide_proposal` function in `agents.py` (final variant) |
| fix_decide_final2.py | Replaces the `decide_proposal` function in `agents.py` (final variant) |
| fix_decide_final3.py | Replaces the `decide_proposal` function in `agents.py` (final variant) |
| fix_decide_final_final.py | Replaces the `decide_proposal` function in `agents.py` (see details) |
| fix_decide_finally.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_decide_proper.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_decide_real.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_decide_v2.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_decide_v3.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_final.py | Replaces the `decide_proposal` function in `agents.py` (variant) |
| fix_ollama_config.py | Fixes `_ollama_config_for_model` in `agents.py` to return `EXPLABS_URL` for `EXPLABS_MODELS` models |
| fix_ollama_url.py | Same as `fix_ollama_config.py` (duplicate fix) |

Note: Many scripts are variants of fixing the `decide_proposal` function in `agents.py`; they were attempted iterations during development.