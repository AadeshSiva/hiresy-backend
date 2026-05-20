import bcrypt

def hash_password(password: str) -> str:
    """Hash a password using bcrypt, truncating to 72 bytes first."""
    # Encode to bytes and truncate to 72 bytes
    pwd_bytes = password.encode('utf-8')[:72]
    # Generate salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    # Return as string for storage
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    # Truncate plain password to 72 bytes
    pwd_bytes = plain_password.encode('utf-8')[:72]
    # Compare
    return bcrypt.checkpw(pwd_bytes, hashed_password.encode('utf-8'))