"""Armenian texts of the staff dashboard (T-802: the user chose Armenian, D-049)."""

from reel.db.enums import JobStatus, Stage

STAGE_LABELS: dict[Stage, str] = {
    Stage.IMPORT: "Ներմուծում",
    Stage.TRANSCRIBE: "Տառադարձում",
    Stage.CLEAN: "Մաքրում",
    Stage.CHAPTERS: "Գլուխներ",
    Stage.CONSENT: "Համաձայնություն",
    Stage.UPLOAD: "Վերբեռնում",
    Stage.POST: "Հղումների տարածում",
}

STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.QUEUED: "Հերթում",
    JobStatus.RUNNING: "Ընթացքում",
    JobStatus.ON_HOLD: "Սպասում է որոշման",
    JobStatus.FAILED: "Ձախողված",
    JobStatus.DONE: "Պատրաստ",
    JobStatus.CANCELLED: "Չեղարկված",
}

LOGIN_FAILED = "Սխալ օգտանուն կամ գաղտնաբառ։"
LOGIN_THROTTLED = "Չափազանց շատ անհաջող փորձեր։ Փորձեք 15 րոպեից։"
CSRF_FAILED = "Ձևի ժամկետն անցել է։ Թարմացրեք էջը և փորձեք նորից։"
NOT_FOUND = "Էջը չի գտնվել։"

# Job flags (FR-12 and the stages). An unknown code is shown as it is.
FLAG_LABELS: dict[str, str] = {
    "unknown_group": "Խումբն անհայտ է",
    "missing_lesson_number": "Դասի համարը չկա",
    "ambiguous_lesson_number": "Դասի համարը միանշանակ չէ",
    "missing_date": "Ամսաթիվը չկա",
    "empty_transcript": "Տառադարձումը դատարկ է",
    "lesson_start_not_found": "Դասի սկիզբը չի գտնվել",
    "lesson_end_not_found": "Դասի ավարտը չի գտնվել",
    "lesson_boundaries_conflict": "Դասի սահմանները հակասում են",
    "chapters_not_generated": "Գլուխները չեն ստեղծվել",
    "speech_unclear": "Խոսքը պարզ չէ",
    "chapters_by_staff": "Գլուխները նշել է աշխատակիցը",
    "thumbnail_not_made": "Նկարը (thumbnail) չի ստեղծվել",
    "description_chapters_dropped": "Նկարագրության գլուխները կրճատվել են",
    "description_footer_truncated": "Նկարագրության վերջը կրճատվել է",
    "description_summary_truncated": "Ամփոփումը կրճատվել է",
    "youtube_not_unlisted": "YouTube-ը տեսանյութը փակել է (private)",
    "youtube_no_playlist": "YouTube playlist չկա",
    "youtube_thumbnail_not_set": "YouTube-ը նկարը չի ընդունել",
}

PILL_TITLES: dict[str, str] = {
    "done": "Արված է",
    "running": "Ընթանում է",
    "queued": "Հերթում է",
    "retry": "Կրկին փորձ է սպասվում",
    "held": "Սպասում է որոշման",
    "failed": "Ձախողվել է",
    "cancelled": "Չեղարկված է",
    "pending": "Դեռ չի սկսվել",
}

RUN_LABELS: dict[str, str] = {
    "running": "Ընթանում է",
    "succeeded": "Հաջողվեց",
    "failed": "Ձախողվեց",
    "held": "Կանգնեց որոշման համար",
    "cancelled": "Չեղարկվեց",
    "interrupted": "Ընդհատվեց",
}

EVENT_LABELS: dict[str, str] = {
    "created": "Ստեղծվեց",
    "started": "Փուլը սկսվեց",
    "stage_completed": "Փուլն ավարտվեց",
    "done": "Ամբողջն ավարտվեց",
    "retry_scheduled": "Նոր փորձ է նշանակվել",
    "failed": "Ձախողվեց",
    "held": "Կանգնեց որոշման համար",
    "released": "Ազատվեց",
    "cancelled": "Չեղարկվեց",
    "rerun": "Վերագործարկվեց",
    "retry_now": "Անմիջապես նոր փորձ",
    "lease_expired": "Worker-ը չպատասխանեց",
    "interrupted": "Ընդհատվեց",
    "identified": "Խումբը/դասը նշվեց",
}

ACTOR_LABELS: dict[str, str] = {"system": "համակարգ", "worker": "worker", "staff": "աշխատակից"}

TARGET_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "slack": "Slack",
    "classroom": "Classroom",
    "library": "Գրադարան (Library)",
}

PUBLICATION_LABELS: dict[str, str] = {
    "pending": "Սպասում է",
    "in_progress": "Ընթացքում",
    "published": "Հրապարակված",
    "failed": "Ձախողված",
    "skipped": "Բաց թողնված",
}

CONSENT_LABELS: dict[str, str] = {
    "passed": "Բոլորը համաձայն են",
    "opted_out": "Կա չհամաձայնվող ուսանող",
    "unknown": "Որոշ ուսանողների համաձայնությունն անհայտ է",
    "unreachable": "Համաձայնության աղբյուրն անհասանելի էր",
}

