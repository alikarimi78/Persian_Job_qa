#!/usr/bin/env python3
"""
build_military_enrichment.py — write `military_enrichment.json`, the taxonomy items the
102 military occupations were missing.

    python3 build_military_enrichment.py

The military rows were authored, not translated: three `skills`, three `knowledge`, three
`abilities` and one `work_context` each, against an O*NET median of 7 / 8 / 19 / 18. What
they hold is good and specific — «بالستیک برد بلند», «اثر باد و شرایط جوی بر گلوله» — and
none of it is thrown away. What they lack is the *shared* vocabulary: `profile.coverage`
is set arithmetic over items, so a record with three skills can cover less of a rich
profile than one with eight, and the military occupations sank below the O*NET ones in
advanced search even on military profiles.

So the missing items are **selected from the four fixed O*NET taxonomies** rather than
invented. That is the whole design: the same 10 / 33 / 52 / 55 canonical phrases every
O*NET row is described with, chosen per occupation. Inventing new phrasing would have
produced items no user's profile could ever match — the exact problem this is fixing.

The selection is composed rather than written out 102 times:

  * `BASE` is what is true of every military occupation — the basic communication skills,
    the abilities O*NET gives almost every job, and the work context of a uniformed,
    team-organised, safety-critical service.

**The order matters, and it is most-specific-first**: the per-occupation `EXTRA`, then the
bundles, then `BASE`. `profile.coverage` divides by the *user's* item count, so a record
holding more items can only ever score higher — which means a military row given more
items than a comparable O*NET row wins on volume rather than on fit. A first pass did
exactly that (26 abilities against an O*NET median of 19) and put «متخصصان جنگ الکترونیک
زمینی» above «برنامه‌نویسان کامپیوتر» for a programmer's profile. `merge_occupations_fa.py`
therefore caps each column at the O*NET median, and this order is what decides which items
survive the cap: the generic ones are the ones dropped.
  * `BUNDLES` are the traits that recur across families — `FIELD`, `PHYSICAL`, `DESK`,
    `ANALYTIC`, `TECH`, `LEAD`, `HAZARD`, `VEHICLE`, `MEDICAL`, `COMMS`, `PRECISION`,
    `VIGILANCE`, `PUBLIC`. An occupation names the ones that describe it.
  * `EXTRA` is what only one occupation needs — «قرار گرفتن در معرض تابش» for the CBRN
    specialists, «هنرهای زیبا» for the band.

Every phrase is checked against `translation_fixes/40_taxonomy_canonical.json` on every
run, so a typo is a failure here rather than an item that silently matches nothing.

`responsibilities` and `tools` are the two columns that cannot be filled this way, and are
why `military_responsibilities_fa.json` and `military_tools_extra_fa.json` sit beside this
file. Task statements belong to one occupation and to nothing else — there is no taxonomy
to draw «تنظیم و صفر کردن سلاح و ادوات نوری» from — and equipment is a free list rather
than a closed vocabulary. Both are written by hand, six or seven per occupation, and
merged into the same table. What is checked for them is only that every occupation named
in one file is named in all of them.

`tools` matters least of the six: it is deliberately **not** a `PROFILE_FIELDS` column, so
nothing in advanced search reads it, and it earns its place only through the dense channel
of `engine._combined_text`.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANONICAL = HERE.parent / "translation_fixes" / "40_taxonomy_canonical.json"

# --- what every military occupation shares -------------------------------------------
BASE = {
    "skills": ["گوش دادن فعال", "سخن گفتن", "تفکر انتقادی", "درک مطلب"],
    "knowledge": ["ایمنی و امنیت عمومی"],
    "abilities": ["درک شفاهی", "بیان شفاهی", "تشخیص گفتار", "وضوح گفتار",
                  "حساسیت به مسئله", "استدلال قیاسی", "توجه انتخابی",
                  "ترتیب‌دهی اطلاعات"],
    "work_context": ["گفتگوهای چهره‌به‌چهره با افراد و درون تیم‌ها",
                     "کار با یا مشارکت در یک گروه کاری یا تیم",
                     "ارتباط با دیگران",
                     "سلامت و ایمنی سایر کارکنان",
                     "اهمیت دقیق یا درست بودن",
                     "پیامد خطا",
                     "فشار زمانی"],
}

BUNDLES = {
    # outdoors, on foot, in the weather
    "FIELD": {
        "knowledge": ["جغرافیا"],
        "abilities": ["بینایی دور", "جهت‌گیری فضایی", "استقامت"],
        "work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی",
                         "قرار گرفتن در معرض دماهای بسیار گرم یا سرد",
                         "صرف زمان برای راه رفتن یا دویدن",
                         "صرف زمان برای ایستادن",
                         "استفاده از تجهیزات حفاظتی یا ایمنی معمول مانند کفش ایمنی، عینک، دستکش، محافظ گوش، کلاه ایمنی یا جلیقه نجات"],
    },
    # carrying, lifting, climbing, crawling
    "PHYSICAL": {
        "abilities": ["قدرت ایستا", "قدرت تنه", "قدرت پویا", "هماهنگی کلی بدن",
                      "هماهنگی چند عضو", "تعادل کلی بدن", "انعطاف‌پذیری دامنه حرکت"],
        "work_context": ["صرف زمان برای خم کردن یا چرخاندن بدن",
                         "صرف زمان برای زانو زدن، چمباتمه زدن، خم شدن یا خزیدن",
                         "نزدیکی فیزیکی"],
    },
    # watching for something that may not come
    "VIGILANCE": {
        "abilities": ["بینایی در شب", "بینایی محیطی", "درک عمق", "توجه شنیداری",
                      "سرعت ادراکی", "زمان عکس‌العمل"],
        "work_context": ["قرار گرفتن در معرض شرایط نوری بسیار روشن یا ناکافی"],
    },
    # a desk, a screen, a chain of correspondence
    "DESK": {
        "knowledge": ["اداری", "رایانه و الکترونیک"],
        "abilities": ["درک کتبی", "بیان کتبی", "بینایی نزدیک"],
        "work_context": ["محیط داخلی، دارای کنترل شرایط محیطی", "صرف زمان برای نشستن",
                         "ایمیل", "نامه‌ها و یادداشت‌های کتبی", "مکالمات تلفنی"],
    },
    # reading, reasoning, writing it up
    "ANALYTIC": {
        "skills": ["نگارش", "یادگیری فعال", "نظارت"],
        "abilities": ["استدلال استقرایی", "انعطاف‌پذیری دسته‌بندی", "درک کتبی",
                      "بیان کتبی", "تقسیم توجه"],
        "work_context": ["آزادی در تصمیم‌گیری", "فراوانی تصمیم‌گیری",
                         "تعیین وظایف، اولویت‌ها و اهداف"],
    },
    # instruments, wiring, maintenance
    "TECH": {
        "skills": ["ریاضیات"],
        "knowledge": ["مهندسی و فناوری", "مکانیک", "فیزیک", "رایانه و الکترونیک"],
        "abilities": ["بینایی نزدیک", "مهارت دستی", "مهارت انگشتان",
                      "ثبات دست و بازو", "دقت کنترل", "تجسم"],
        "work_context": ["صرف زمان برای استفاده از دست‌ها جهت جابجایی، کنترل یا لمس اشیاء، ابزارها یا کنترل‌ها",
                         "قرار گرفتن در معرض تجهیزات خطرناک",
                         "قرار گرفتن در معرض صداها و سطوح نویزی که حواس‌پرت‌کننده یا آزاردهنده هستند"],
    },
    # answerable for other people's work
    "LEAD": {
        "knowledge": ["مدیریت و اداره", "پرسنل و منابع انسانی"],
        "abilities": ["روانی ایده‌ها"],
        "work_context": ["هماهنگی یا هدایت دیگران در انجام فعالیت‌های کاری",
                         "آزادی در تصمیم‌گیری",
                         "تعیین وظایف، اولویت‌ها و اهداف",
                         "تأثیر تصمیمات بر همکاران یا نتایج شرکت",
                         "پیامدهای کاری و نتایج سایر کارکنان"],
    },
    # the work can kill you if done wrong
    "HAZARD": {
        "abilities": ["زمان عکس‌العمل", "جهت‌گیری پاسخ"],
        "work_context": ["قرار گرفتن در معرض شرایط خطرناک",
                         "قرار گرفتن در معرض تجهیزات خطرناک",
                         "استفاده از تجهیزات حفاظتی یا ایمنی تخصصی مانند دستگاه تنفس، هارنس ایمنی، لباس‌های محافظ کامل یا محافظت در برابر تشعشع",
                         "قرار گرفتن در معرض سوختگی‌های جزئی، بریدگی‌ها، گاز گرفتگی‌ها یا نیش‌ها"],
    },
    # driving, flying, sailing something
    "VEHICLE": {
        "knowledge": ["حمل و نقل"],
        "abilities": ["دقت کنترل", "کنترل نرخ", "زمان عکس‌العمل", "سرعت حرکت اعضا",
                      "هماهنگی چند عضو", "بینایی دور", "درک عمق"],
        "work_context": ["در یک وسیله نقلیه محصور یا کار با تجهیزات محصور",
                         "قرار گرفتن در معرض ارتعاش کل بدن",
                         "قرار گرفتن در معرض صداها و سطوح نویزی که حواس‌پرت‌کننده یا آزاردهنده هستند"],
    },
    "MEDICAL": {
        "skills": ["علوم"],
        "knowledge": ["پزشکی و دندان‌پزشکی", "زیست‌شناسی", "درمان و مشاوره",
                      "روان‌شناسی", "خدمات مشتری و شخصی"],
        "abilities": ["حفظ کردن", "مهارت انگشتان"],
        "work_context": ["قرار گرفتن در معرض بیماری‌ها یا عفونت‌ها",
                         "برخورد با افراد ناخوشایند، عصبانی یا بی‌ادب"],
    },
    "COMMS": {
        "knowledge": ["مخابرات", "رایانه و الکترونیک"],
        "abilities": ["توجه شنیداری", "حساسیت شنوایی", "تقسیم توجه", "سرعت ادراکی"],
        "work_context": ["مکالمات تلفنی", "سرعت تعیین‌شده توسط سرعت تجهیزات"],
    },
    # very small movements, very exactly
    "PRECISION": {
        "abilities": ["ثبات دست و بازو", "مهارت انگشتان", "بینایی نزدیک",
                      "دقت کنترل", "سرعت مچ و انگشتان"],
        "work_context": ["اهمیت تکرار وظایف یکسان",
                         "صرف زمان برای انجام حرکات تکراری"],
    },
    # standing in front of people who are not soldiers
    "PUBLIC": {
        "knowledge": ["ارتباطات و رسانه", "خدمات مشتری و شخصی", "روان‌شناسی"],
        "work_context": ["تعامل با مشتریان خارجی یا عموم مردم", "سخنرانی عمومی",
                         "موقعیت‌های تعارض"],
    },
}

# occupation -> the bundles that describe it, then anything only it needs
ROWS: dict[str, tuple[list[str], dict]] = {
    "تک‌تیراندازان نظامی": (["FIELD", "PHYSICAL", "VIGILANCE", "HAZARD", "PRECISION"],
        {"skills": ["ریاضیات"], "knowledge": ["فیزیک"],
         "abilities": ["استدلال ریاضی", "توانایی عددی"]}),
    "متخصصان شناسایی و دیده‌بانی": (["FIELD", "PHYSICAL", "VIGILANCE", "HAZARD"],
        {"abilities": ["حفظ کردن", "درک کتبی", "بیان کتبی"]}),
    "متخصصان جنگ شهری": (["FIELD", "PHYSICAL", "VIGILANCE", "HAZARD"],
        {"work_context": ["برخورد با افراد خشن یا از نظر فیزیکی پرخاشگر",
                          "قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب"]}),
    "خدمه پدافند ضدزره": (["FIELD", "PHYSICAL", "VIGILANCE", "HAZARD", "TECH"], {}),
    "خدمه خمپاره‌انداز": (["FIELD", "PHYSICAL", "HAZARD", "TECH"],
        {"skills": ["ریاضیات"], "abilities": ["استدلال ریاضی", "توانایی عددی"]}),
    "دیده‌بان‌های آتش توپخانه": (["FIELD", "VIGILANCE", "HAZARD", "COMMS"],
        {"skills": ["ریاضیات"], "knowledge": ["جغرافیا"],
         "abilities": ["استدلال ریاضی", "توانایی عددی", "بینایی دور"]}),
    "متخصصان مهندسی رزمی": (["FIELD", "PHYSICAL", "HAZARD", "TECH"],
        {"knowledge": ["ساختمان و ساخت‌وساز", "طراحی"]}),
    "متخصصان خنثی‌سازی مهمات عمل‌نکرده": (["FIELD", "HAZARD", "TECH", "PRECISION"],
        {"knowledge": ["شیمی", "فیزیک"],
         "work_context": ["قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب"]}),
    "متخصصان پاکسازی میادین مین": (["FIELD", "PHYSICAL", "HAZARD", "PRECISION"],
        {"abilities": ["انعطاف‌پذیری بستار", "سرعت بستار"]}),
    "متخصصان پل‌سازی و عبور نظامی": (["FIELD", "PHYSICAL", "HAZARD", "TECH"],
        {"knowledge": ["ساختمان و ساخت‌وساز"], "abilities": ["استدلال ریاضی"]}),
    "رانندگان خودروهای تاکتیکی": (["FIELD", "VEHICLE", "VIGILANCE", "HAZARD"], {}),
    "تکنسین‌های تعمیر خودروهای زرهی": (["TECH", "PHYSICAL", "HAZARD"],
        {"work_context": ["قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب",
                          "قرار گرفتن در معرض آلاینده‌ها",
                          "محیط داخلی، بدون کنترل شرایط محیطی"]}),
    "متخصصان جنگ الکترونیک زمینی": (["TECH", "COMMS", "ANALYTIC", "DESK"],
        {"knowledge": ["ریاضیات"]}),
    "اپراتورهای پهپاد تاکتیکی": (["TECH", "VIGILANCE", "COMMS", "DESK"],
        {"knowledge": ["حمل و نقل", "جغرافیا"],
         "abilities": ["دقت کنترل", "هماهنگی چند عضو", "تقسیم توجه"]}),
    "مربیان و مأموران سگ‌های نظامی": (["FIELD", "PHYSICAL", "VIGILANCE"],
        {"skills": ["راهبردهای یادگیری"],
         "knowledge": ["زیست‌شناسی", "آموزش و پرورش", "روان‌شناسی"],
         "work_context": ["قرار گرفتن در معرض سوختگی‌های جزئی، بریدگی‌ها، گاز گرفتگی‌ها یا نیش‌ها",
                          "قرار گرفتن در معرض بیماری‌ها یا عفونت‌ها"]}),
    "متخصصان استتار و اختفا": (["FIELD", "PHYSICAL", "VIGILANCE"],
        {"knowledge": ["طراحی"],
         "abilities": ["تشخیص رنگ بصری", "اصالت", "روانی ایده‌ها", "تجسم"]}),
    "متخصصان عملیات کوهستان": (["FIELD", "PHYSICAL", "HAZARD", "VIGILANCE"],
        {"skills": ["راهبردهای یادگیری"],
         "abilities": ["استقامت", "قدرت پویا", "انعطاف‌پذیری پویا"],
         "work_context": ["قرار گرفتن در معرض ارتفاعات بالا",
                          "صرف زمان برای بالا رفتن از نردبان‌ها، داربست‌ها یا تیرها",
                          "صرف زمان برای حفظ یا بازیابی تعادل"]}),
    "متخصصان عملیات چترباز": (["FIELD", "PHYSICAL", "HAZARD", "VIGILANCE"],
        {"abilities": ["قدرت انفجاری", "انعطاف‌پذیری پویا", "جهت‌گیری فضایی"],
         "work_context": ["قرار گرفتن در معرض ارتفاعات بالا",
                          "در یک وسیله نقلیه روباز یا کار با تجهیزات"]}),
    "متخصصان پدافند شیمیایی، زیستی و پرتوی": (["FIELD", "HAZARD", "TECH"],
        {"skills": ["علوم"], "knowledge": ["شیمی", "زیست‌شناسی", "فیزیک"],
         "work_context": ["قرار گرفتن در معرض تابش",
                          "قرار گرفتن در معرض آلاینده‌ها",
                          "قرار گرفتن در معرض بیماری‌ها یا عفونت‌ها"]}),
    "متخصصان پدافند غیرعامل": (["ANALYTIC", "DESK", "LEAD"],
        {"knowledge": ["ساختمان و ساخت‌وساز", "جغرافیا", "مهندسی و فناوری"],
         "abilities": ["تجسم"]}),
}

ROWS.update({
    # --- air ---------------------------------------------------------------------
    "خلبانان جنگنده": (["VEHICLE", "TECH", "VIGILANCE", "HAZARD", "ANALYTIC"],
        {"skills": ["ریاضیات"], "knowledge": ["فیزیک", "جغرافیا"],
         "abilities": ["جهت‌گیری فضایی", "تقسیم توجه", "استدلال ریاضی",
                       "حساسیت به درخشندگی شدید"],
         "work_context": ["قرار گرفتن در معرض ارتفاعات بالا", "سطح رقابت"]}),
    "خلبانان بالگرد نظامی": (["VEHICLE", "TECH", "VIGILANCE", "HAZARD"],
        {"knowledge": ["جغرافیا", "فیزیک"],
         "abilities": ["جهت‌گیری فضایی", "تقسیم توجه", "هماهنگی چند عضو"],
         "work_context": ["قرار گرفتن در معرض ارتفاعات بالا"]}),
    "خلبانان هواپیمای ترابری نظامی": (["VEHICLE", "TECH", "VIGILANCE", "ANALYTIC"],
        {"knowledge": ["جغرافیا"],
         "abilities": ["جهت‌گیری فضایی", "تقسیم توجه"],
         "work_context": ["قرار گرفتن در معرض ارتفاعات بالا"]}),
    "افسران سامانه‌های تسلیحاتی هوایی": (["TECH", "VIGILANCE", "ANALYTIC", "COMMS"],
        {"skills": ["ریاضیات"], "abilities": ["استدلال ریاضی", "تقسیم توجه"],
         "work_context": ["در یک وسیله نقلیه محصور یا کار با تجهیزات محصور"]}),
    "کنترل‌کنندگان ترافیک هوایی نظامی": (["DESK", "COMMS", "VIGILANCE", "ANALYTIC"],
        {"knowledge": ["حمل و نقل", "جغرافیا"],
         "abilities": ["تقسیم توجه", "جهت‌گیری فضایی", "حفظ کردن", "تجسم"],
         "work_context": ["سرعت تعیین‌شده توسط سرعت تجهیزات"]}),
    "اپراتورهای رادار پدافند هوایی": (["DESK", "TECH", "COMMS", "VIGILANCE"],
        {"abilities": ["سرعت ادراکی", "تقسیم توجه", "انعطاف‌پذیری بستار"]}),
    "خدمه سامانه‌های موشکی پدافند هوایی": (["TECH", "HAZARD", "COMMS", "FIELD"],
        {"skills": ["ریاضیات"], "abilities": ["استدلال ریاضی", "زمان عکس‌العمل"]}),
    "خدمه توپخانه پدافند هوایی": (["FIELD", "PHYSICAL", "TECH", "HAZARD"],
        {"skills": ["ریاضیات"], "abilities": ["استدلال ریاضی", "زمان عکس‌العمل",
                                              "بینایی دور"]}),
    "افسران هدایت عملیات هوایی": (["DESK", "ANALYTIC", "COMMS", "LEAD"],
        {"knowledge": ["جغرافیا", "حمل و نقل"], "abilities": ["تقسیم توجه", "تجسم"]}),
    "تکنسین‌های موتور هواپیمای نظامی": (["TECH", "PRECISION", "HAZARD", "PHYSICAL"],
        {"work_context": ["قرار گرفتن در معرض آلاینده‌ها",
                          "محیط داخلی، بدون کنترل شرایط محیطی",
                          "قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب"]}),
    "تکنسین‌های سازه و بدنه هواپیما": (["TECH", "PRECISION", "PHYSICAL", "HAZARD"],
        {"knowledge": ["طراحی", "تولید و فرآوری"],
         "work_context": ["صرف زمان برای بالا رفتن از نردبان‌ها، داربست‌ها یا تیرها",
                          "قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب"]}),
    "متخصصان تسلیحات هوایی": (["TECH", "PHYSICAL", "HAZARD", "PRECISION"],
        {"work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی"]}),
    "تکنسین‌های تجهیزات نجات و چتر": (["TECH", "PRECISION", "HAZARD"],
        {"knowledge": ["تولید و فرآوری"],
         "work_context": ["محیط داخلی، دارای کنترل شرایط محیطی"]}),
    "متخصصان سوخت‌رسانی هوایی": (["FIELD", "PHYSICAL", "HAZARD", "TECH"],
        {"knowledge": ["شیمی"],
         "work_context": ["قرار گرفتن در معرض آلاینده‌ها"]}),
    "خدمه سوخت‌گیری در پرواز": (["TECH", "VIGILANCE", "HAZARD", "PRECISION"],
        {"abilities": ["درک عمق", "جهت‌گیری فضایی", "دقت کنترل"],
         "work_context": ["در یک وسیله نقلیه محصور یا کار با تجهیزات محصور",
                          "قرار گرفتن در معرض ارتفاعات بالا",
                          "صرف زمان برای زانو زدن، چمباتمه زدن، خم شدن یا خزیدن"]}),
    "بازرسان ایمنی پرواز نظامی": (["ANALYTIC", "DESK", "TECH", "LEAD"],
        {"knowledge": ["حمل و نقل", "قانون و دولت"]}),
    "اپراتورهای پهپاد راهبردی": (["DESK", "TECH", "COMMS", "VIGILANCE", "ANALYTIC"],
        {"knowledge": ["جغرافیا", "حمل و نقل"],
         "abilities": ["تقسیم توجه", "جهت‌گیری فضایی"]}),
    "متخصصان هواشناسی نظامی": (["ANALYTIC", "DESK", "TECH"],
        {"skills": ["علوم", "ریاضیات"],
         "knowledge": ["فیزیک", "جغرافیا", "ریاضیات"],
         "abilities": ["استدلال ریاضی", "توانایی عددی", "تجسم"]}),
    "امدادگران نجات هوایی": (["FIELD", "PHYSICAL", "HAZARD", "MEDICAL", "VIGILANCE"],
        {"abilities": ["قدرت پویا", "استقامت"],
         "work_context": ["قرار گرفتن در معرض ارتفاعات بالا",
                          "در یک وسیله نقلیه روباز یا کار با تجهیزات"]}),
    "متخصصان تجهیزات پشتیبانی زمینی پرواز": (["TECH", "PHYSICAL", "HAZARD"],
        {"work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی",
                          "قرار گرفتن در معرض آلاینده‌ها"]}),

    # --- sea ---------------------------------------------------------------------
    "افسران ناوبری دریایی": (["VEHICLE", "ANALYTIC", "COMMS", "LEAD", "VIGILANCE"],
        {"skills": ["ریاضیات"], "knowledge": ["جغرافیا", "فیزیک"],
         "abilities": ["استدلال ریاضی", "جهت‌گیری فضایی", "تجسم"],
         "work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی"]}),
    "افسران مانور و عملیات عرشه": (["FIELD", "PHYSICAL", "LEAD", "HAZARD", "COMMS"],
        {"knowledge": ["حمل و نقل"],
         "work_context": ["صرف زمان برای حفظ یا بازیابی تعادل",
                          "قرار گرفتن در معرض ارتعاش کل بدن"]}),
    "متخصصان سونار و جنگ زیرسطحی": (["DESK", "TECH", "COMMS", "VIGILANCE", "ANALYTIC"],
        {"knowledge": ["فیزیک"],
         "abilities": ["حساسیت شنوایی", "توجه شنیداری", "مکان‌یابی صدا",
                       "انعطاف‌پذیری بستار"]}),
    "خدمه زیردریایی": (["TECH", "HAZARD", "COMMS", "VIGILANCE"],
        {"work_context": ["قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب",
                          "در یک وسیله نقلیه محصور یا کار با تجهیزات محصور",
                          "محیط داخلی، بدون کنترل شرایط محیطی",
                          "نزدیکی فیزیکی"]}),
    "غواصان نظامی": (["PHYSICAL", "HAZARD", "TECH", "VIGILANCE"],
        {"skills": ["علوم"], "knowledge": ["فیزیک", "زیست‌شناسی"],
         "abilities": ["استقامت", "تعادل کلی بدن", "درک عمق"],
         "work_context": ["قرار گرفتن در معرض دماهای بسیار گرم یا سرد",
                          "قرار گرفتن در معرض شرایط نوری بسیار روشن یا ناکافی"]}),
    "تفنگداران دریایی": (["FIELD", "PHYSICAL", "VIGILANCE", "HAZARD"],
        {"work_context": ["برخورد با افراد خشن یا از نظر فیزیکی پرخاشگر"]}),
    "متخصصان مین‌روبی دریایی": (["TECH", "HAZARD", "VIGILANCE", "PRECISION"],
        {"knowledge": ["فیزیک"],
         "work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی"]}),
    "تکنسین‌های موتورخانه شناور": (["TECH", "PHYSICAL", "HAZARD"],
        {"work_context": ["قرار گرفتن در معرض دماهای بسیار گرم یا سرد",
                          "قرار گرفتن در معرض آلاینده‌ها",
                          "محیط داخلی، بدون کنترل شرایط محیطی",
                          "قرار گرفتن در معرض ارتعاش کل بدن"]}),
    "تکنسین‌های برق و توزیع نیروی شناور": (["TECH", "HAZARD", "PRECISION"],
        {"knowledge": ["فیزیک"],
         "work_context": ["قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب"]}),
    "متخصصان سامانه‌های تسلیحاتی دریایی": (["TECH", "HAZARD", "COMMS", "PRECISION"],
        {"skills": ["ریاضیات"], "abilities": ["استدلال ریاضی"]}),
    "متخصصان مخابرات دریایی": (["COMMS", "DESK", "TECH", "ANALYTIC"], {}),
    "خدمه عرشه پروازی ناو": (["FIELD", "PHYSICAL", "HAZARD", "VIGILANCE"],
        {"work_context": ["قرار گرفتن در معرض صداها و سطوح نویزی که حواس‌پرت‌کننده یا آزاردهنده هستند",
                          "سرعت تعیین‌شده توسط سرعت تجهیزات",
                          "صرف زمان برای حفظ یا بازیابی تعادل"]}),
    "متخصصان کنترل خسارت شناور": (["PHYSICAL", "HAZARD", "TECH", "VIGILANCE"],
        {"knowledge": ["شیمی"],
         "work_context": ["قرار گرفتن در معرض دماهای بسیار گرم یا سرد",
                          "قرار گرفتن در معرض آلاینده‌ها",
                          "قرار گرفتن در معرض فضای کاری تنگ، موقعیت‌های بدنی نامناسب"]}),
    "متخصصان جستجو و نجات دریایی": (["FIELD", "PHYSICAL", "HAZARD", "MEDICAL",
                                      "VIGILANCE", "VEHICLE"], {}),
    "ناخدایان شناورهای گشتی": (["VEHICLE", "LEAD", "COMMS", "VIGILANCE", "ANALYTIC"],
        {"knowledge": ["جغرافیا", "قانون و دولت"],
         "abilities": ["جهت‌گیری فضایی"],
         "work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی"]}),
    "متخصصان هیدروگرافی نظامی": (["ANALYTIC", "TECH", "DESK"],
        {"skills": ["ریاضیات", "علوم"],
         "knowledge": ["جغرافیا", "ریاضیات", "فیزیک"],
         "abilities": ["استدلال ریاضی", "توانایی عددی", "تجسم"],
         "work_context": ["فضای باز، در معرض تمام شرایط آب و هوایی"]}),
    "متخصصان لجستیک و بارگیری دریایی": (["PHYSICAL", "ANALYTIC", "HAZARD", "DESK"],
        {"skills": ["ریاضیات"], "knowledge": ["حمل و نقل"],
         "abilities": ["استدلال ریاضی", "توانایی عددی", "تجسم"]}),
    "تکنسین‌های تعمیر بدنه و جوش زیرآب": (["TECH", "PHYSICAL", "HAZARD", "PRECISION"],
        {"knowledge": ["فیزیک", "تولید و فرآوری"],
         "abilities": ["درک عمق", "استقامت"],
         "work_context": ["قرار گرفتن در معرض شرایط نوری بسیار روشن یا ناکافی",
                          "قرار گرفتن در معرض دماهای بسیار گرم یا سرد"]}),
    "متخصصان امنیت بندری و حفاظت سواحل": (["FIELD", "VIGILANCE", "PUBLIC", "COMMS"],
        {"knowledge": ["قانون و دولت", "حمل و نقل"],
         "work_context": ["برخورد با افراد ناخوشایند، عصبانی یا بی‌ادب"]}),
    "متخصصان تدارکات و تغذیه شناور": (["PHYSICAL", "DESK"],
        {"knowledge": ["تولید مواد غذایی", "خدمات مشتری و شخصی", "اقتصاد و حسابداری"],
         "work_context": ["قرار گرفتن در معرض دماهای بسیار گرم یا سرد",
                          "اهمیت تکرار وظایف یکسان",
                          "محیط داخلی، بدون کنترل شرایط محیطی"]}),
})

ROWS.update({
    # --- signals, cyber, intelligence ---------------------------------------------
    "متخصصان مخابرات نظامی": (["COMMS", "TECH", "DESK", "ANALYTIC"], {}),
    "اپراتورهای ارتباطات ماهواره‌ای": (["COMMS", "TECH", "DESK", "ANALYTIC"],
        {"skills": ["ریاضیات"], "knowledge": ["فیزیک"],
         "abilities": ["استدلال ریاضی"]}),
    "تکنسین‌های شبکه‌های تاکتیکی": (["COMMS", "TECH", "ANALYTIC", "FIELD"],
        {"knowledge": ["رایانه و الکترونیک"]}),
    "متخصصان امنیت ارتباطات و رمزنگاری": (["COMMS", "TECH", "ANALYTIC", "DESK"],
        {"skills": ["ریاضیات"], "knowledge": ["ریاضیات", "قانون و دولت"],
         "abilities": ["استدلال ریاضی", "انعطاف‌پذیری بستار"]}),
    "تحلیلگران دفاع سایبری": (["DESK", "ANALYTIC", "TECH", "COMMS"],
        {"knowledge": ["ریاضیات"],
         "abilities": ["انعطاف‌پذیری بستار", "سرعت بستار", "استدلال ریاضی"]}),
    "متخصصان پاسخ به رخدادهای سایبری": (["DESK", "ANALYTIC", "TECH", "COMMS"],
        {"abilities": ["انعطاف‌پذیری بستار", "زمان عکس‌العمل", "تقسیم توجه"],
         "work_context": ["سرعت تعیین‌شده توسط سرعت تجهیزات"]}),
    "تحلیلگران تهدیدات سایبری": (["DESK", "ANALYTIC", "TECH"],
        {"knowledge": ["ارتباطات و رسانه", "زبان انگلیسی"],
         "abilities": ["انعطاف‌پذیری بستار", "اصالت"]}),
    "مدیران امنیت شبکه‌های نظامی": (["DESK", "ANALYTIC", "TECH", "LEAD", "COMMS"],
        {"knowledge": ["قانون و دولت"]}),
    "تحلیلگران اطلاعات نظامی": (["DESK", "ANALYTIC"],
        {"knowledge": ["جغرافیا", "جامعه‌شناسی و انسان‌شناسی", "تاریخ و باستان‌شناسی",
                       "قانون و دولت"],
         "abilities": ["حفظ کردن", "انعطاف‌پذیری بستار"]}),
    "تحلیلگران تصاویر ماهواره‌ای و هوایی": (["DESK", "ANALYTIC", "TECH"],
        {"knowledge": ["جغرافیا", "فیزیک"],
         "abilities": ["بینایی نزدیک", "سرعت ادراکی", "انعطاف‌پذیری بستار",
                       "تشخیص رنگ بصری", "تجسم", "درک عمق"]}),
    "تحلیلگران اطلاعات سیگنالی": (["DESK", "ANALYTIC", "TECH", "COMMS"],
        {"knowledge": ["ریاضیات", "زبان خارجی"],
         "abilities": ["توجه شنیداری", "انعطاف‌پذیری بستار", "حفظ کردن"]}),
    "تحلیلگران اطلاعات منابع باز": (["DESK", "ANALYTIC"],
        {"knowledge": ["ارتباطات و رسانه", "زبان انگلیسی", "زبان خارجی", "جغرافیا",
                       "جامعه‌شناسی و انسان‌شناسی"],
         "abilities": ["سرعت ادراکی", "انعطاف‌پذیری بستار"]}),
    "افسران ضداطلاعات": (["DESK", "ANALYTIC", "PUBLIC"],
        {"knowledge": ["قانون و دولت", "روان‌شناسی", "جامعه‌شناسی و انسان‌شناسی"],
         "abilities": ["حفظ کردن", "استدلال استقرایی"],
         "work_context": ["موقعیت‌های تعارض"]}),
    "زبان‌شناسان و مترجمان نظامی": (["DESK", "ANALYTIC"],
        {"knowledge": ["زبان خارجی", "زبان انگلیسی", "جامعه‌شناسی و انسان‌شناسی",
                       "تاریخ و باستان‌شناسی", "ارتباطات و رسانه"],
         "abilities": ["حفظ کردن", "توجه شنیداری", "تشخیص گفتار", "سرعت بستار"]}),
    "متخصصان گردآوری اطلاعات انسانی": (["DESK", "ANALYTIC", "PUBLIC", "FIELD"],
        {"knowledge": ["روان‌شناسی", "جامعه‌شناسی و انسان‌شناسی", "زبان خارجی"],
         "abilities": ["حفظ کردن", "استدلال استقرایی"]}),
    "متخصصان اطلاع‌رسانی و ارتباطات عملیاتی": (["DESK", "ANALYTIC", "PUBLIC", "LEAD"],
        {"knowledge": ["ارتباطات و رسانه", "طراحی"],
         "abilities": ["اصالت", "روانی ایده‌ها"]}),
    "افسران طبقه‌بندی و حفاظت اسناد": (["DESK", "ANALYTIC"],
        {"knowledge": ["قانون و دولت", "اداری"],
         "abilities": ["حفظ کردن", "سرعت ادراکی"],
         "work_context": ["اهمیت تکرار وظایف یکسان"]}),
    "متخصصان ژئواینتلیجنس و تحلیل مکانی": (["DESK", "ANALYTIC", "TECH"],
        {"skills": ["ریاضیات"], "knowledge": ["جغرافیا", "ریاضیات"],
         "abilities": ["تجسم", "جهت‌گیری فضایی", "استدلال ریاضی", "تشخیص رنگ بصری"]}),
    "تکنسین‌های تجهیزات رمز و امن‌سازی": (["TECH", "COMMS", "PRECISION", "DESK"],
        {"knowledge": ["ریاضیات"]}),
    "متخصصان مدیریت طیف فرکانسی": (["DESK", "ANALYTIC", "TECH", "COMMS"],
        {"skills": ["ریاضیات"], "knowledge": ["فیزیک", "ریاضیات", "قانون و دولت"],
         "abilities": ["استدلال ریاضی", "توانایی عددی"]}),

    # --- medical ------------------------------------------------------------------
    "پزشکان نظامی": (["MEDICAL", "ANALYTIC", "DESK", "LEAD", "PRECISION"],
        {"skills": ["علوم", "راهبردهای یادگیری"], "knowledge": ["شیمی"],
         "abilities": ["حساسیت به مسئله", "استدلال استقرایی"]}),
    "امدادگران رزمی": (["MEDICAL", "FIELD", "PHYSICAL", "HAZARD", "VIGILANCE"],
        {"abilities": ["زمان عکس‌العمل", "جهت‌گیری پاسخ"],
         "work_context": ["برخورد با افراد ناخوشایند، عصبانی یا بی‌ادب"]}),
    "پرستاران نظامی": (["MEDICAL", "PHYSICAL", "ANALYTIC", "PRECISION"],
        {"work_context": ["صرف زمان برای ایستادن", "صرف زمان برای راه رفتن یا دویدن",
                          "محیط داخلی، دارای کنترل شرایط محیطی"]}),
    "تکنسین‌های بهداشت میدانی": (["MEDICAL", "FIELD", "ANALYTIC", "TECH"],
        {"skills": ["علوم"], "knowledge": ["شیمی", "زیست‌شناسی"],
         "work_context": ["قرار گرفتن در معرض آلاینده‌ها"]}),
    "متخصصان تخلیه پزشکی": (["MEDICAL", "PHYSICAL", "VEHICLE", "HAZARD", "COMMS"],
        {"abilities": ["زمان عکس‌العمل", "قدرت پویا"]}),
    "کارشناسان بهداشت محیط و ایمنی نظامی": (["ANALYTIC", "DESK", "MEDICAL", "TECH"],
        {"skills": ["علوم"], "knowledge": ["شیمی", "زیست‌شناسی", "قانون و دولت"],
         "work_context": ["قرار گرفتن در معرض آلاینده‌ها"]}),
    "دامپزشکان نظامی": (["MEDICAL", "ANALYTIC", "PRECISION", "FIELD"],
        {"skills": ["علوم"], "knowledge": ["شیمی", "تولید مواد غذایی"],
         "work_context": ["قرار گرفتن در معرض سوختگی‌های جزئی، بریدگی‌ها، گاز گرفتگی‌ها یا نیش‌ها"]}),
    "روان‌شناسان بالینی نظامی": (["MEDICAL", "ANALYTIC", "DESK"],
        {"knowledge": ["روان‌شناسی", "درمان و مشاوره", "جامعه‌شناسی و انسان‌شناسی",
                       "آموزش و پرورش"],
         "abilities": ["استدلال استقرایی", "حساسیت به مسئله"],
         "work_context": ["موقعیت‌های تعارض"]}),

    # --- support, command and administration ---------------------------------------
    "افسران لجستیک نظامی": (["ANALYTIC", "DESK", "LEAD"],
        {"skills": ["ریاضیات"], "knowledge": ["حمل و نقل", "اقتصاد و حسابداری"],
         "abilities": ["استدلال ریاضی", "توانایی عددی"]}),
    "متخصصان انبارداری نظامی": (["DESK", "PHYSICAL", "ANALYTIC"],
        {"skills": ["ریاضیات"], "knowledge": ["حمل و نقل", "اقتصاد و حسابداری"],
         "abilities": ["توانایی عددی", "سرعت ادراکی"],
         "work_context": ["اهمیت تکرار وظایف یکسان",
                          "محیط داخلی، بدون کنترل شرایط محیطی"]}),
    "متخصصان ترابری و جابجایی نیرو": (["ANALYTIC", "DESK", "VEHICLE", "LEAD"],
        {"knowledge": ["حمل و نقل", "جغرافیا"], "abilities": ["توانایی عددی"]}),
    "متخصصان مهمات و انبار تسلیحات": (["TECH", "PHYSICAL", "HAZARD", "DESK"],
        {"knowledge": ["شیمی"],
         "work_context": ["اهمیت تکرار وظایف یکسان"]}),
    "افسران مالی و بودجه نظامی": (["DESK", "ANALYTIC", "LEAD"],
        {"skills": ["ریاضیات"], "knowledge": ["اقتصاد و حسابداری", "ریاضیات",
                                              "قانون و دولت", "اداری"],
         "abilities": ["استدلال ریاضی", "توانایی عددی", "سرعت ادراکی"]}),
    "افسران منابع انسانی نظامی": (["DESK", "ANALYTIC", "LEAD", "PUBLIC"],
        {"knowledge": ["پرسنل و منابع انسانی", "قانون و دولت", "آموزش و پرورش"],
         "work_context": ["موقعیت‌های تعارض"]}),
    "افسران حقوقی نظامی": (["DESK", "ANALYTIC", "PUBLIC", "LEAD"],
        {"knowledge": ["قانون و دولت", "فلسفه و الهیات", "روان‌شناسی"],
         "abilities": ["حفظ کردن", "استدلال استقرایی"],
         "work_context": ["موقعیت‌های تعارض", "سخنرانی عمومی"]}),
    "روحانیون نظامی": (["PUBLIC", "DESK", "ANALYTIC"],
        {"knowledge": ["فلسفه و الهیات", "روان‌شناسی", "درمان و مشاوره",
                       "جامعه‌شناسی و انسان‌شناسی", "تاریخ و باستان‌شناسی"],
         "abilities": ["حساسیت به مسئله"],
         "work_context": ["سخنرانی عمومی", "موقعیت‌های تعارض"]}),
    "مربیان آموزش نظامی": (["PUBLIC", "FIELD", "PHYSICAL", "LEAD"],
        {"skills": ["راهبردهای یادگیری", "یادگیری فعال", "نظارت"],
         "knowledge": ["آموزش و پرورش", "روان‌شناسی"],
         "abilities": ["روانی ایده‌ها", "اصالت"],
         "work_context": ["سخنرانی عمومی"]}),
    "افسران روابط عمومی نظامی": (["PUBLIC", "DESK", "ANALYTIC", "LEAD"],
        {"knowledge": ["ارتباطات و رسانه", "طراحی", "فروش و بازاریابی"],
         "abilities": ["اصالت", "روانی ایده‌ها"],
         "work_context": ["سخنرانی عمومی"]}),
    "متخصصان امور غیرنظامی": (["PUBLIC", "DESK", "ANALYTIC", "FIELD"],
        {"knowledge": ["جامعه‌شناسی و انسان‌شناسی", "قانون و دولت", "زبان خارجی",
                       "جغرافیا", "روان‌شناسی"],
         "work_context": ["موقعیت‌های تعارض"]}),
    "افسران برنامه‌ریزی عملیات": (["DESK", "ANALYTIC", "LEAD", "COMMS"],
        {"knowledge": ["جغرافیا", "حمل و نقل"],
         "abilities": ["تجسم", "استدلال استقرایی", "انعطاف‌پذیری دسته‌بندی"]}),
    "بازرسان ایمنی و بازرسی نظامی": (["ANALYTIC", "DESK", "FIELD", "LEAD"],
        {"knowledge": ["قانون و دولت", "اداری"],
         "abilities": ["سرعت ادراکی", "بینایی نزدیک"],
         "work_context": ["اهمیت تکرار وظایف یکسان", "موقعیت‌های تعارض"]}),
    "نوازندگان گروه‌های موسیقی نظامی": (["PUBLIC", "PRECISION", "PHYSICAL"],
        {"skills": ["راهبردهای یادگیری", "نظارت"],
         "knowledge": ["هنرهای زیبا", "آموزش و پرورش"],
         "abilities": ["حساسیت شنوایی", "توجه شنیداری", "حفظ کردن", "مهارت انگشتان",
                       "سرعت مچ و انگشتان", "هماهنگی چند عضو"],
         "work_context": ["صرف زمان برای ایستادن", "اهمیت تکرار وظایف یکسان",
                          "فضای باز، در معرض تمام شرایط آب و هوایی"]}),
    # the dog handlers were placed with the ground occupations above
})


COLUMNS = ["skills", "knowledge", "abilities", "work_context"]
# free-text columns: written out by hand, checked only for coverage
HAND = {"responsibilities": HERE / "military_responsibilities_fa.json",
        "tools": HERE / "military_tools_extra_fa.json"}


def main() -> int:
    canonical = json.loads(CANONICAL.read_text(encoding="utf-8"))["canonical"]
    allowed = {c: set(canonical[c].values()) for c in COLUMNS}
    hand = {c: json.loads(path.read_text(encoding="utf-8"))["add"]
            for c, path in HAND.items()}

    # Every phrase used anywhere here must be one the O*NET rows are described with,
    # or it is an item no profile can match and no other record shares.
    bad = []
    for label, table in [("BASE", BASE)] + [(f"BUNDLES[{k}]", v) for k, v in BUNDLES.items()]:
        for column, values in table.items():
            for value in values:
                if value not in allowed[column]:
                    bad.append(f"{label} / {column}: {value!r}")
    for title, (bundles, extra) in ROWS.items():
        for name in bundles:
            if name not in BUNDLES:
                bad.append(f"{title}: no bundle named {name!r}")
        for column, values in extra.items():
            if column not in COLUMNS:
                bad.append(f"{title}: {column!r} is not one of the four taxonomies")
                continue
            for value in values:
                if value not in allowed[column]:
                    bad.append(f"{title} / {column}: {value!r}")
    # the hand-written columns: free text, but every occupation must have some
    for column, path in HAND.items():
        for title in ROWS:
            if title not in hand[column]:
                bad.append(f"{title}: no {column} in {path.name}")
        for title in hand[column]:
            if title not in ROWS:
                bad.append(f"{title}: in {path.name} but not an occupation here")
    if bad:
        print(f"{len(bad)} problems:")
        for line in bad[:30]:
            print(f"  {line}")
        return 1

    out = {}
    for title, (bundles, extra) in ROWS.items():
        row = {}
        for column in COLUMNS:
            seen, values = set(), []
            # most specific first — merge_occupations_fa.py truncates the tail
            for source in [extra] + [BUNDLES[b] for b in bundles] + [BASE]:
                for value in source.get(column, []):
                    if value not in seen:
                        seen.add(value)
                        values.append(value)
            row[column] = values
        for column in HAND:
            row[column] = list(hand[column][title])
        out[title] = row

    order = COLUMNS + list(HAND)
    counts = {c: sorted(len(r[c]) for r in out.values()) for c in order}
    for column in order:
        n = counts[column]
        print(f"  {column:14} {n[0]:3}–{n[-1]:3} items added per row "
              f"(median {n[len(n) // 2]})")
    path = HERE / "military_enrichment.json"
    path.write_text(json.dumps({"add": out}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"\nwrote {path} for {len(out)} occupations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
