import os
import json
from typing import List, Dict, Any, Tuple

import numpy as np
from datasets import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForTokenClassification,
    TrainingArguments,
    Trainer,
    DataCollatorForTokenClassification,
)
import evaluate


# ---------------------------
# تنظیمات
# ---------------------------
MODEL_NAME = "bert-base-multilingual-cased"
MAX_LENGTH = 256
DATA_PATH = "data.jsonl"
OUTPUT_DIR = "./job_ner_model2"

SEED = 42
TEST_SIZE = 0.10   # 10%
VALID_SIZE = 0.10  # 10%


# ---------------------------
# خواندن JSONL
# ---------------------------
def load_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------
# ساخت لیست label ها (BIO)
# ---------------------------
def build_label_list(examples: List[Dict[str, Any]]) -> List[str]:
    base_labels = set()
    for ex in examples:
        for ent in ex.get("entities", []):
            base_labels.add(ent["label"])
    base_labels = sorted(list(base_labels))

    labels = ["O"]
    for bl in base_labels:
        labels.append(f"B-{bl}")
        labels.append(f"I-{bl}")
    return labels


# ---------------------------
# char spans -> token labels
# ---------------------------
def char_span_to_token_labels(
    entities: List[Dict[str, Any]],
    offsets: List[Tuple[int, int]],
    label2id: Dict[str, int],
) -> List[int]:
    """
    برای توکن‌های special با offset (0,0)، label = -100 می‌گذاریم تا در loss حساب نشوند.
    """
    labels = ["O"] * len(offsets)
    ignore_mask = [False] * len(offsets)

    for i, (s, e) in enumerate(offsets):
        if s == 0 and e == 0:
            ignore_mask[i] = True

    ents = sorted(entities, key=lambda x: (x["start"], x["end"]))
    for ent in ents:
        start, end, lab = ent["start"], ent["end"], ent["label"]

        token_idxs = []
        for i, (s, e) in enumerate(offsets):
            if ignore_mask[i]:
                continue
            if e <= start:
                continue
            if s >= end:
                break
            if not (e <= start or s >= end):
                token_idxs.append(i)

        if not token_idxs:
            continue

        labels[token_idxs[0]] = f"B-{lab}"
        for ti in token_idxs[1:]:
            labels[ti] = f"I-{lab}"

    out = []
    for i, lab in enumerate(labels):
        if ignore_mask[i]:
            out.append(-100)
        else:
            out.append(label2id[lab])
    return out


def tokenize_and_align_labels(batch, tokenizer, label2id):
    tokenized = tokenizer(
        batch["text"],
        truncation=True,
        max_length=MAX_LENGTH,
        return_offsets_mapping=True,
    )

    all_labels = []
    for i in range(len(batch["text"])):
        entities = batch["entities"][i] if batch["entities"][i] is not None else []
        offsets = tokenized["offset_mapping"][i]
        token_labels = char_span_to_token_labels(entities, offsets, label2id)
        all_labels.append(token_labels)

    tokenized["labels"] = all_labels
    tokenized.pop("offset_mapping")
    return tokenized


# ---------------------------
# متریک seqeval
# ---------------------------
seqeval = evaluate.load("seqeval")

def compute_metrics(eval_pred, id2label):
    predictions = eval_pred.predictions
    labels = eval_pred.label_ids

    preds = np.argmax(predictions, axis=2)

    true_predictions = [
        [id2label[p] for (p, l) in zip(pred_row, lab_row) if l != -100]
        for pred_row, lab_row in zip(preds, labels)
    ]
    true_labels = [
        [id2label[l] for (p, l) in zip(pred_row, lab_row) if l != -100]
        for pred_row, lab_row in zip(preds, labels)
    ]

    results = seqeval.compute(predictions=true_predictions, references=true_labels)
    return {
        "precision": results["overall_precision"],
        "recall": results["overall_recall"],
        "f1": results["overall_f1"],
        "accuracy": results["overall_accuracy"],
    }


def save_json(path: str, obj: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    raw = load_jsonl(DATA_PATH)
    labels = build_label_list(raw)
    label2id = {l: i for i, l in enumerate(labels)}
    id2label = {i: l for l, i in label2id.items()}

    print("Labels:", labels)

    ds = Dataset.from_list(raw)
    # برای بازسازی split در eval: یک id ثابت به هر نمونه می‌دهیم
    ds = ds.add_column("example_id", list(range(len(ds))))

    # 1) جدا کردن test
    first_split = ds.train_test_split(test_size=TEST_SIZE, seed=SEED, shuffle=True)
    train_valid = first_split["train"]
    test_ds = first_split["test"]

    # 2) جدا کردن valid از train_valid
    # VALID_SIZE از کل دیتاست است، پس نسبتش به train_valid = VALID_SIZE / (1-TEST_SIZE)
    valid_ratio = VALID_SIZE / (1.0 - TEST_SIZE)
    second_split = train_valid.train_test_split(test_size=valid_ratio, seed=SEED, shuffle=True)
    train_ds = second_split["train"]
    valid_ds = second_split["test"]

    # ذخیره splitها (با example_id)
    splits = {
        "train": list(train_ds["example_id"]),
        "valid": list(valid_ds["example_id"]),
        "test":  list(test_ds["example_id"]),
        "seed": int(SEED),
        "test_size": float(TEST_SIZE),
        "valid_size": float(VALID_SIZE),
    }
    save_json(os.path.join(OUTPUT_DIR, "splits.json"), splits)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    train_tok = train_ds.map(
        lambda b: tokenize_and_align_labels(b, tokenizer, label2id),
        batched=True,
        remove_columns=train_ds.column_names,
    )
    valid_tok = valid_ds.map(
        lambda b: tokenize_and_align_labels(b, tokenizer, label2id),
        batched=True,
        remove_columns=valid_ds.column_names,
    )

    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(labels),
        id2label=id2label,
        label2id=label2id,
    )

    args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        learning_rate=2e-5,
        per_device_train_batch_size=8,
        per_device_eval_batch_size=8,
        num_train_epochs=5,
        weight_decay=0.01,

        # فقط روی valid ارزیابی می‌کنیم (نه test)
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_steps=50,

        load_best_model_at_end=True,
        metric_for_best_model="f1",
        greater_is_better=True,

        seed=SEED,
    )

    data_collator = DataCollatorForTokenClassification(tokenizer)

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_tok,
        eval_dataset=valid_tok,
        processing_class=tokenizer,
        data_collator=data_collator,
        compute_metrics=lambda p: compute_metrics(p, id2label),
    )

    trainer.train()

    # ذخیره مدل + توکنایزر (config شامل id2label/label2id هم هست)
    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # برای اطمینان، لیست لیبل‌ها را هم جدا ذخیره می‌کنیم
    save_json(os.path.join(OUTPUT_DIR, "labels.json"), {"labels": labels})

    print(f"\n✅ Training finished. Model saved in: {OUTPUT_DIR}")
    print("✅ Splits saved in:", os.path.join(OUTPUT_DIR, "splits.json"))


if __name__ == "__main__":
    main()