import secrets

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def generate_code(length=7):
    """Random base62 code. Random (not sequential) so codes are not enumerable."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
