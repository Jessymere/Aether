import os
from pathlib import Path

from unsloth import FastLanguageModel

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
TARGET = OUTPUT / "gguf" / "local-coder-tuned"


def main() -> None:
    max_seq_length = int(os.environ.get("UNSLOTH_MAX_SEQ_LENGTH", "512"))
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(OUTPUT),
        max_seq_length=max_seq_length,
        load_in_4bit=True,
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    model.save_pretrained_gguf(str(TARGET), tokenizer, quantization_method="q4_k_m")
    print(f"GGUF export complete: {TARGET}")


if __name__ == "__main__":
    main()
