"""Build the companion DBPF tuning package.

The compact writer follows the Sims 4 DBPF 2.1 resource/index layout and keeps
the editable XML files as the source of truth.
"""

import os
import struct
import sys
import zlib


HEADER = struct.Struct("<4sIIIIIIIIIII12sIQ24s")
INTERACTION = 0xE882D22F
SNIPPET = 0x7DF2169C
GROUP = 0
RESOURCES = (
    (SNIPPET, 0xD4E5286FEC3FECA3, "menu_injector.xml"),
    (INTERACTION, 0xE0F10AF9CE100E8E, "apply_plague.xml"),
    (INTERACTION, 0x36BCE7E6B9A79B0A, "apply_smallpox.xml"),
    (INTERACTION, 0x5DB55FE6F5AFA692, "apply_cholera.xml"),
    (INTERACTION, 0x3A8A7D764990CD4F, "apply_typhus.xml"),
    (INTERACTION, 0xC20ADE0DAA016FD9, "apply_dysentery.xml"),
    (INTERACTION, 0x992F3B97F1A11934, "apply_scarlet_fever.xml"),
    (INTERACTION, 0x3158E3209FCB2B76, "clear_diseases.xml"),
)


def build(source_dir, output_path):
    entries = []
    cursor = HEADER.size
    for type_id, instance_id, filename in sorted(RESOURCES):
        raw = open(os.path.join(source_dir, filename), "rb").read()
        stored = zlib.compress(raw)
        entries.append((type_id, instance_id, cursor, stored, len(raw)))
        cursor += len(stored)

    index_offset = cursor
    index_size = 4 + len(entries) * 32
    header = HEADER.pack(
        b"DBPF", 2, 1, 0, 0, 0, 0, 0, 0, len(entries), index_offset,
        index_size, b"\0" * 12, 3, index_offset, b"\0" * 24,
    )
    with open(output_path, "wb") as package:
        package.write(header)
        for _, _, _, stored, _ in entries:
            package.write(stored)
        package.write(struct.pack("<I", 0))
        for type_id, instance_id, offset, stored, raw_size in entries:
            package.write(struct.pack(
                "<IIIIIIIHH", type_id, GROUP, instance_id >> 32,
                instance_id & 0xFFFFFFFF, offset,
                len(stored) | 0x80000000, raw_size, 0x5A42, 1,
            ))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
