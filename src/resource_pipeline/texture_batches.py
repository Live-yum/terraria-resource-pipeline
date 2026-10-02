"""Header-only, bounded planning for a complete private Content tree.

No compressed payload or game assembly is loaded here. 2 pixels per expanded
byte is a conservative upper bound for the supported DXT1/3/5 and RGBA formats;
the decoder independently validates every dimension, mip and byte count.
"""
from dataclasses import dataclass
from pathlib import Path
import struct

from .contracts import package_file
from .security import PipelineError


@dataclass(frozen=True)
class TextureBatchPolicy:
    files_per_child: int = 256
    child_pixels: int = 256_000_000
    image_pixels: int = 16_000_000
    max_children: int = 256
    # Sequential work allowance, never an allocation or child-decoder limit.
    total_pixels: int = 4 * 256_000_000
    expanded_bytes: int = 4 * 1024**3

    def __post_init__(self):
        maxima = (256, 256_000_000, 16_000_000, 256, 1_024_000_000, 4 * 1024**3)
        values = (self.files_per_child, self.child_pixels, self.image_pixels,
                  self.max_children, self.total_pixels, self.expanded_bytes)
        if any(type(value) is not int or not 0 < value <= maximum
               for value, maximum in zip(values, maxima)):
            raise ValueError('Texture batch policy may tighten, never raise, fixed limits')


@dataclass(frozen=True)
class TextureBatchPlan:
    batches: tuple[tuple[dict, ...], ...]
    declared_expanded_bytes: int
    pixel_risk: int


def plan_texture_batches(source: Path, inventory: list[dict],
                         policy: TextureBatchPolicy = TextureBatchPolicy(),
                         check=lambda: None) -> TextureBatchPlan:
    batches, current = [], []
    expanded_total = risk_total = current_risk = 0
    for row in inventory:
        check()
        with package_file(source, row['path']).open('rb') as stream:
            header = stream.read(14)
        check()
        if (len(header) < 10 or header[:3] != b'XNB' or header[3] not in b'wmx'
                or header[4] != 5 or header[5] & ~0x81
                or struct.unpack_from('<i', header, 6)[0] != row['bytes']):
            raise PipelineError('TEXTURE_REJECTED:HEADER:InvalidDataException')
        if header[5] & 0x80:
            if len(header) < 14:
                raise PipelineError('TEXTURE_REJECTED:HEADER:InvalidDataException')
            expanded = struct.unpack_from('<i', header, 10)[0]
        else:
            expanded = row['bytes'] - 10
        if not 0 < expanded <= 128 * 1024**2:
            raise PipelineError('TEXTURE_REJECTED:HEADER:InvalidDataException')
        expanded_total += expanded
        if expanded_total > policy.expanded_bytes:
            raise PipelineError('Texture job declared-expanded-byte budget exceeded')
        risk = min(policy.image_pixels, 2 * expanded)
        if risk > policy.child_pixels:
            raise PipelineError('Texture cannot fit the child pixel-risk budget')
        risk_total += risk
        if current and (len(current) >= policy.files_per_child or current_risk + risk > policy.child_pixels):
            batches.append(tuple(current))
            current, current_risk = [], 0
        current.append(row)
        current_risk += risk
    if current:
        batches.append(tuple(current))
    if len(batches) > policy.max_children:
        raise PipelineError('Texture job child-count budget exceeded')
    check()
    return TextureBatchPlan(tuple(batches), expanded_total, risk_total)
