"""
Safety number and public key fingerprint generation.

Safety numbers provide an out-of-band channel for users to verify each
other's identity, preventing man-in-the-middle attacks.  The format is
inspired by Signal's safety numbers: a 5-group decimal code derived from
the sorted concatenation of both parties' public keys.
"""

import hashlib


def pubkey_fingerprint(public_key: bytes) -> str:
    """Compute the SHA-256 hex fingerprint of a public key.

    Args:
        public_key: Raw 32-byte X25519 public key.

    Returns:
        64-character lowercase hexadecimal string.
    """
    return hashlib.sha256(public_key).hexdigest()


def format_fingerprint(hex_fp: str) -> str:
    """Format a hex fingerprint into space-separated groups of 4 characters.

    Example:
        "abcd1234ef56..." → "abcd 1234 ef56 ..."

    Args:
        hex_fp: Hexadecimal string of arbitrary length.

    Returns:
        Space-separated groups of 4 characters.  If the length is not a
        multiple of 4 the final group will be shorter.
    """
    groups = [hex_fp[i : i + 4] for i in range(0, len(hex_fp), 4)]
    return " ".join(groups)


def safety_number(my_pub: bytes, their_pub: bytes) -> str:
    """Generate a deterministic 20-digit safety number for identity verification.

    The two public keys are sorted lexicographically before concatenation
    so both parties derive the same number regardless of call direction.

    Algorithm:
        1. Sort (my_pub, their_pub) lexicographically.
        2. Concatenate the sorted keys.
        3. SHA-256 hash the concatenation.
        4. Take the first 16 hex characters (64-bit value, max 18446744073709551615).
        5. Convert to decimal, zero-pad to 20 digits.
        6. Format as 5 groups of 4 digits separated by spaces.

    Args:
        my_pub:    Raw 32-byte X25519 public key of the local user.
        their_pub: Raw 32-byte X25519 public key of the remote user.

    Returns:
        A string like "1234 5678 9012 3456 7890" (20 digits in 5 groups).
    """
    # Deterministic ordering — both sides arrive at the same result
    first, second = sorted([my_pub, their_pub])

    digest = hashlib.sha256(first + second).hexdigest()

    # Take the first 16 hex characters → 64-bit integer
    value = int(digest[:16], 16)

    # Zero-pad to 20 decimal digits, then split into 5 groups of 4
    decimal_str = str(value).zfill(20)
    groups = [decimal_str[i : i + 4] for i in range(0, 20, 4)]
    return " ".join(groups)
