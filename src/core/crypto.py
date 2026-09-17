import base64
import os
from pathlib import Path
from .config import AppConfig

try:
    from cryptography.fernet import Fernet
    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False


class PasswordEncryptor:
    """本地敏感信息对称加密器"""
    
    _key: bytes = None

    @classmethod
    def _get_or_create_key(cls) -> bytes:
        if cls._key is not None:
            return cls._key
            
        key_file = AppConfig.get_key_path()
        AppConfig.initialize()
        
        if key_file.exists():
            with open(key_file, "rb") as f:
                cls._key = f.read().strip()
        else:
            if HAS_FERNET:
                cls._key = Fernet.generate_key()
            else:
                # 简单备用密钥
                cls._key = base64.urlsafe_b64encode(os.urandom(32))
            with open(key_file, "wb") as f:
                f.write(cls._key)
                
        return cls._key

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        """加密明文字符串"""
        if not plaintext:
            return ""
        try:
            if HAS_FERNET:
                f = Fernet(cls._get_or_create_key())
                return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
            else:
                # 简易Base64编码兜底
                return base64.b64encode(plaintext.encode("utf-8")).decode("utf-8")
        except Exception as e:
            print(f"[Crypto Error] 加密失败: {e}")
            return plaintext

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        """解密密文字符串"""
        if not ciphertext:
            return ""
        try:
            if HAS_FERNET:
                f = Fernet(cls._get_or_create_key())
                return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
            else:
                return base64.b64decode(ciphertext.encode("utf-8")).decode("utf-8")
        except Exception:
            # 若解密失败（可能是旧版本明文），原样返回
            return ciphertext
