"""Compare Telethon's fallback AES with cryptg using synthetic data, offline."""

from statistics import median
from time import perf_counter
from unittest.mock import patch

from telethon.crypto import aes


def main():
    if aes.cryptg is None:
        raise SystemExit('Install acceleration first: python -m pip install -e ".[fast]"')
    data, key, iv = b"x" * (64 * 1024), bytes(range(32)), bytes(range(32))
    durations = {}
    ciphertexts = {}
    fallback = "libssl" if aes.libssl.encrypt_ige else "Python"
    for name, backend in ((fallback, None), ("cryptg", aes.cryptg)):
        samples = []
        with patch.object(aes, "cryptg", backend):
            for _ in range(3):
                started = perf_counter()
                encrypted = aes.AES.encrypt_ige(data, key, iv)
                samples.append(perf_counter() - started)
            assert aes.AES.decrypt_ige(encrypted, key, iv) == data
        ciphertexts[name] = encrypted
        durations[name] = median(samples)
        print(f"{name}: {len(data) / durations[name] / 1024 / 1024:.2f} MiB/s (AES encryption)")
    assert ciphertexts[fallback] == ciphertexts["cryptg"]
    print(f"AES speedup: {durations[fallback] / durations['cryptg']:.2f}x")
    print("CPU-only measurement; Telegram transfer speed also depends on network and server limits.")


if __name__ == "__main__":
    main()
