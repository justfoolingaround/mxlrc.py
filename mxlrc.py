import click

try:
    import orjson
except ImportError:
    import json as orjson

import regex
import requests

DEFAULT_TOKEN = "2203269256ff7abcb649269df00e14c833dbf4ddfb5b36a1aae8b0"

ARTIST_TRACK_REGEX = regex.compile(
    r"^(?P<artist>(?:\\(?&separator)|.)+?)\s*(?P<separator>[,—-])+\s*(?P<track_name>(?:\\(?&separator)|.)+?)$"
)


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

    lyrics_endpoint = "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get"

    def __init__(self, token=DEFAULT_TOKEN, *, session: requests.Session = None):

        self.token = token
        self.session = session or requests.Session()

    def get_song(self, artist, track_name):

        response = self.session.get(
            self.lyrics_endpoint,
            params={
                "usertoken": self.token,
                "q_artist": artist,
                "q_track": track_name,
                "format": "json",
                "namespace": "lyrics_richsynched",
                "subtitle_format": "mxm",
                "app_id": "web-desktop-app-v1.0",
            },
        )
        response.raise_for_status()

        message = response.json()["message"]
        UnexpectedResponse.raise_if_faulty(message)

        return message

    def get_track_lyrics_body(self, artist, title):

        message = self.get_song(artist, title)

        message_body = message["body"]["macro_calls"]
        UnexpectedResponse.raise_if_faulty(message_body["matcher.track.get"]["message"])

        lyrics_body = message_body["track.lyrics.get"]["message"]
        UnexpectedResponse.raise_if_faulty(lyrics_body)

        if lyrics_body is None:
            raise TrackNotFoundError(f"No lyrics found for {f'{title} by {artist}'!r}")

        if lyrics_body.get("body", {}).get("lyrics", {}).get("restricted", False):
            raise TrackRestrictedError(
                f"Lyrics for {f'{title} by {artist}'!r} are restricted."
            )

        return message_body

    def iter_lines(self, artist, title, lrc_format=False):

        message_body = self.get_track_lyrics_body(artist, title)

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


def parse_track(ctx: click.Context, argument: click.Argument, track):

    if track == "-":
        return parse_track(ctx, argument, click.get_text_stream("stdin").read())

    match = ARTIST_TRACK_REGEX.match(track)
    if match is None:
        raise click.ClickException(f"Invalid track name: {track!r}")

    return match.group("artist", "track_name")


@click.command()
@click.argument("track", callback=parse_track)
@click.option("--token", default=DEFAULT_TOKEN, help="MusixMatch API token")
@click.option("-l", "--lrc", is_flag=True, help="Output LRC instead of plain text")
def musixmatch_lyrics(track, token, lrc):

    client = MusixMatch(token)

    for line in client.iter_lines(*track, lrc_format=lrc):
        print(line)


if __name__ == "__main__":
    musixmatch_lyrics()
