import os
from pathlib import Path

ROOT = Path(__file__).parent
DATASET = ROOT / "dataset.jsonl"
OUTPUT = ROOT / "output"
MODEL_NAME = os.environ.get("UNSLOTH_BASE_MODEL", "unsloth/Qwen3.5-4B")
MAX_SEQ_LENGTH = int(os.environ.get("UNSLOTH_MAX_SEQ_LENGTH", "2048"))


def main() -> None:
    if not DATASET.exists() or not DATASET.read_text(encoding="utf-8").strip():
        raise SystemExit("dataset.jsonl is empty. Run prepare_dataset.py and approve more runs first.")
    example_count = len(DATASET.read_text(encoding="utf-8").splitlines())
    minimum_examples = int(os.environ.get("UNSLOTH_MIN_EXAMPLES", "10"))
    if example_count < minimum_examples:
        raise SystemExit(f"Only {example_count} training examples found. Collect at least {minimum_examples} reviewed examples before training.")
    from datasets import load_dataset
    from unsloth import FastLanguageModel
    from trl import SFTConfig, SFTTrainer

    dataset = load_dataset("json", data_files=str(DATASET), split="train")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=8,
        lora_alpha=16,
        lora_dropout=0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    def format_rows(rows):
        batch = rows["messages"]
        if batch and isinstance(batch[0], dict):
            batch = [batch]
        return [
            tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            for messages in batch
        ]

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        formatting_func=format_rows,
        args=SFTConfig(
            output_dir=str(OUTPUT),
            per_device_train_batch_size=1,
            gradient_accumulation_steps=int(os.environ.get("UNSLOTH_GRAD_ACCUM", "8")),
            num_train_epochs=float(os.environ.get("UNSLOTH_EPOCHS", "2")),
            learning_rate=2e-4,
            logging_steps=1,
            save_strategy="epoch",
            optim="adamw_8bit",
            report_to="none",
            seed=3407,
        ),
    )
    trainer.train()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUTPUT))
    tokenizer.save_pretrained(str(OUTPUT))
    model.save_pretrained_gguf(str(OUTPUT / "gguf" / "local-coder-tuned"), tokenizer, quantization_method="q4_k_m")
    print(f"Training and GGUF export complete: {OUTPUT}")


if __name__ == "__main__":
    main()