HOLD_LABELS: dict[str, str] = {
    "identification": "Պետք է նշել խումբը և դասի համարը",
    "consent_opted_out": "Ուսանողը համաձայն չէ հրապարակմանը",
    "consent_unknown": "Համաձայնությունն անհայտ է",
    "consent_unreachable": "Համաձայնության աղբյուրն անհասանելի է",
    "speech_unclear": "Խոսքը պարզ չէ, բաժիններ չեն գտնվել",
}


def cut_reason(reason: str) -> str:
    """The cleaning stage's reasons are English sentences; the known ones in Armenian."""
    if reason == "before the lesson":
        return "Դասից առաջ"
    if reason == "after the lesson":
        return "Դասից հետո"
    if reason == "silence over a static picture":
        return "Լռություն, անշարժ պատկեր"
    if reason == "silence":
        return "Լռություն"
    if reason.startswith("nobody at the board"):
        return "Գրատախտակի մոտ ոչ ոք չկա"
    return reason


DONE_MESSAGES: dict[str, str] = {
    "released": "Job-ը ազատվեց. փուլը կաշխատի նորից։",
    "accepted": "Ընդունվեց. job-ը շարունակվում է հաջորդ փուլով։",
    "cancelled": "Job-ը չեղարկվեց։",
    "identified": "Խումբը և դասը պահպանվեցին։",
    "rerun": "Job-ը կաշխատի նորից ընտրված փուլից։",
    "retried": "Նոր փորձը կսկսվի հենց հիմա։",
    "published": "Տեսանյութը կտեղադրվի առանց բաժինների (համաձայնության ստուգումից հետո)։",
    "chapters": (
        "Բաժինները պահպանվեցին. տեսանյութը կտեղադրվի դրանցով (համաձայնության ստուգումից հետո)։"
    ),
    "finished": "Մշակումը կավարտվի առանց բաժինների. տեսանյութը կներբեռնեք այս էջից։",
    "chapters_finished": "Բաժինները պահպանվեցին. մշակումը կավարտվի դրանցով։",
}

ACTION_FAILED = "Գործողությունը հնարավոր չէ այս վիճակում"

# --- accounts (T-1013) -------------------------------------------------------------------

ADMIN_ONLY = "Այս էջը միայն ադմինի համար է"
REGISTER_THROTTLED = "Այս համակարգչից շատ հաշիվներ են ստեղծվել։ Փորձեք մեկ ժամից։"
EMAIL_TAKEN = "Այս email-ով հաշիվ արդեն կա։ Մուտք գործեք կամ դիմեք ադմինին։"
CANNOT_CLOSE_SELF = "Չեք կարող փակել ձեր սեփական հաշիվը։"
ADMIN_PUBLISHES = "Ադմինի տեսանյութերը միշտ տեղադրվում են YouTube-ում։"
ROLE_LABELS: dict[str, str] = {"admin": "Ադմին", "user": "Օգտատեր"}
ACCOUNT_MESSAGES: dict[str, str] = {
    "closed": "Հաշիվը փակվեց. օգտատերն այլևս չի կարող մուտք գործել։",
    "opened": "Հաշիվը բացվեց. օգտատերը կարող է մուտք գործել։",
    "publish_on": "Այս օգտատիրոջ նոր տեսանյութերը կտեղադրվեն YouTube-ում։",
    "publish_off": "Այս օգտատիրոջ նոր տեսանյութերը միայն կմշակվեն։",
}


# --- the signed-in account's page (T-1016) ------------------------------------------------

PROFILE_MESSAGES: dict[str, str] = {
    "details": "Տվյալները պահպանվեցին։",
    "password": "Գաղտնաբառը փոխվեց։ Մյուս սարքերում մուտքը փակվեց։",
}
PROFILE_LABELS: dict[str, str] = {
    "title": "Իմ էջը",
    "details": "Իմ տվյալները",
    "username": "Մուտքի անուն",
    "first_name": "Անուն",
    "last_name": "Ազգանուն",
    "email": "Email",
    "phone": "Հեռախոսահամար",
    "optional": "(ոչ պարտադիր)",
    "created": "Գրանցվել է",
    "last_login": "Վերջին մուտք",
    "publishes": "Տեղադրում է YouTube-ում",
    "processes": "Միայն մշակում",
    "password": "Գաղտնաբառ",
    "current_password": "Ընթացիկ գաղտնաբառ",
    "new_password": "Նոր գաղտնաբառ (առնվազն 10 նիշ)",
    "password_again": "Նոր գաղտնաբառը նորից",
    "save_details": "Պահպանել տվյալները",
    "save_password": "Փոխել գաղտնաբառը",
}
PROFILE_CURRENT_PASSWORD_WRONG = "Ընթացիկ գաղտնաբառը սխալ է։"
PROFILE_PASSWORD_MISMATCH = "Գաղտնաբառերը չեն համընկնում։"
PROFILE_PASSWORD_RULES = "Գաղտնաբառը պետք է ունենա առնվազն {minimum} նիշ։"
PROFILE_PASSWORD_UNCHANGED = "Նոր գաղտնաբառը պետք է տարբերվի ընթացիկից։"
PROFILE_FORM_INVALID = "Ձևի տվյալները սխալ են կամ չափազանց երկար։"
