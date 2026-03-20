"""AWS Bedrock provider.

# To configure: set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION in .env
# and install boto3: uv pip install boto3
#
# boto3 is optional — if not installed, BedrockProvider raises ImportError
# with install instructions rather than crashing at import time.
#
# If you ever want to use it, it needs a real test. * * * * * *
"""

from __future__ import annotations

import os
from typing import Any

from orchid.errors import ProviderError
from orchid.providers.base import ProviderBase


class BedrockProvider(ProviderBase):
    """AWS Bedrock inference via the Converse API.

    Requires:
      AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or an IAM instance role)
      AWS_REGION (default us-east-1)
      boto3 installed: uv pip install boto3

    Supported models: any Bedrock-accessible model ID.
    Default: anthropic.claude-sonnet-4-6-v1:0
    """

    name = "bedrock"

    def __init__(
        self,
        model: str | None = None,
        region: str | None = None,
    ) -> None:
        super().__init__()
        self.model = model or os.environ.get(
            "BEDROCK_MODEL", "anthropic.claude-sonnet-4-6-v1:0"
        )
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")

    def _check_availability(self) -> bool:
        # Check credentials presence only — no network call
        has_key = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
        has_secret = bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))
        # Also accept IAM instance roles (no env creds needed, but we can't probe)
        if not has_key or not has_secret:
            missing = []
            if not has_key:
                missing.append("AWS_ACCESS_KEY_ID")
            if not has_secret:
                missing.append("AWS_SECRET_ACCESS_KEY")
            self._missing_detail = f"{', '.join(missing)} not set"
            return False
        return True

    def fix_suggestion(self) -> str:
        return (
            "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION in .env\n"
            "  and install boto3: uv pip install boto3"
        )

    def complete(
        self,
        messages: list[Any],
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            import boto3  # noqa: PLC0415
        except ImportError:
            raise ProviderError(
                "boto3 is required for BedrockProvider. "
                "Install it: uv pip install boto3"
            )

        client = boto3.client(
            "bedrock-runtime",
            region_name=self.region,
        )

        raw = self._normalise_messages(messages)

        # Convert to Bedrock Converse API format
        bedrock_messages = []
        for m in raw:
            role = m.get("role", "user")
            content = m.get("content", "")
            # Bedrock only accepts "user" and "assistant" roles
            if role == "system":
                continue  # handled via system param below
            bedrock_messages.append({"role": role, "content": [{"text": content}]})

        request: dict[str, Any] = {
            "modelId": kwargs.pop("model", self.model),
            "messages": bedrock_messages,
        }
        if system:
            request["system"] = [{"text": system}]

        max_tokens = kwargs.pop("max_tokens", 4096)
        request["inferenceConfig"] = {"maxTokens": max_tokens}

        response = client.converse(**request)
        try:
            return response["output"]["message"]["content"][0]["text"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"Bedrock: unexpected response structure: {e}") from e

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "BedrockProvider does not implement embed(). "
            "Use LocalProvider or OllamaProvider for embeddings."
        )
