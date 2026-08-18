"""Build the companion DBPF tuning package.

The compact writer follows the Sims 4 DBPF 2.1 resource/index layout and keeps
the editable XML files as the source of truth.
"""

import base64
import os
import struct
import sys
import zlib


HEADER = struct.Struct("<4sIIIIIIIIIII12sIQ24s")
INTERACTION = 0xE882D22F
SNIPPET = 0x7DF2169C
BUFF = 0x6017E896
SIMDATA = 0x545AC67A
STBL = 0x220557DA
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

MOODLETS = (
    (0xED0927480477A9CA, "moodlet_plague.xml", 0xB0D95CAC, 0x2C577503,
     "Signs of the Plague", "Fever, chills, painful swellings, and terrible weakness have taken hold."),
    (0xF7A64864C26434C6, "moodlet_smallpox.xml", 0x312BD680, 0x5616AE5F,
     "Marked by Smallpox", "High fever and exhaustion are followed by a spreading, painful pustular rash."),
    (0xC331FDB990F87CDE, "moodlet_cholera.xml", 0x1D738CF8, 0x55E27387,
     "The Blue Death", "Violent watery illness, cramps, thirst, and dangerous dehydration came without warning."),
    (0x6BB3B4BD6227451B, "moodlet_typhus.xml", 0x613EB667, 0x7B4EDBC6,
     "Camp Fever", "A sudden fever, pounding headache, deep weakness, and a spreading rash cloud every thought."),
    (0xF27E27596C6931B5, "moodlet_dysentery.xml", 0x3FC945FD, 0x49A89BA0,
     "The Bloody Flux", "Severe abdominal pain, fever, and bloody illness leave the body dangerously depleted."),
    (0xF18C41F70F4A4E48, "moodlet_scarlet_fever.xml", 0x1E038636, 0x4FF8A159,
     "Scarlet Fever", "A burning throat, high fever, flushed cheeks, and a rough scarlet rash have appeared."),
)

# Neutral SimData structure for a visible Uncomfortable +2 sickness moodlet.
# The two localization fields are replaced for every generated moodlet.
SIMDATA_TEMPLATE = base64.b64decode(
    "REFUQQEBAAAYAAAAAQAAAMAAAAABAAAACgAAAAAAAAD9AQAAzJO3OKgAAAANAAAAUAAAAEwAAAAB"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAABGxmTPFrn4ir7jBP0AAAAA6kZVohYy8zu+4wT9AAAAADt0xcA4EgO3FvY0xq"
    "GHDdUEAH0vAAAAADY5AAAAAAAAAgAAAAAAAAABAAAAAAAAAEgBAACW6BdgVilycVAAAAAIAAAACQ"
    "AAAMcAAAB44fMaEwAAABAAAAAAAACA6QAAAC+jnBsSAAAAOAAAAAAAAIDQAAAAepOqbhMAAAAoAA"
    "AAAAAAgKEAAABJiwWRFAAAACAAAAAAAACAngAAAKxVLqIUAAAAJAAAAAAAAICjAAAAc3sltgYAAA"
    "BAAAAAAAAAgJsAAADW38fAFAAAAEQAAAAAAACAlgAAAKfSPNsGAAAASAAAAAAAAIAUAAAAsWlw5h"
    "MAAAAAAAAAAAAAgGF1ZGlvX3N0aW5nX29uX2FkZABhdWRpb19zdGluZ19vbl9yZW1vdmUAYnVm"
    "Zl9kZXNjcmlwdGlvbgBidWZmX25hbWUAaWNvbgBtb29kX3R5cGUAbW9vZF93ZWlnaHQAdGltZW"
    "91dF9zdHJpbmcAdWlfc29ydF9vcmRlcgBCdWZmAGFkZWVwaW5kaWdvX0hlYWx0aGNhcmVSZWR1"
    "eF9TeW1wdG9tc19Tb3JlVGhyb2F0QnVmZgA="
)
SIMDATA_TEMPLATE = SIMDATA_TEMPLATE[:57] + b"\0\0\0" + SIMDATA_TEMPLATE[57:]


def build_stbl(strings):
    encoded = [(key, value.encode("utf-8")) for key, value in strings]
    result = bytearray(b"STBL")
    result.extend(struct.pack("<HBQ2sI", 5, 0, len(encoded), b"\0\0",
                              sum(len(value) + 1 for _, value in encoded)))
    for key, value in encoded:
        result.extend(struct.pack("<IBH", key, 0, len(value)))
        result.extend(value)
    return bytes(result)


def build(source_dir, output_path):
    resources = []
    for type_id, instance_id, filename in RESOURCES:
        resources.append((type_id, instance_id,
                          open(os.path.join(source_dir, filename), "rb").read()))
    strings = []
    for instance_id, filename, name_key, description_key, name, description in MOODLETS:
        resources.append((BUFF, instance_id,
                          open(os.path.join(source_dir, filename), "rb").read()))
        simdata = bytearray(SIMDATA_TEMPLATE)
        struct.pack_into("<I", simdata, 160, description_key)
        struct.pack_into("<I", simdata, 164, name_key)
        resources.append((SIMDATA, instance_id, bytes(simdata)))
        strings.extend(((name_key, name), (description_key, description)))
    resources.append((STBL, 0x00C25A2F2EC7931D, build_stbl(strings)))

    entries = []
    cursor = HEADER.size
    for type_id, instance_id, raw in sorted(resources):
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
