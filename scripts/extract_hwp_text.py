# -*- coding: utf-8 -*-
"""
HWP(5.x) 문서의 본문 텍스트를 추출한다.

HWP 5.x는 OLE 복합 파일이며, 본문은 BodyText/Section* 스트림에
(대개 zlib raw deflate로 압축된) 레코드로 저장된다. 이 중 문단 텍스트 레코드
(HWPTAG_PARA_TEXT = 67)만 UTF-16LE로 읽어 문단 단위로 이어 붙인다.
표·글상자 안의 텍스트도 문단 레코드라 함께 추출된다. 서식·이미지는 버린다.

사용법:
  python scripts/extract_hwp_text.py <입력.hwp> <출력.txt>

의존성: olefile (requirements.txt)
"""
import re
import struct
import sys
import zlib
from pathlib import Path

import olefile

HWPTAG_PARA_TEXT = 67
# 문단 텍스트 안의 컨트롤 문자: 2바이트 문자 + 추가 14바이트를 차지하는 확장/인라인 컨트롤
EXTENDED_CONTROL_CHARS = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
INLINE_CONTROL_CHARS = {4, 5, 6, 7, 8, 9, 19, 20}


def is_compressed(ole: olefile.OleFileIO) -> bool:
    header = ole.openstream("FileHeader").read()
    flags = struct.unpack("<I", header[36:40])[0]
    return bool(flags & 0x01)


def iter_records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        header = struct.unpack("<I", data[pos:pos + 4])[0]
        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF
        pos += 4
        if size == 0xFFF:  # 4095 이상이면 실제 크기가 다음 4바이트에 있다
            size = struct.unpack("<I", data[pos:pos + 4])[0]
            pos += 4
        yield tag_id, data[pos:pos + size]
        pos += size


def decode_para_text(payload: bytes) -> str:
    chars, i = [], 0
    while i + 2 <= len(payload):
        code = struct.unpack("<H", payload[i:i + 2])[0]
        if code in EXTENDED_CONTROL_CHARS or code in INLINE_CONTROL_CHARS:
            i += 16
            if code == 9:
                chars.append("\t")
            continue
        if code in (10, 13):
            chars.append("\n")
        elif code >= 32:
            chars.append(chr(code))
        i += 2
    return "".join(chars)


def extract_text(path: Path) -> str:
    ole = olefile.OleFileIO(str(path))
    compressed = is_compressed(ole)
    sections = sorted(
        (entry for entry in ole.listdir() if entry[0] == "BodyText"),
        key=lambda entry: int(re.sub(r"\D", "", entry[1]) or 0),
    )
    paragraphs = []
    for entry in sections:
        data = ole.openstream(entry).read()
        if compressed:
            data = zlib.decompress(data, -15)
        for tag_id, payload in iter_records(data):
            if tag_id == HWPTAG_PARA_TEXT:
                text = decode_para_text(payload).strip()
                if text:
                    paragraphs.append(text)
    ole.close()
    return "\n".join(paragraphs)


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    text = extract_text(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"{src.name}: {len(text)}자 추출 -> {dst}")
