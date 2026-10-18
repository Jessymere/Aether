# Fine-tune a local coder with Unsloth

This pipeline trains a small coder adapter from approved builder runs, exports GGUF, and creates an Ollama model.

## 1. Install in an isolated environment

Use the isolated setup script on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File training\setup_windows.ps1
.\training\.venv\Scripts\Activate.ps1
```

This follows Unsloth's current Windows guidance: Python 3.13 through `uv`, `torchao` first, then Unsloth. Do not install training packages into the builder's normal runtime.

## 2. Build a dataset from approved runs

```powershell
python training\prepare_dataset.py
```

Only runs with `approved: true` are included. Review the generated `training\dataset.jsonl` before training. Aim for at least 50–200 high-quality examples; never train on secrets or unreviewed output.

You can generate starter candidates and approve them interactively:

```powershell
python training\generate_examples.py --count 5
python training\approve_examples.py
```

Candidates are never added to training until you answer `y` for each one.

For automatic approval with a reviewer gate:

```powershell
python training\approve_examples.py --auto-approve
```

This only accepts examples whose reviewer ends with `APPROVE`; it still does not train until at least 10 reviewed examples exist.

To automatically repair rejected examples before deciding:

```powershell
python training\approve_examples.py --auto-fix
```

## 3. Train and export

```powershell
python training\train.py
```

The default is QLoRA with conservative settings for a small NVIDIA laptop GPU and `unsloth/Qwen3.5-4B`. Set `UNSLOTH_BASE_MODEL` if you choose a different compatible Hugging Face model. The published base model is approximately 9.35 GB before 4-bit loading, so first-run downloads need substantial disk space.

For an explicitly experimental run with fewer than 10 examples, set `UNSLOTH_MIN_EXAMPLES` yourself; small datasets can overfit.

On Windows, if Triton compilation takes too long, use `UNSLOTH_COMPILE_DISABLE=1`, `UNSLOTH_MAX_SEQ_LENGTH=512`, `UNSLOTH_GRAD_ACCUM=1`, and `UNSLOTH_EPOCHS=1` for a smoke-test run.

## 4. Import into Ollama

```powershell
ollama create local-coder-tuned -f training\Modelfile
ollama run local-coder-tuned
```

The exported model must use the same chat template used during training. Unsloth specifically warns that a mismatched template can cause poor or broken Ollama output.
