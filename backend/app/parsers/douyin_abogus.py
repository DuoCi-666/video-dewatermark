"""抖音 a_bogus 签名算法（纯算法移植，无外部依赖）。

来源与许可
----------
参考 `wujunwei928/parse-video`（Go）的 a_bogus 实现（``parser/douyin_detail.go``），
按其算法逻辑用 Python 重新实现，未复制其源码。

算法构成
--------
- **SM3**（国标哈希，IV 即国标初始值）；
- ``double_sm3(x) = sm3(sm3(x))``，分别用于 query 与 method；
- **RC4**（密钥字节为 ``'y'``，即 121）；
- **自定义 base64** 字母表（见 ``ALPHABET``）；
- 浏览器指纹串 ``BROWSER`` 与 UA 指纹码 ``UA_CODE``（固定常量）。

用于给抖音 web 详情接口（``/aweme/v1/web/aweme/detail/``）签名。
"""
from __future__ import annotations

from urllib.parse import quote

ALPHABET = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"
BROWSER = "1536|742|1536|864|0|0|0|0|1536|864|1536|864|1536|742|24|24|MacIntel"

INIT_STATE = [
    1937774191,
    1226093241,
    388252375,
    3666478592,
    2842636476,
    372324522,
    3817729613,
    2969243214,
]

UA_CODE = [
    76, 98, 15, 131, 97, 245, 224, 133,
    122, 199, 241, 166, 79, 34, 90, 191,
    128, 126, 122, 98, 66, 11, 14, 40,
    49, 110, 110, 173, 67, 96, 138, 252,
]

_MASK32 = 0xFFFFFFFF


def _rotl(x: int, n: int) -> int:
    n &= 31
    return ((x << n) | (x >> (32 - n))) & _MASK32


def _p0(x: int) -> int:
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _p1(x: int) -> int:
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def sm3(message: bytes) -> bytes:
    """标准 SM3 哈希（国标初始值），返回 32 字节摘要。"""
    bit_length = (len(message) * 8) & 0xFFFFFFFFFFFFFFFF
    data = bytearray(message) + b"\x80"
    while len(data) % 64 != 56:
        data.append(0)
    data += bit_length.to_bytes(8, "big")

    state = list(INIT_STATE)
    for offset in range(0, len(data), 64):
        block = data[offset:offset + 64]
        words = [int.from_bytes(block[i * 4:i * 4 + 4], "big") for i in range(16)]
        for i in range(16, 68):
            words.append(
                _p1(words[i - 16] ^ words[i - 9] ^ _rotl(words[i - 3], 15))
                ^ _rotl(words[i - 13], 7)
                ^ words[i - 6]
            )
        expanded = [words[i] ^ words[i + 4] for i in range(64)]

        a, b, c, d, e, f, g, h = state
        for i in range(64):
            if i < 16:
                ff = a ^ b ^ c
                gg = e ^ f ^ g
                const = 0x79CC4519
            else:
                ff = (a & b) | (a & c) | (b & c)
                gg = (e & f) | ((~e & _MASK32) & g)
                const = 0x7A879D8A
            ss1 = _rotl((_rotl(a, 12) + e + _rotl(const, i)) & _MASK32, 7)
            ss2 = ss1 ^ _rotl(a, 12)
            tt1 = (ff + d + ss2 + expanded[i]) & _MASK32
            tt2 = (gg + h + ss1 + words[i]) & _MASK32
            d = c
            c = _rotl(b, 9)
            b = a
            a = tt1
            h = g
            g = _rotl(f, 19)
            f = e
            e = _p0(tt2)
        state = [state[i] ^ v for i, v in enumerate((a, b, c, d, e, f, g, h))]

    return b"".join(word.to_bytes(4, "big") for word in state)


def double_sm3(value: bytes) -> bytes:
    return sm3(sm3(value))


def _rc4(plaintext: list[int]) -> list[int]:
    state = list(range(256))
    position = 0
    for i in range(256):
        position = (position + state[i] + ord("y")) % 256
        state[i], state[position] = state[position], state[i]

    position = 0
    result = [0] * len(plaintext)
    for i, value in enumerate(plaintext):
        index = (i + 1) % 256
        position = (position + state[index]) % 256
        state[index], state[position] = state[position], state[index]
        result[i] = state[(state[index] + state[position]) % 256] ^ value
    return result


