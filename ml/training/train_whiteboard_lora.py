"""Fine-tune a small open model to draw project architectures as Plovane /sketch lines (LoRA).

Runs on a rented GPU (``vast_run.py run --job whiteboard``) or any CUDA box:

    python ml/training/train_whiteboard_lora.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct --epochs 3

Reads ``ml/data/whiteboard_sft_{train,eval}.jsonl`` (from ``ml.whiteboard_data.prepare``),
trains LoRA adapters on the train split, then answers every held-out prompt
and scores the answers against the authors' diagrams with the same scorer the
hosted models get (``evaluate.score_sketch``), so the numbers compare directly.

Writes to ``ml/artifacts/whiteboard_lora/``: ``adapter/`` (the LoRA weights,
tens of MB), ``metrics.json`` (settings, train loss, held-out scores) and
``predictions.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "ml" / "artifacts" / "whiteboard_lora"


# Input: path (Path)
# Output: list[dict]
# Processing: The rows of a JSONL file.
# Changed by: Claude Code
# Date: 2026-11-26
def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# Input: argv (list[str] | None)
# Output: int
# Processing: Train LoRA adapters, generate answers for the held-out prompts, score them, and save adapter, predictions and metrics.
# Changed by: Claude Code
# Date: 2026-11-26
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="Qwen/Qwen2.5-Coder-1.5B-Instruct")
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accumulate", type=int, default=8)
    parser.add_argument("--four-bit", action="store_true", help="load the base model in 4-bit (for 7B on a 24 GB card)")
    args = parser.parse_args(argv)

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    from ml.whiteboard_data.evaluate import score_sketch
    from ml.whiteboard_data.harvest import EXAMPLES

    train_rows = read_rows(ROOT / "ml" / "data" / "whiteboard_sft_train.jsonl")
    eval_rows = read_rows(ROOT / "ml" / "data" / "whiteboard_sft_eval.jsonl")
    charts = {(e["repo"], e["diagram"]): e["chart"] for e in read_rows(EXAMPLES)}
    OUT.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    load = {"torch_dtype": torch.bfloat16, "device_map": "auto"}
    if args.four_bit:
        from transformers import BitsAndBytesConfig

        load["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
                                                         bnb_4bit_quant_type="nf4")
    model = AutoModelForCausalLM.from_pretrained(args.model, **load)

    started = time.time()
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=Dataset.from_list([{"messages": r["messages"]} for r in train_rows]),
        peft_config=LoraConfig(r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, task_type="CAUSAL_LM",
                               target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]),
        args=SFTConfig(output_dir=str(OUT / "checkpoints"), num_train_epochs=args.epochs, learning_rate=args.lr,
                       per_device_train_batch_size=args.batch, gradient_accumulation_steps=args.accumulate,
                       max_length=args.max_length, bf16=True, logging_steps=5, save_strategy="no",
                       assistant_only_loss=False, report_to=[], gradient_checkpointing=True,
                       lr_scheduler_type="cosine", warmup_ratio=0.05),
    )
    result = trainer.train()
    train_seconds = round(time.time() - started, 1)
    trainer.model.save_pretrained(OUT / "adapter")
    tokenizer.save_pretrained(OUT / "adapter")

    trainer.model.eval()
    predictions, scores = [], []
    for row in eval_rows:
        prompt = tokenizer.apply_chat_template(row["messages"][:2], tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=args.max_length).to(trainer.model.device)
        with torch.no_grad():
            output = trainer.model.generate(**inputs, max_new_tokens=600, do_sample=False, pad_token_id=tokenizer.pad_token_id)
        answer = tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        score = score_sketch(answer, charts[(row["repo"], row["diagram"])])
        scores.append(score)
        predictions.append({"repo": row["repo"], "diagram": row["diagram"], "answer": answer, **score})
    with (OUT / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for p in predictions:
            handle.write(json.dumps(p) + "\n")
    keys = ("parts_recall", "parts_precision", "parts_f1", "arrow_recall", "bad_lines")
    mean = {k: round(sum(s[k] for s in scores) / len(scores), 3) for k in keys} if scores else {}
    metrics = {"model": args.model, "settings": vars(args), "train_examples": len(train_rows),
               "eval_examples": len(eval_rows), "train_loss": round(result.training_loss, 4),
               "train_seconds": train_seconds, "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
               "held_out": mean}
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n")
    print(json.dumps(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
