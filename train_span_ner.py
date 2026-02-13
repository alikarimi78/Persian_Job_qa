import json
from typing import List, Dict, Any, Tuple
import torch

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
# 1) تنظیمات
# ---------------------------

MODEL_NAME = "bert-base-multilingual-cased"   # اگر مدل فارسی مثل ParsBERT داری، جایگزین کن
MAX_LENGTH = 256
DATA_PATH = "data.jsonl"
OUTPUT_DIR = "./job_ner_model"
HF_TOKEN = "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"
# ---------------------------
# 2) خواندن دیتاست JSONL
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
# 3) ساخت لیست label ها و mapping های BIO
# ---------------------------

def build_label_list(examples: List[Dict[str, Any]]) -> List[str]:
    base_labels = set()
    for ex in examples:
        for ent in ex.get("entities", []):
            base_labels.add(ent["label"])
    base_labels = sorted(list(base_labels))

    # BIO labels: O + B- + I-
    labels = ["O"]
    for bl in base_labels:
        labels.append(f"B-{bl}")
        labels.append(f"I-{bl}")
    return labels

# ---------------------------
# 4) تبدیل entities (char spans) -> token labels با offset_mapping
# ---------------------------

def char_span_to_token_labels(
    entities: List[Dict[str, Any]],
    offsets: List[Tuple[int, int]],
    label2id: Dict[str, int],
) -> List[int]:
    """
    offsets: برای هر توکن (start_char, end_char) در متن اصلی.
    خروجی: لیبل هر توکن به صورت id با BIO.
    """

    labels = ["O"] * len(offsets)

    # برای راحتی: entities را مرتب کن
    ents = sorted(entities, key=lambda x: (x["start"], x["end"]))

    for ent in ents:
        start, end, lab = ent["start"], ent["end"], ent["label"]

        # توکن‌هایی که با این بازه همپوشانی دارند را پیدا کن
        token_idxs = []
        for i, (s, e) in enumerate(offsets):
            # توکن‌های ویژه مثل [CLS]/[SEP] معمولا (0,0) هستند
            if s == 0 and e == 0:
                continue
            # همپوشانی کاراکتری
            if e <= start:
                continue
            if s >= end:
                break
            # اگر همپوشانی داشت:
            if not (e <= start or s >= end):
                token_idxs.append(i)

        if not token_idxs:
            continue

        # اولین توکن: B- ، بقیه: I-
        labels[token_idxs[0]] = f"B-{lab}"
        for ti in token_idxs[1:]:
            labels[ti] = f"I-{lab}"

    return [label2id[x] for x in labels]

def tokenize_and_align_labels(batch, tokenizer, label2id):
    tokenized = tokenizer(
        batch["text"],
        truncation=True,
        max_length=MAX_LENGTH,
        return_offsets_mapping=True,
    )

    all_labels = []
    for i in range(len(batch["text"])):
        text = batch["text"][i]
        entities = batch["entities"][i] if batch["entities"][i] is not None else []
        offsets = tokenized["offset_mapping"][i]

        token_labels = char_span_to_token_labels(
            entities=entities,
            offsets=offsets,
            label2id=label2id,
        )

        # برای Trainer باید offset_mapping را حذف کنیم، ولی لیبل‌ها را نگه داریم
        all_labels.append(token_labels)

    tokenized["labels"] = all_labels
    tokenized.pop("offset_mapping")
    return tokenized

# ---------------------------
# 5) متریک seqeval
# ---------------------------

seqeval = evaluate.load("seqeval")

def compute_metrics(p, id2label):
    predictions, labels = p
    preds = np.argmax(predictions, axis=2)

    true_predictions = [
        [id2label[pred] for (pred, lab) in zip(pred_row, lab_row) if lab != -100]
        for pred_row, lab_row in zip(preds, labels)
    ]
    true_labels = [
        [id2label[lab] for (pred, lab) in zip(pred_row, lab_row) if lab != -100]
        for pred_row, lab_row in zip(preds, labels)
    ]

    results = seqeval.compute(predictions=true_predictions, references=true_labels)
    # خروجی‌های اصلی
    return {
        "precision": results["overall_precision"],
        "recall": results["overall_recall"],
        "f1": results["overall_f1"],
        "accuracy": results["overall_accuracy"],
    }

# ---------------------------
# 6) استخراج اسپن‌ها از پیش‌بینی مدل (بدون pipeline، دقیق‌تر با offsets)
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
    offsets = enc["offset_mapping"][0]  # shape: (seq_len, 2)
    pred_ids = logits.argmax(axis=-1)   # shape: (seq_len,)

    spans = []
    current = None  # (label, start_char, end_char)

    for (s, e), pid in zip(offsets, pred_ids):
        if s == 0 and e == 0:
            continue  # special token

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
            # I- همان برچسب => span را گسترش بده
            current = (current[0], current[1], int(e))

    if current is not None:
        spans.append(current)

    # متن span ها را اضافه کن
    out = []
    for lab, s, e in spans:
        out.append({"label": lab, "start": s, "end": e, "span_text": text[s:e]})
    return out

# ---------------------------
# 7) main: آموزش
# ---------------------------

def main():
    raw = load_jsonl(DATA_PATH)

    labels = build_label_list(raw)
    label2id = {l: i for i, l in enumerate(labels)}
    id2label = {i: l for l, i in label2id.items()}

    print("Labels:", labels)

    ds = Dataset.from_list(raw)

    # تقسیم train/test ساده (می‌تونی بهترش کنی)
    ds = ds.train_test_split(test_size=0.2, seed=42)
    train_ds, test_ds = ds["train"], ds["test"]

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    train_tok = train_ds.map(
        lambda b: tokenize_and_align_labels(b, tokenizer, label2id),
        batched=True,
        remove_columns=train_ds.column_names,
    )
    test_tok = test_ds.map(
        lambda b: tokenize_and_align_labels(b, tokenizer, label2id),
        batched=True,
        remove_columns=test_ds.column_names,
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
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_steps=50,
        load_best_model_at_end=True,
        metric_for_best_model="f1",
    )

    data_collator = DataCollatorForTokenClassification(tokenizer)

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_tok,
        eval_dataset=test_tok,
        processing_class=tokenizer,
        data_collator=data_collator,
        compute_metrics=lambda p: compute_metrics(p, id2label),
    )

    trainer.train()
    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # تست استخراج روی یک نمونه
    sample_text = raw[0]["text"]
    device = next(model.parameters()).device  # یا torch.device("cuda")
    enc = tokenizer(sample_text, return_tensors="pt", truncation=True, max_length=MAX_LENGTH)
    enc = {k: v.to(device) for k, v in enc.items()}
    with torch.no_grad():
        out = model(**enc)
    logits = out.logits[0].cpu().numpy()
    spans = extract_spans_from_logits(sample_text, tokenizer, logits, id2label)
    print("\nSample text:", sample_text)
    print("Extracted spans:", spans)

if __name__ == "__main__":
    main()