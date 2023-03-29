import sys


def stderr_print(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def setup_global_http_exception_hook():
    hook = sys.excepthook

    def exception_hook(exctype, value, traceback):
        if issubclass(exctype, UnexpectedResponse):

            match value.status_code:
                case 401:
                    match value.hint:
                        case "captcha":
                            stderr_print(
                                "- Unauthorized as the API now requests a captcha to be solved. Please try again later."
                            )
                        case "renew":
                            stderr_print("- Unauthorized as the API token is invalid.")
                        case default:
                            stderr_print(f"- Unauthorized due to {default!r}.")
                    return stderr_print(
                        "These issues can be resolved by deleting the token file (default: '.token' in root directory)."
                    )
                case 404:
                    return stderr_print(
                        "The requested resource was not found on the API. The track or lyrics may not exist."
                    )
                case default:
                    hook(exctype, value, traceback)

        return hook(exctype, value, traceback)

    sys.excepthook = exception_hook


setup_global_http_exception_hook()


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
        header = message["header"]

        if header["status_code"] > 399:
            raise UnexpectedResponse(message["header"])


class TrackError(Exception):
    pass


class TrackRestrictedError(TrackError):
    pass


class TrackNotFoundError(TrackError):
    pass


class LyricsNotFoundError(TrackError):
    pass
