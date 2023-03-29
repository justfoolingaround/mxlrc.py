import requests

from .constants import API_URL
from .exceptions import UnexpectedResponse


class MusixMatch:

    lyrics_endpoint = API_URL + "macro.subtitles.get"

    def __init__(self, token, *, session: requests.Session = None):

        self.token = token
        self.session = session or requests.Session()

    def get_api(self, endpoint, params, *, raise_on_faulty=True):
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
        if raise_on_faulty:
            UnexpectedResponse.raise_if_faulty(response)

        return response["body"]

    def search_track(self, query):

        return self.get_api(
            "track.search",
            {
                "q": query,
            },
        ).get("track_list", [])

    def get_track_from_isrc(self, isrc):
        return self.get_api(
            "track.subtitles.get",
            {
                "track_isrc": isrc,
            },
            raise_on_faulty=False,
        )

    def get_track_from_id(self, track_id):
        return self.get_api(
            "track.subtitles.get",
            {
                "track_id": track_id,
            },
            raise_on_faulty=False,
        )

    def get_track_from_spotify_id(self, spotify_id):
        return self.get_api(
            "track.subtitles.get",
            {
                "track_spotify_id": spotify_id,
            },
            raise_on_faulty=False,
        )
