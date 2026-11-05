"""Ogg checksum compatibility, including the largest possible page."""
import random
import io
import unittest

from custom_voice_pack_format import ogg_crc, opus_info


def reference_crc(data):
    value = 0
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value


class OggCRC(unittest.TestCase):
    def test_audio_page_tampering_is_still_rejected(self):
        import soundfile as sf
        stream = io.BytesIO()
        sf.write(stream, [0.0] * 24000, 24000, format='OGG', subtype='OPUS')
        original = stream.getvalue()
        self.assertEqual(opus_info(original), 1)
        corrupted = bytearray(original)
        corrupted[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'Ogg'):
            opus_info(corrupted)
    def test_known_vectors(self):
        self.assertEqual(ogg_crc(b''), 0)
        self.assertEqual(ogg_crc(b'123456789'), 0x89a1897f)

    def test_matches_original_polynomial_for_all_bytes_and_page_sizes(self):
        randomizer = random.Random(20261105)
        for data in [bytes(range(256)), bytes(65307), *[randomizer.randbytes(n) for n in (1, 27, 255, 4096, 65307)]]:
            with self.subTest(length=len(data)):
                self.assertEqual(ogg_crc(data), reference_crc(data))
                self.assertEqual(ogg_crc(bytearray(data)), reference_crc(data))
