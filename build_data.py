import json

def spans_to_entities(text, labeled_spans):
    """
    labeled_spans: list of tuples (label, span_text)
    """
    entities = []
    for label, span_text in labeled_spans:
        if text.count(span_text) != 1:
            raise ValueError(
                f"Span must appear exactly once.\nSpan: {span_text}\nText: {text}"
            )
        start = text.index(span_text)
        end = start + len(span_text)
        entities.append({"start": start, "end": end, "label": label})
    return entities

samples = [
    {
        "id": "1",
        "text": "اپراتور مخابرات باید تنظیم فرکانس رادیو و تهیه گزارش روزانه را انجام دهد.",
        "spans": [("TASK", "تنظیم فرکانس رادیو"), ("TASK", "تهیه گزارش روزانه")],
    },
    {
        "id": "2",
        "text": "برای این شغل مدرک کارشناسی برق و حداقل 2 سال تجربه تعمیرات لازم است.",
        "spans": [("REQ_EDU", "مدرک کارشناسی برق"), ("REQ_EXP", "حداقل 2 سال تجربه تعمیرات")],
    },
    {
        "id": "3",
        "text": "شرکت در دوره ایمنی پرواز و آشنایی با دستورالعمل های پروازی الزامی است.",
        "spans": [("REQ_TRAIN", "دوره ایمنی پرواز"), ("COMP_K", "آشنایی با دستورالعمل های پروازی")],
    },
    {
        "id": "4",
        "text": "توانایی عیب یابی سامانه ناوبری و کار با نرم افزار نگهداری مورد نیاز است.",
        "spans": [("COMP_S", "عیب یابی سامانه ناوبری"), ("COMP_S", "کار با نرم افزار نگهداری")],
    },
    {
        "id": "5",
        "text": "رعایت سلسله مراتب و دقت در ثبت اطلاعات از رفتارهای مورد انتظار است.",
        "spans": [("COMP_B", "رعایت سلسله مراتب"), ("COMP_B", "دقت در ثبت اطلاعات")],
    },
    {
        "id": "6",
        "text": "انجام بازرسی قبل از عملیات و ثبت نتایج در فرم استاندارد از وظایف اصلی است.",
        "spans": [("TASK", "بازرسی قبل از عملیات"), ("TASK", "ثبت نتایج در فرم استاندارد")],
    },
    {
        "id": "7",
        "text": "برای این شغل مدرک دیپلم فنی و گذراندن دوره تعمیر موتور ضروری است.",
        "spans": [("REQ_EDU", "مدرک دیپلم فنی"), ("REQ_TRAIN", "گذراندن دوره تعمیر موتور")],
    },
    {
        "id": "8",
        "text": "هماهنگی با تیم شیفت و گزارش دهی به فرمانده باید به صورت منظم انجام شود.",
        "spans": [("TASK", "هماهنگی با تیم شیفت"), ("TASK", "گزارش دهی به فرمانده")],
    },
    {
        "id": "9",
        "text": "آشنایی با نقشه های فنی و خواندن شماتیک برای انجام کار لازم است.",
        "spans": [("COMP_K", "آشنایی با نقشه های فنی"), ("COMP_S", "خواندن شماتیک")],
    },
    {
        "id": "10",
        "text": "حداقل 1 سال سابقه کار در واحد تعمیرات به عنوان شرط احراز در نظر گرفته می شود.",
        "spans": [("REQ_EXP", "حداقل 1 سال سابقه کار در واحد تعمیرات")],
    },
]

out_path = "data.jsonl"
with open(out_path, "w", encoding="utf-8") as f:
    for s in samples:
        row = {
            "id": s["id"],
            "text": s["text"],
            "entities": spans_to_entities(s["text"], s["spans"]),
        }
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

print(f"✅ wrote {len(samples)} samples to {out_path}")