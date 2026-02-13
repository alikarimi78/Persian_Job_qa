import os
import json
from typing import Dict, Any, List, Tuple

import numpy as np
import torch
from datasets import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForTokenClassification,
    Trainer,
    TrainingArguments,
    DataCollatorForTokenClassification,
)
import evaluate


MAX_LENGTH = 256
DATA_PATH = "data.jsonl"
MODEL_DIR = "./job_ner_model2"   # همون OUTPUT_DIR که در train ساختی


# ---------------------------
# utils
# ---------------------------
def load_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------
# metریک seqeval
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


# ---------------------------
# char spans -> token labels (برای ساخت test_tok)
# ---------------------------
def char_span_to_token_labels(entities, offsets, label2id):
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

def tokenize_and_align_labels(batch, tokenizer, label2id, max_length=MAX_LENGTH):
    tokenized = tokenizer(
        batch["text"],
        truncation=True,
        max_length=max_length,
        return_offsets_mapping=True,
    )

    all_labels = []
    for i in range(len(batch["text"])):
        entities = batch["entities"][i] if batch["entities"][i] is not None else []
        offsets = tokenized["offset_mapping"][i]
        all_labels.append(char_span_to_token_labels(entities, offsets, label2id))

    tokenized["labels"] = all_labels
    tokenized.pop("offset_mapping")
    return tokenized


# ---------------------------
# استخراج span از logits (اختیاری برای نمایش)
# ---------------------------
def extract_spans_from_logits(
    text: str,
    tokenizer,
    logits: np.ndarray,
    id2label: Dict[int, str],
    max_length: int = MAX_LENGTH,
):
    enc = tokenizer(
        text,
        truncation=True,
        max_length=max_length,
        return_offsets_mapping=True,
        return_tensors="np",
    )
    offsets = enc["offset_mapping"][0]
    pred_ids = logits.argmax(axis=-1)

    spans = []
    current = None  # (label, start_char, end_char)

    for (s, e), pid in zip(offsets, pred_ids):
        if s == 0 and e == 0:
            continue

        tag = id2label[int(pid)]
        if tag == "O":
            if current is not None:
                spans.append(current)
                current = None
            continue

        bio, base = tag.split("-", 1)
        if bio == "B" or current is None or current[0] != base:
            if current is not None:
                spans.append(current)
            current = (base, int(s), int(e))
        else:
            current = (current[0], current[1], int(e))

    if current is not None:
        spans.append(current)

    return [{"label": lab, "start": s, "end": e, "span_text": text[s:e]} for lab, s, e in spans]


def main():
    # 1) لود مدل/توکنایزر
    tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
    model = AutoModelForTokenClassification.from_pretrained(MODEL_DIR)

    # label maps از config
    id2label = {int(k): v for k, v in model.config.id2label.items()} if isinstance(model.config.id2label, dict) else model.config.id2label
    label2id = model.config.label2id

    # 2) دیتاست خام + بازسازی split test با splits.json
    raw = load_jsonl(DATA_PATH)
    ds = Dataset.from_list(raw)
    ds = ds.add_column("example_id", list(range(len(ds))))

    splits_path = os.path.join(MODEL_DIR, "splits.json")
    if not os.path.exists(splits_path):
        raise FileNotFoundError("splits.json پیدا نشد. اول train_ner.py رو اجرا کن تا splitها ذخیره بشن.")

    splits = load_json(splits_path)
    test_ids = set(splits["test"])

    # فیلتر test بر اساس example_id
    test_ds = ds.filter(lambda x: x["example_id"] in test_ids)

    test_tok = test_ds.map(
        lambda b: tokenize_and_align_labels(b, tokenizer, label2id),
        batched=True,
        remove_columns=test_ds.column_names,
    )

    # 3) ارزیابی روی test
    data_collator = DataCollatorForTokenClassification(tokenizer)

    # TrainingArguments فقط برای اینکه Trainer ساخته بشه (اینجا آموزش نداریم)
    args = TrainingArguments(
        output_dir=os.path.join(MODEL_DIR, "eval_tmp"),
        per_device_eval_batch_size=8,
        report_to=[],
    )

    trainer = Trainer(
        model=model,
        args=args,
        eval_dataset=test_tok,
        tokenizer=tokenizer,
        data_collator=data_collator,
        compute_metrics=lambda p: compute_metrics(p, id2label),
    )

    metrics = trainer.evaluate()
    print("\n📌 Test metrics:")
    for k, v in metrics.items():
        print(f"{k}: {v}")

    # 4) یک نمونه inference (اختیاری)
    sample_text = raw[0]["text"]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    enc = tokenizer(sample_text, return_tensors="pt", truncation=True, max_length=MAX_LENGTH)
    enc = {k: v.to(device) for k, v in enc.items()}
    with torch.no_grad():
        out = model(**enc)
    logits = out.logits[0].detach().cpu().numpy()

    spans = extract_spans_from_logits(sample_text, tokenizer, logits, id2label)
    print("\nSample text:", sample_text)
    print("Extracted spans:", spans)


if __name__ == "__main__":
    main()