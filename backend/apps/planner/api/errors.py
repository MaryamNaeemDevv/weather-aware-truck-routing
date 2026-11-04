"""Uniform error envelope: {"error": {"code", "message", "fields"?}}."""

import logging

from rest_framework import status
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler

from ..providers.base import ProviderError

log = logging.getLogger(__name__)


def exception_handler(exc, context):
    if isinstance(exc, ProviderError):
        return Response({"error": {"code": exc.code, "message": exc.message}}, status=exc.status)
    if isinstance(exc, ValidationError):
        return Response(
            {
                "error": {
                    "code": "invalid_input",
                    "message": "Please check the highlighted fields.",
                    "fields": exc.detail,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(exc, Throttled):
        return Response(
            {"error": {"code": "rate_limited", "message": "Too many requests — please wait a moment and retry."}},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    response = drf_handler(exc, context)
    if response is not None:
        return Response({"error": {"code": "error", "message": str(exc)}}, status=response.status_code)
    log.exception("Unhandled error")
    return Response(
        {"error": {"code": "internal", "message": "Something went wrong on our side. Please try again."}},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
