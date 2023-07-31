import click
import requests

from mxlrclib import MusixMatch, TokenManager, UserInputEnum, parse_track
from mxlrclib.exceptions import TrackNotFoundError
from mxlrclib.utils import (
    iter_instrumental,
    iter_parsed_to_lrc,
    iter_synced_lyrics,
    iter_unsynced_lyrics,
    stderr_print,
)

HTTP_USER_AGENT = "mxlrc.py (justfoolingarounddev)"


@click.command()
@click.argument("track")
@click.option("--token", required=False, type=click.STRING, help="MusixMatch API token")
@click.option("-l", "--lrc", is_flag=True, help="Output LRC instead of plain text")
@click.pass_context
def musixmatch_lyrics(ctx: click.Context, track: str, token: str, lrc: bool):
    session = requests.Session()
    session.headers.update({"User-Agent": HTTP_USER_AGENT})

    type_of, args = parse_track(track)

    if token is None:
        token = TokenManager(session).token

    lyrics_api_client = MusixMatch(token, session=session)

    match type_of:
        case UserInputEnum.ISRC:
            response = lyrics_api_client.get_track_from_isrc(args)

        case UserInputEnum.MUSIXMATCH_TRACK_ID:
            response = lyrics_api_client.get_track_from_id(args)

        case UserInputEnum.SPOTIFY_TRACK_ID:
            response = lyrics_api_client.get_track_from_spotify_id(args)

        case UserInputEnum.UNKNOWN:
            stderr_print(
                "mxlrc.py is operating under search results, please duely note that MusixMatch's are extremely inaccurate. "
                "Using the project with an ISRC, Spotify track URL (or URI) or MusixMatch track ID is recommended."
            )
            search_results = lyrics_api_client.search_track(track)

            if not search_results:
                raise TrackNotFoundError(f"No lyrics found for {track!r}.")

            response = lyrics_api_client.get_track_from_id(
                search_results[0]["track"]["track_id"]
            )

    message = response["macro_calls"]["track.subtitles.get"]["message"].get("body", {})
    track_meta = (
        response["macro_calls"]["matcher.track.get"]["message"].get("body", {}) or {}
    ).get("track", {})

    if track_meta.get("has_subtitles", 0):
        genexp = iter_synced_lyrics(message)

    else:
        if track_meta.get("has_lyrics", 0):
            genexp = iter_unsynced_lyrics(message)
        else:
            genexp = iter_instrumental(message)

    if lrc:
        lines_genexp = iter_parsed_to_lrc(genexp, track_meta)
    else:
        lines_genexp = map(lambda track: track.get("text"), genexp)

    for line in lines_genexp:
        print(line)


if __name__ == "__main__":
    musixmatch_lyrics()
