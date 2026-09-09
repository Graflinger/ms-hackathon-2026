"""Optional Azure clients isolated from OpenAI's process-wide configuration."""
import openai
from openai import AsyncAzureOpenAI, AzureOpenAI


class _ExplicitAzure:
    def __init__(self, **kwargs):
        version = tuple(int(part) for part in openai.__version__.split(".")[:2])
        if not (3, 8) <= version < (4, 0):
            raise RuntimeError("GoldenLoop live clients require openai>=3.8,<4")
        kwargs.update(organization="", project="", admin_api_key="", webhook_secret="")
        super().__init__(**kwargs)
        # The SDK merges OPENAI_CUSTOM_HEADERS even with explicit default_headers.
        # Never retain those values for requests or copies of this client.
        self._custom_headers = {}
        self._ambient_authorizations = frozenset()
        self.organization = self.project = None

    @property
    def default_headers(self):
        # Azure injects the explicit key/token into request options separately.
        return {"Accept": "application/json", "Content-Type": "application/json",
                "User-Agent": "GoldenLoop/0.2.0"}


class ExplicitAzureOpenAI(_ExplicitAzure, AzureOpenAI):
    pass


class ExplicitAsyncAzureOpenAI(_ExplicitAzure, AsyncAzureOpenAI):
    pass
