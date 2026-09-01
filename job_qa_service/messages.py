# -*- coding: utf-8 -*-
"""Fixed Persian text shown to the user.

Separate from `prompts.py` on purpose: those are instructions to the model, these are
sentences the user reads verbatim — including on every path where the LLM produced
nothing, since each caller falls back to a template so the endpoint never fails
because the API did.
"""

OOD_MESSAGE = "متاسفانه در پایگاه داده سامانه، اطلاعاتی درباره این موضوع یافت نشد."

MATCH_HEADER = "بر اساس ویژگی‌هایی که توصیف کرده‌اید، این شغل در پایگاه داده موجود است:"
# Reached from two places now — a described spec and a typed job name — so the wording
# says «what you entered» rather than «the features you listed»: on the second path the
# user stated no features at all, only a title.
DRAFT_HEADER = ("شغلی منطبق بر آنچه وارد کرده‌اید در پایگاه داده موجود نیست؛ "
                "بر اساس ورودی شما، شغل زیر پیشنهاد می‌شود:")
DRAFT_QUESTION = ("در صورت تمایل می‌توانید مشخصات این شغل را در فرم زیر بررسی و در صورت نیاز "
                  "ویرایش نمایید و سپس ثبت کنید؛ ثبت نهایی پس از تایید مدیر سامانه انجام "
                  "می‌شود.")
RELATED_LABEL = "مشاغل مرتبط موجود در پایگاه داده"

# Advanced search. The header is written as a statement of what was done rather than as
# an answer, because what follows it is a ranked list: the analysis prose sits above the
# matches, and on the template path (no API) the list is all there is.
PROFILE_HEADER = "بر اساس پروفایل واردشده، نزدیک‌ترین مشاغل موجود در پایگاه داده به شرح زیر است:"
PROFILE_COVER_LABEL = "پوشش موارد شما"
PROFILE_MISSING_LABEL = "پوشش داده نشد"
PROFILE_NONE = ("هیچ شغلی در پایگاه داده با موارد واردشده هم‌خوانی ندارد. "
                "می‌توانید تعداد موارد را کاهش دهید یا آن‌ها را کلی‌تر وارد نمایید.")

# Kept distinct on purpose: an API outage must never be reported to the user as
# "that isn't a real job". See the NOT_A_JOB sentinel in engine.py.
DISCOVERY_UNAVAILABLE = ("امکان ایجاد شغل پیشنهادی در حال حاضر فراهم نیست؛ "
                         "نزدیک‌ترین مشاغل موجود در پایگاه داده به شرح زیر است:")
DISCOVERY_NOT_REAL = ("آنچه توصیف کرده‌اید به شغلی واقعی اشاره ندارد، بنابراین شغلی برای آن ایجاد نشد؛ "
                      "نزدیک‌ترین مشاغل موجود در پایگاه داده به شرح زیر است:")
