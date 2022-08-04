import base64
import hmac
import pathlib
import time
from datetime import datetime, timedelta
from urllib.parse import unquote, urlencode

import click
import regex
import requests

try:
    import orjson
except ImportError:
    import json as orjson


API_URL = "https://apic-desktop.musixmatch.com/ws/1.1/"


ARTIST_TRACK_REGEX = regex.compile(
    r"^(?P<artist>(?:\\(?&separator)|.)+?)\s*(?P<separator>[,—-])+\s*(?P<track_name>(?:\\(?&separator)|.)+?)$"
)
SPOTIFY_EMBED_TRACK = regex.compile(r'<script id="resource".+?>\s+(.+?)\s+</script>')

HTTP_USER_AGENT = "Musixmatch/0.19.4"

TOKEN_PATH = pathlib.Path(__file__).parent / ".token"


class UnexpectedResponse(RuntimeError):
    def __init__(self, message_header):

        self.status_code = message_header["status_code"]
        self.execute_time = message_header["execute_time"]
        self.hint = message_header.get("hint")

        super().__init__(
            f"Invalid response {self.status_code}{f' [hint: {self.hint}]' if self.hint else ''}, server execute time: {self.execute_time}"
        )

    @staticmethod
    def raise_if_faulty(message):
        if message["header"]["status_code"] > 399:
            raise UnexpectedResponse(message["header"])


class TrackError(Exception):
    pass


class TrackRestrictedError(TrackError):
    pass


class TrackNotFoundError(TrackError):
    pass


class LyricsNotFoundError(TrackError):
    pass


MUSIC_SYMBOL = "♪"


def get_api_signature(api_endpoint, timestamp):

    signature_protocol = "sha1"

    signature = base64.urlsafe_b64encode(
        hmac.digest(
            b"IEJ5E8XFaHQvIQNfs7IC",
            (api_endpoint + format(timestamp, "%Y%m%d")).encode(),
            signature_protocol,
        )
    ).decode()

    return signature, signature_protocol


def get_spotify_track_information(session, spotify_id):

    response = session.get(
        f"https://open.spotify.com/embed-legacy/track/{spotify_id}",
    )
    response.raise_for_status()

    api_response = orjson.loads(
        unquote(SPOTIFY_EMBED_TRACK.search(response.text).group(1))
    )

    return api_response["name"], ", ".join(_["name"] for _ in api_response["artists"])


def generate_token(session):

    if TOKEN_PATH.exists():
        with TOKEN_PATH.open("rb") as token_file:
            token = orjson.loads(token_file.read())

        if time.time() < token.get("expires_at", 0):
            return token["token"]

    datetime_now = datetime.now()
    current_timestamp = format(datetime_now, "%Y-%m-%dT%H:%M:%SZ")

    params = {
        "format": "json",
        "timestamp": current_timestamp,
        "app_id": "web-desktop-app-v1.0",
    }

    url = API_URL + "token.get?" + urlencode(params)

    signature, signature_protocol = get_api_signature(url, datetime_now)

    response = session.get(
        url, params={"signature": signature, "signature_protocol": signature_protocol}
    ).json()["message"]

    UnexpectedResponse.raise_if_faulty(response)

    with TOKEN_PATH.open("wb") as token_file:
        token_file.write(
            orjson.dumps(
                {
                    "token": response["body"]["user_token"],
                    "expires_at": (datetime_now + timedelta(days=30)).timestamp(),
                },
            )
        )

    return response["body"]["user_token"]


def parse_duration(duration: float):
    minutes, seconds = divmod(duration, 60)
    return f"{minutes:02d}:{seconds:02d}"


