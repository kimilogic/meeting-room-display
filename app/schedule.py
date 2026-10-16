"""Timezone-aware calendar normalization and server-side display decisions."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
UTC = timezone.utc
ROOM_NAME = "ミーティングルーム（表示PoC）"
DUMMY_SCHEDULE = (
    (9, 0, 10, 0, "朝会"), (11, 0, 12, 0, "ミーティング"),
    (14, 0, 15, 30, "定例会議"), (16, 0, 17, 0, "打ち合わせ"),
)


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: datetime
    end: datetime
    blocks: bool = True
    all_day: bool = False
    organizer: str = "－"


def window_for(now):
    start = now.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=2)


def parse_time(value):
    parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        zones = {"UTC": UTC, "Etc/UTC": UTC, "GMT": UTC, "Tokyo Standard Time": JST, "Asia/Tokyo": JST}
        parsed = parsed.replace(tzinfo=zones[value["timeZone"]])
    return parsed.astimezone(UTC)


def organizer_for_display(raw, *, hide_private=False):
    """Separate organizer disclosure policy from subject masking."""
    if hide_private and raw.get("sensitivity") in ("private", "confidential"):
        return "－"
    organizer = raw.get("organizer")
    email = organizer.get("emailAddress") if isinstance(organizer, dict) else None
    if isinstance(email, dict):
        for field in ("name", "address"):
            value = email.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "－"


def normalize_event(raw):
    # Reject incomplete/malformed data instead of treating it as an empty calendar.
    if not isinstance(raw, dict) or type(raw.get("isCancelled")) is not bool:
        raise ValueError("invalid event")
    if raw["isCancelled"]:
        return None
    if (not isinstance(raw.get("id"), str) or not raw["id"] or
            type(raw.get("isAllDay")) is not bool or
            raw.get("showAs") not in ("free", "tentative", "busy", "oof", "workingElsewhere", "unknown") or
            raw.get("sensitivity") not in ("normal", "personal", "private", "confidential")):
        raise ValueError("invalid event")
    start, end = parse_time(raw["start"]), parse_time(raw["end"])
    if end <= start:
        raise ValueError("invalid event interval")
    subject = raw.get("subject")
    if subject is not None and not isinstance(subject, str):
        raise ValueError("invalid subject")
    title = "非公開の予定" if raw["sensitivity"] in ("private", "confidential") else (subject or "（件名なし）")
    return Event(raw["id"], title, start, end, raw["showAs"] != "free", raw["isAllDay"],
                 organizer=organizer_for_display(raw))


def dummy_events(now):
    today, _ = window_for(now)
    return tuple(Event(str(i), title,
                       today.replace(hour=sh, minute=sm).astimezone(UTC),
                       today.replace(hour=eh, minute=em).astimezone(UTC),
                       organizer="テスト予約者" + str(i + 1))
                 for i, (sh, sm, eh, em, title) in enumerate(DUMMY_SCHEDULE))


def present(event, now):
    start, end = event.start.astimezone(JST), event.end.astimezone(JST)
    def label(time):
        return time.strftime("%H:%M" if time.date() == now.date() else "%m/%d %H:%M")
    time_text = label(start) + " – " + label(end)
    if event.all_day:
        time_text = "終日（" + time_text + "）"
    return {
        "title": event.title + ("（空き時間扱い）" if not event.blocks else ""),
        "organizer": event.organizer,
        "start_text": label(start), "end_text": label(end), "time_text": time_text,
        "state": "current" if event.blocks and event.start <= now < event.end else (
            "past" if event.end <= now else "upcoming"),
    }


def combined(events, now):
    if not events:
        return None
    if len(events) == 1:
        return present(events[0], now)
    return {
        "title": "予約重複（" + str(len(events)) + "件）：" + " / ".join(e.title for e in events),
        "time_text": " / ".join(present(e, now)["time_text"] for e in events),
        "organizer": " / ".join(e.organizer for e in events),
        "start_text": "", "end_text": "", "state": "current" if events[0].start <= now else "upcoming",
    }


def build_display(now, events=None, *, room_name=ROOM_NAME, is_demo=True, fetched_at=None,
                  warning="", unknown_text=None):
    now = now.astimezone(JST)
    ordered = sorted(dummy_events(now) if events is None else events, key=lambda e: (e.start, e.end, e.id))
    current_events = [e for e in ordered if e.blocks and e.start <= now < e.end]
    future = [e for e in ordered if e.blocks and e.start > now]
    next_events = [e for e in future if e.start == future[0].start] if future else []
    current, upcoming = combined(current_events, now), combined(next_events, now)
    today, _ = window_for(now)
    tomorrow = today + timedelta(days=1)
    empty_next = "本日の残りの予定はありません" if is_demo else "取得範囲内に次の予定はありません"
    if len(current_events) > 1:
        detail = "予約重複（" + str(len(current_events)) + "件）・各予定を確認"
    elif current:
        detail = current["end_text"] + "まで"
    elif future:
        detail = "次の予定は " + present(future[0], now)["start_text"] + "から"
    else:
        detail = empty_next
    success = fetched_at if not is_demo else now
    return {
        "room_name": room_name,
        "date_text": now.strftime("%Y年%m月%d日") + "（" + "月火水木金土日"[now.weekday()] + "）",
        "status": "unknown" if unknown_text else "busy" if current else "free",
        "status_text": unknown_text or ("使用中" if current else "空室"),
        "status_detail": "最新の利用状況を確認できません" if unknown_text else detail,
        "current": current, "next": upcoming,
        "events": [present(e, now) for e in ordered if e.start < tomorrow and e.end > today],
        "next_empty_text": "予定を確認できません" if unknown_text else empty_next,
        "events_empty_text": "予定を確認できません" if unknown_text else "本日の予定はありません",
        "updated_text": success.astimezone(JST).strftime("%m/%d %H:%M:%S") if success else "—",
        "server_epoch_ms": int(now.timestamp() * 1000), "utc_offset_minutes": 540,
        "is_demo": is_demo, "warning": bool(warning) or bool(unknown_text),
        "connection_text": warning or ("接続中 · この画面は実際の予約ではなくダミーデータです" if is_demo else "接続中 · Microsoft 365の会議室予定"),
    }
