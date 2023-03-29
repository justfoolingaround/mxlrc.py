import base64
import enum
import hmac
import pathlib
import time
from datetime import datetime, timedelta
from urllib.parse import unquote, urlencode

import click
import orjson
import regex

from .constants import API_URL, MUSIC_SYMBOL
from .exceptions import UnexpectedResponse, stderr_print

SPOTIFY_TRACK_REGEX = regex.compile(
    r"(?:\bspotify:track:|open\.spotify\.com/track/)(?P<id>[a-zA-Z0-9]+)"
)
MUSIXMATCH_TRACK_REGEX = regex.compile(r"\b\d{8,10}\b")


def get_api_signature(api_endpoint, timestamp, signature_protocol="sha1"):

    signature = base64.urlsafe_b64encode(
        hmac.digest(
            b"IEJ5E8XFaHQvIQNfs7IC",
            (api_endpoint + format(timestamp, "%Y%m%d")).encode(),
            signature_protocol,
        )
    ).decode()

    return signature


class TokenManager:
    def __init__(self, session, *, token_path=pathlib.Path("./.token")):

        self.token_path = token_path
        self.payload = {}

        self.session = session

    @property
    def token(self):

        if self.is_valid():
            return self.payload["token"]

        self.load()
        return self.token

    def is_valid(self, payload=None):
        if payload is None:
            return self.is_valid(self.payload)

        return time.time() < payload.get("expires_at", 0)

    def load(self, *, signature_protocol="sha1"):

        is_valid = self.is_valid()

        if self.token_path is not None:
            if self.token_path.exists() and not is_valid:
                with self.token_path.open("rb") as token_file:
                    self.payload = orjson.loads(token_file.read())

        if self.is_valid():
            return self.save()

        datetime_now = datetime.now()

        params = {
            "format": "json",
            "app_id": "web-desktop-app-v1.0",
        }

        url = API_URL + "token.get?" + urlencode(params)

        signature = get_api_signature(url, datetime_now, signature_protocol)

        response = self.session.get(
            url,
            params={"signature": signature, "signature_protocol": signature_protocol},
        ).json()

        msg = response["message"]
        UnexpectedResponse.raise_if_faulty(msg)

        return self.save(
            {
                "token": msg["body"]["user_token"],
                "expires_at": (datetime_now + timedelta(days=30)).timestamp(),
            }
        )

    def save(self, payload=None):

        if payload is not None:
            self.payload = payload

        if self.token_path is not None:
            with self.token_path.open("wb") as token_file:
                token_file.write(orjson.dumps(self.payload))


def parse_duration(duration: float):
    minutes, seconds = divmod(duration, 60)

    if minutes > 59:
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def iter_synced_lyrics(message_body):

    center_count = 1

    for subtitle in orjson.loads(
        message_body.get("subtitle_list", ({},))[0]
        .get("subtitle", {})
        .get("subtitle_body", "[]")
    ):
        subtitle_time = subtitle["time"]
        subtitle_text = subtitle["text"]

        if subtitle_text is not None and subtitle_text:
            center_count = len(subtitle_text)

        yield {
            "time": f"{subtitle_time['minutes']:02d}:{subtitle_time['seconds']:02d}.{subtitle_time['hundredths']:02d}",
            "text": subtitle_text or MUSIC_SYMBOL.center(center_count, " "),
        }


def iter_unsynced_lyrics(message_body):

    for lyrics in (
        message_body.get("body", {})
        .get("lyrics", {})
        .get("lyrics_body", "")
        .split("\n")
    ):

        yield {
            "time": "00:00.00",
            "text": lyrics,
        }


def iter_instrumental(_):
    yield {
        "time": "00:00.00",
        "text": f"{MUSIC_SYMBOL} Instrumental {MUSIC_SYMBOL}",
    }


def iter_parsed_to_lrc(parsed_lyrics, track_meta):
    if (artist_name := track_meta.get("artist_name")) is not None:
        yield f"[ar:{artist_name}]"

    if (track_name := track_meta.get("track_name")) is not None:
        yield f"[ti:{track_name}]"

    if (album_name := track_meta.get("album_name")) is not None:
        yield f"[al:{album_name}]"

    if (duration := track_meta.get("track_length")) is not None:
        yield f"[length:{parse_duration(duration)}]"

    for lyric in parsed_lyrics:
        yield f"[{lyric['time']}]{lyric['text']}"


class UserInputEnum(enum.Enum):

    ISRC = "isrc"
    SPOTIFY_TRACK_ID = "spotify_track_id"
    MUSIXMATCH_TRACK_ID = "musixmatch_track_id"

    UNKNOWN = "unknown"


def parse_track(track: str):

    if track == "-":
        return parse_track(click.get_text_stream("stdin").read())

    isrc_raw = "".join(_ for _ in track if _.isalnum())

    if len(isrc_raw) == 12:
        return UserInputEnum.ISRC, isrc_raw

    spotify = SPOTIFY_TRACK_REGEX.search(track)

    if spotify:
        return UserInputEnum.SPOTIFY_TRACK_ID, spotify.group("id")

    musixmatch = MUSIXMATCH_TRACK_REGEX.search(track)

    if musixmatch:
        return UserInputEnum.MUSIXMATCH_TRACK_ID, musixmatch.group(0)

    return UserInputEnum.UNKNOWN, track
