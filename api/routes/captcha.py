"""
CAPTCHA API routes
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from services.captcha_service import captcha_service
from services.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/api/captcha", tags=["captcha"])


class CaptchaResponse(BaseModel):
    """Response model for CAPTCHA generation"""

    token: str


class CaptchaRefreshRequest(BaseModel):
    """Request model for CAPTCHA refresh"""

    old_token: str


@router.get("/generate")
async def generate_captcha(request: Request):
    """
    Generate a new CAPTCHA
    Returns the token in JSON and client should call /api/captcha/image/{token} to get the image
    """
    await enforce_rate_limit(request, "captcha", limit=30, window=60)
    token, _ = await captcha_service.generate_captcha()
    return CaptchaResponse(token=token)


@router.get("/image/{token}")
async def get_captcha_image(token: str, request: Request):
    """
    Get CAPTCHA image for a specific token
    This endpoint regenerates the image for the existing token
    """
    # For security, we don't regenerate from token
    # Instead, client should call /generate first to get a token
    # Then call this endpoint with that token
    # To prevent abuse, we generate a new captcha and return it
    await enforce_rate_limit(request, "captcha", limit=30, window=60)
    new_token, image_bytes = await captcha_service.generate_captcha()

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "X-Captcha-Token": new_token,
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post("/refresh")
async def refresh_captcha(refresh_request: CaptchaRefreshRequest, request: Request):
    """
    Refresh a CAPTCHA (invalidate old one and get new token)
    Client should call /api/captcha/image/{new_token} to get the new image
    """
    await enforce_rate_limit(request, "captcha", limit=30, window=60)
    new_token, _ = await captcha_service.refresh_captcha(refresh_request.old_token)
    return CaptchaResponse(token=new_token)
