# -*- coding: utf-8 -*-
"""Fixed Persian text shown to the user.

Separate from `prompts.py` on purpose: those are instructions to the model, these are
sentences the user reads verbatim — including on every path where the LLM produced
nothing, since each caller falls back to a template so the endpoint never fails
because the API did.
"""

OOD_MESSAGE = "متاسفانه در دیتابیس من اطلاعاتی درباره این موضوع پیدا نشد."

MATCH_HEADER = "بر اساس ویژگی‌هایی که توصیف کردی، این شغل در پایگاه داده موجود است:"
DRAFT_HEADER = ("شغلی که دقیقاً منطبق بر توصیف تو باشد در پایگاه داده موجود نیست، "
                "اما بر اساس ویژگی‌هایی که گفتی یک شغل پیشنهادی طراحی شد:")
DRAFT_QUESTION = ("می‌خواهی این شغل را به‌عنوان شغل جدید ثبت کنی؟ در صورت تایید، فرم ثبت شغل با "
                  "همین مشخصات باز می‌شود و می‌توانی پیش از ارسال آن‌ها را ویرایش کنی. "
                  "ثبت نهایی پس از تایید ادمین انجام می‌شود.")
RELATED_LABEL = "مشاغل مرتبط موجود در پایگاه داده"

# Kept distinct on purpose: an API outage must never be reported to the user as
# "that isn't a real job". See the NOT_A_JOB sentinel in engine.py.
DISCOVERY_UNAVAILABLE = ("امکان ساخت شغل پیشنهادی در این لحظه فراهم نیست؛ "
                         "نزدیک‌ترین مشاغل موجود در پایگاه داده اینها هستند:")
DISCOVERY_NOT_REAL = ("آنچه توصیف کردی به شغل واقعی‌ای اشاره نمی‌کند، بنابراین شغلی برای آن ساخته نشد؛ "
                      "نزدیک‌ترین مشاغل موجود در پایگاه داده اینها هستند:")
