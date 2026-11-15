"""Raw-token checks for the explicit business-data JSON write routes."""

import json

from fastapi import HTTPException, Request

from document_pipeline_api.schemas.json_numbers import NumericPrecisionError, loads_json_numbers


async def validate_numeric_body(request: Request) -> None:
    # FastAPI may already have decoded the body; cached original bytes still
    # let us reject a rounded lexeme before the handler can mutate any data.
    try:
        loads_json_numbers(await request.body())
    except NumericPrecisionError as error:
        raise HTTPException(422, str(error)) from error
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Syntax/content-type errors remain FastAPI's normal body validation.
        return
