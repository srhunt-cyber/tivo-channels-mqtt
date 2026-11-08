"""Normalize Channels responses without guessing television power."""
from datetime import datetime, timezone


def text(value):
    return value.strip() if isinstance(value,str) else ''


def normalize(raw):
    playback = raw.get('status','unknown')
    if playback not in ('playing','paused','stopped'):
        playback = 'unknown'
    active = playback in ('playing','paused')
    channel = raw.get('channel') if active and isinstance(raw.get('channel'),dict) else {}
    content = raw.get('now_playing') if active and isinstance(raw.get('now_playing'),dict) else {}
    number = str(channel['number']) if channel.get('number') is not None else ''
    name, title, episode = text(channel.get('name')), text(content.get('title')), text(content.get('episode_title'))
    station = ' '.join(x for x in (name,number) if x)
    marker = {'playing':'▶','paused':'⏸','stopped':'■'}.get(playback,'?')
    detail = ' · '.join(x for x in (station,title) if x)
    display = marker + ' ' + (detail or playback.capitalize())
    return {
        'playback':playback, 'channel_number':number, 'channel_name':name,
        'channel':station, 'program_title':title, 'episode_title':episode,
        'content_type':text(content.get('type')), 'display':display[:255],
        'playback_time':raw.get('playback_time') if isinstance(raw.get('playback_time'),(int,float)) else None,
        'muted':raw.get('muted') if isinstance(raw.get('muted'),bool) else None,
        'tv_power':'unknown', 'updated_at':datetime.now(timezone.utc).isoformat(),
    }