def iter_synced_lyrics(message_body):

    center_count = 1

    for subtitle in orjson.loads(
        message_body.get("track.subtitles.get", {})
        .get("message", {})
        .get("body", {})
        .get("subtitle_list", ({},))[0]
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
        message_body.get("track.lyrics.get", {})
        .get("message", {})
        .get("body", {})
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


class MusixMatch:

    lyrics_endpoint = API_URL + "macro.subtitles.get"

    def __init__(self, token, *, session: requests.Session = None):

        self.token = token
        self.session = session or requests.Session()

    def get_api(self, endpoint, params):
        params.update(
            {
                "usertoken": self.token,
                "format": "json",
                "subtitle_format": "mxm",
                "namespace": "lyrics_richsynched",
                "app_id": "web-desktop-app-v1.0",
            }
        )
        response = self.session.get(API_URL + endpoint, params=params).json()["message"]
        UnexpectedResponse.raise_if_faulty(response)
        return response["body"]

    def get_track_from_isrc(self, isrc):
        return self.get_api(
            "track.subtitles.get",
            {
                "track_isrc": isrc,
            },
        )

    def get_track_from_meta(self, artist, title):
        return self.get_api(
            "macro.subtitles.get",
            {
                "q_artist": artist,
                "q_track": title,
            },
        )

    def get_track_from_id(self, track_id):
        return self.get_api(
            "track.subtitles.get",
            {
                "track_id": track_id,
            },
        )

    def get_track_from_spotify_id(self, spotify_id):
        return self.get_api(
            "track.subtitles.get",
            {
                "track_spotify_id": spotify_id,
            },
        )

    def iter_lines(
        self,
        *args,
        lrc_format=False,
        is_isrc=False,
        is_track_id=False,
        is_spotify=False,
        **kwargs,
    ):

        if is_isrc:
            message = self.get_track_from_isrc(*args, **kwargs)
        else:
            if is_track_id:
                message = self.get_track_from_id(*args, **kwargs)
            else:
                if is_spotify:
                    message = self.get_track_from_spotify_id(*args, **kwargs)
                else:
                    message = self.get_track_from_meta(*args, **kwargs)

        track_meta = {}

        if "macro_calls" not in message:
            genexp = iter_synced_lyrics(
                {"track.subtitles.get": {"message": {"body": message}}}
            )

        else:

            message_body = message["macro_calls"]
            UnexpectedResponse.raise_if_faulty(
                message_body["matcher.track.get"]["message"]
            )

            track_body = message_body["matcher.track.get"]["message"]["body"]["track"]
            title, artist = track_body["track_name"], track_body["artist_name"]

            lyrics_body = message_body["track.lyrics.get"]["message"]
            UnexpectedResponse.raise_if_faulty(lyrics_body)

            if lyrics_body is None:
                raise TrackNotFoundError(
                    f"No lyrics found for {f'{title} by {artist}'!r}"
                )

            if lyrics_body.get("body", {}).get("lyrics", {}).get("restricted", False):
                raise TrackRestrictedError(
                    f"Lyrics for {f'{title} by {artist}'!r} are restricted."
                )

            track_meta = message_body["matcher.track.get"]["message"]["body"]["track"]

            if track_meta.get("has_subtitles", 0):
                genexp = iter_synced_lyrics(message_body)

            else:
                if track_meta.get("has_lyrics", 0):
                    genexp = iter_unsynced_lyrics(message_body)
                else:
                    if track_meta.get("has_instrumental", 0):
                        genexp = iter_instrumental(message_body)
                    else:
                        raise TrackNotFoundError(
                            f"No lyrics found for {f'{title} by {artist}'!r}"
                        )

        if lrc_format:
            yield from iter_parsed_to_lrc(genexp, track_meta)
        else:
            yield from map(lambda track: track.get("text"), genexp)


def parse_track(track):

    if track == "-":
        return parse_track(click.get_text_stream("stdin").read())

    match = ARTIST_TRACK_REGEX.match(track)
    if match is None:
        raise click.ClickException(f"Invalid track name: {track!r}")

    return match.group("artist", "track_name")


@click.command()
@click.argument("track")
@click.option("--token", required=False, type=click.STRING, help="MusixMatch API token")
@click.option("-l", "--lrc", is_flag=True, help="Output LRC instead of plain text")
@click.option(
    "-i", "--isrc", is_flag=True, help="Use ISRC instead of artist and track name"
)
@click.option(
    "-t",
    "--track-id",
    is_flag=True,
    help="Use track ID instead of artist and track name",
)
@click.option(
    "-s",
    "--spotify-id",
    is_flag=True,
    help="Use Spotify ID instead of artist and track name",
)
def musixmatch_lyrics(track, token, lrc, isrc, track_id, spotify_id):

    session = requests.Session()
    session.headers.update({"User-Agent": HTTP_USER_AGENT})

    kwargs = {}

    if not any((isrc, track_id, spotify_id)):
        artist, title = parse_track(track)

        kwargs.update(
            artist=artist,
            title=title,
        )

    if (isrc & track_id) | (isrc & spotify_id) | (track_id & spotify_id):
        raise click.ClickException(
            "Cannot use any two of --isrc, --track-id, --spotify-id"
        )

    if isrc:
        kwargs.update(isrc=track)

    if track_id:
        kwargs.update(track_id=track)

    if spotify_id:
        kwargs.update(spotify_id=track)

    if token is None:
        token = generate_token(session)

    client = MusixMatch(token, session=session)

    for line in client.iter_lines(
        **kwargs,
        lrc_format=lrc,
        is_isrc=isrc,
        is_track_id=track_id,
        is_spotify=spotify_id,
    ):
        print(line)


if __name__ == "__main__":
    musixmatch_lyrics()
