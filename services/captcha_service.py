"""
CAPTCHA service for generating and validating CAPTCHAs
Uses Redis for temporary storage of CAPTCHA codes
"""
import io
import secrets
import string
from typing import Tuple, Optional
from captcha.image import ImageCaptcha
from services.redis_manager import redis_manager


class CaptchaService:
    """Service for CAPTCHA generation and validation"""
    
    def __init__(self):
        self.image_captcha = ImageCaptcha(width=200, height=80)
        self.code_length = 4
        self.expiration_seconds = 300  # 5 minutes
    
    def _generate_code(self) -> str:
        """Generate a random CAPTCHA code"""
        # Use uppercase letters and digits, excluding confusing characters
        chars = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '').replace('1', '')
        return ''.join(secrets.choice(chars) for _ in range(self.code_length))
    
    async def generate_captcha(self) -> Tuple[str, bytes]:
        """
        Generate a new CAPTCHA
        Returns: (token, image_bytes)
        """
        # Generate unique token
        token = secrets.token_urlsafe(32)
        
        # Generate CAPTCHA code
        code = self._generate_code()
        
        # Store code in Redis with expiration
        await redis_manager.client.setex(
            f"captcha:{token}",
            self.expiration_seconds,
            code
        )
        
        # Generate CAPTCHA image
        image_data = self.image_captcha.generate(code)
        image_bytes = image_data.getvalue() if hasattr(image_data, 'getvalue') else image_data.read()
        
        return token, image_bytes
    
    async def validate_captcha(self, token: str, code: str) -> bool:
        """
        Validate a CAPTCHA code
        Args:
            token: The CAPTCHA token
            code: User-provided CAPTCHA code
        Returns: True if valid, False otherwise
        """
        if not token or not code:
            return False
        
        # Get stored code from Redis
        key = f"captcha:{token}"
        stored_code = await redis_manager.client.get(key)
        
        if not stored_code:
            return False
        
        # Decode if bytes
        if isinstance(stored_code, bytes):
            stored_code = stored_code.decode('utf-8')
        
        # Delete the token after validation (one-time use)
        await redis_manager.client.delete(key)
        
        # Case-insensitive comparison
        return code.upper() == stored_code.upper()
    
    async def refresh_captcha(self, old_token: str) -> Tuple[str, bytes]:
        """
        Refresh a CAPTCHA (delete old one and generate new one)
        Args:
            old_token: The old CAPTCHA token to invalidate
        Returns: (new_token, image_bytes)
        """
        # Delete old token if provided
        if old_token:
            await redis_manager.client.delete(f"captcha:{old_token}")
        
        # Generate new CAPTCHA
        return await self.generate_captcha()


# Global instance
captcha_service = CaptchaService()
