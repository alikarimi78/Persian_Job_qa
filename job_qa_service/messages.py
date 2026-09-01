# -*- coding: utf-8 -*-
"""Fixed Persian text shown to the user.

Separate from `prompts.py` on purpose: those are instructions to the model, these are
sentences the user reads verbatim — including on every path where the LLM produced
nothing, since each caller falls back to a template so the endpoint never fails
because the API did.
"""

OOD_MESSAGE = "متاسفانه در پایگاه داده سامانه، اطلاعاتی درباره این موضوع یافت نشد."

# Asked about itself rather than about an occupation — «کار تو چیه؟», «هدف شما چیست؟».
# There is no record that answers that, so before this text existed the question was
# answered from whichever record its two content words happened to point at. It says
# what the service does and what may be asked of it, because that is what the person
# is actually after: they are looking for the way in, not for a job description.
ABOUT_MESSAGE = (
    "من دستیار تحلیل مشاغل این سامانه هستم و تنها به پرسش‌های مرتبط با مشاغل پاسخ می‌دهم.\n\n"
    "آنچه می‌توانید از این سامانه بخواهید:\n"
    "- پرسش درباره یک شغل مشخص: وظایف، مهارت‌ها، دانش، توانایی‌ها، ابزارها، محیط کاری و "
    "مسیر ارتقای آن. برای نمونه: «وظایف افسر توپخانه چیست؟»\n"
    "- جست‌وجوی پیشرفته: مهارت‌ها و توانمندی‌های خود را وارد نمایید تا نزدیک‌ترین مشاغل "
    "به پروفایل شما رتبه‌بندی و میزان هم‌خوانی هر مورد مشخص شود.\n"
    "- شغلی که در پایگاه داده موجود نیست: آن را توصیف نمایید تا شغلی متناسب با آن پیشنهاد "
    "شود؛ پس از بررسی و ثبت شما، برای تایید به مدیر سامانه ارسال می‌گردد.\n\n"
    "پرسش خود را درباره شغل مورد نظر وارد نمایید."
)

# A greeting is the same question asked with no question in it — the person is looking
# for the way in. It answers with the greeting returned and then the *same* body, built
# by concatenation rather than written twice: whoever edits what this service says about
# itself should not have to find two copies of it.
GREETING_MESSAGE = "با سلام و احترام؛ در خدمت شما هستم.\n\n" + ABOUT_MESSAGE

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
