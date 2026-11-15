"""Bounded requests sessions for PyVO (which otherwise supplies no timeout)."""
import requests


class TimeoutSession(requests.Session):
    def __init__(self, timeout=30.0):
        super().__init__()
        self.default_timeout = timeout

    def request(self, method, url, **kwargs):
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = self.default_timeout
        return super().request(method, url, **kwargs)
