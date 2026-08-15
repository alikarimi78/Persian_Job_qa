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

# Advanced search. The header is written as a statement of what was done rather than as
# an answer, because what follows it is a ranked list: the analysis prose sits above the
# matches, and on the template path (no API) the list is all there is.
PROFILE_HEADER = "بر اساس پروفایلی که وارد کردی، نزدیک‌ترین مشاغل موجود در پایگاه داده اینها هستند:"
PROFILE_COVER_LABEL = "پوشش موارد شما"
PROFILE_MISSING_LABEL = "پوشش داده نشد"
PROFILE_NONE = ("هیچ شغلی در پایگاه داده با موارد واردشده هم‌خوانی ندارد. "
                "می‌توانی موارد کمتری وارد کنی یا آنها را کلی‌تر بنویسی.")

# Kept distinct on purpose: an API outage must never be reported to the user as
# "that isn't a real job". See the NOT_A_JOB sentinel in engine.py.
DISCOVERY_UNAVAILABLE = ("امکان ساخت شغل پیشنهادی در این لحظه فراهم نیست؛ "
                         "نزدیک‌ترین مشاغل موجود در پایگاه داده اینها هستند:")
DISCOVERY_NOT_REAL = ("آنچه توصیف کردی به شغل واقعی‌ای اشاره نمی‌کند، بنابراین شغلی برای آن ساخته نشد؛ "
                      "نزدیک‌ترین مشاغل موجود در پایگاه داده اینها هستند:")