def _encode(values: list[int]) -> str:
    out: list[str] = []
    for index in range(0, len(values), 3):
        number = (values[index] & _MASK32) << 16
        if index + 1 < len(values):
            number |= (values[index + 1] & _MASK32) << 8
        if index + 2 < len(values):
            number |= values[index + 2] & _MASK32
        number &= _MASK32
        out.append(ALPHABET[(number >> 18) & 63])
        out.append(ALPHABET[(number >> 12) & 63])
        if index + 1 < len(values):
            out.append(ALPHABET[(number >> 6) & 63])
        if index + 2 < len(values):
            out.append(ALPHABET[number & 63])
    text = "".join(out)
    while len(text) % 4 != 0:
        text += "="
    return text


def _random_group(value: int, extra1: int, extra2: int, extra3: int, extra4: int) -> list[int]:
    low = value & 255
    high = (value >> 8) & 255
    return [
        (low & 170) | extra1,
        (low & 85) | extra2,
        (high & 170) | extra3,
        (high & 85) | extra4,
    ]


def make_a_bogus(
    query: str,
    method: str,
    started: int,
    finished: int,
    random1: int,
    random2: int,
    random3: int,
) -> str:
    """按算法构造 a_bogus 签名。"""
    params_hash = double_sm3((query + "cus").encode())
    method_hash = double_sm3((method + "cus").encode())

    payload: list[int] = [
        44,
        (finished >> 24) & 255,
        0, 0, 0, 0,
        24,
        params_hash[21],
        method_hash[21],
        0,
        UA_CODE[23],
        (finished >> 16) & 255,
        0, 0, 0,
        1,
        0,
        239,
        params_hash[22],
        method_hash[22],
        UA_CODE[24],
        (finished >> 8) & 255,
        0, 0, 0, 0,
        finished & 255,
        0, 0,
        14,
        (started >> 24) & 255,
        (started >> 16) & 255,
        0,
        (started >> 8) & 255,
        3,
        finished >> 32,
        1,
        started >> 32,
        1,
        len(BROWSER),
        0, 0, 0,
    ]

    checksum = 0
    for value in payload:
        checksum ^= value
    payload += list(BROWSER.encode("ascii"))
    payload.append(checksum)

    prefix = (
        _random_group(random1, 1, 2, 5, 45 & 170)
        + _random_group(random2, 1, 0, 0, 0)
        + _random_group(random3, 1, 0, 5, 0)
    )
    ciphertext = _rc4(payload)
    return _encode(prefix + ciphertext)


# web 详情接口的固定查询参数（顺序参与签名，不可调整）
_DETAIL_PARAMS: list[tuple[str, str]] = [
    ("device_platform", "webapp"),
    ("aid", "6383"),
    ("channel", "channel_pc_web"),
    ("pc_client_type", "1"),
    ("version_code", "290100"),
    ("version_name", "29.1.0"),
    ("cookie_enabled", "true"),
    ("screen_width", "1920"),
    ("screen_height", "1080"),
    ("browser_language", "zh-CN"),
    ("browser_platform", "Win32"),
    ("browser_name", "Chrome"),
    ("browser_version", "130.0.0.0"),
    ("browser_online", "true"),
    ("engine_name", "Blink"),
    ("engine_version", "130.0.0.0"),
    ("os_name", "Windows"),
    ("os_version", "10"),
    ("cpu_core_num", "12"),
    ("device_memory", "8"),
    ("platform", "PC"),
    ("downlink", "10"),
    ("effective_type", "4g"),
    ("from_user_page", "1"),
    ("locate_query", "false"),
    ("need_time_list", "1"),
    ("pc_libra_divert", "Windows"),
    ("publish_video_strategy_type", "2"),
    ("round_trip_time", "0"),
    ("show_live_replay_strategy", "1"),
    ("time_list_query", "0"),
    ("whale_cut_token", ""),
    ("update_version_code", "170400"),
    ("msToken", ""),
]


def web_detail_query(video_id: str) -> str:
    """生成 web 详情接口的查询串（不含 a_bogus）。"""
    parts = [
        f"{quote(key, safe='')}={quote(value, safe='')}"
        for key, value in _DETAIL_PARAMS
    ]
    parts.append(f"aweme_id={quote(video_id, safe='')}")
    return "&".join(parts)
