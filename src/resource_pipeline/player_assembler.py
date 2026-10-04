"""Bounded player presentation from explicit data-only RGBA frame recipes.

No recipe is inferred from a filename, old atlas, or guessed game draw rule.
This implements consumer assembly; source facts and recipe derivation remain
separate, unverified inputs. Receipts never authorize source publication.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from io import BytesIO
import json
import math
import re
import struct
import zlib
from PIL import Image, ImageChops

from .marker_assembler import _png_pixels
from .item_assembler import POOLS, GROUPS
from .security import PipelineError, canonical_json, relative_path, sha256

ALGORITHM = 'player-rgba-recipes-v1'
ITEM_ROLES = ('items.catalog', 'items.rules', 'items.categories')
HAIR_SETS = ('fullHairHeads', 'hatHairHeads', 'drawsBackWithoutHeadgear', 'facePreventHairDraw',
             'faceHeadLayer', 'faceMaskLayer', 'faceFlowerLayer', 'faceUnderHairLayer', 'faceOverrideHelmet')
UNLOCKS = ('extraAccessory', 'ateArtisanBread', 'usedAegisCrystal', 'usedAegisFruit', 'usedArcaneCrystal',
           'usedGalaxyPearl', 'usedGummyWorm', 'usedAmbrosia', 'unlockedBiomeTorches', 'unlockedSuperCart')
WING_FIELDS = {'frameCount', 'offsetX', 'offsetY', 'cropRight', 'cropBottom', 'anchorMode', 'special', 'requires', 'animateWhenIdle'}
WING_EFFECTS = {'rainbow-render-target', 'flame-wing-1866', 'flame-overlay', 'always-animated',
                'ghostar-infinity-eight', 'celestial-starboard-rainbow-trail', 'chicken-bones-glow', 'special-glow', 'luna-glow'}
MAX_OBJECT_BYTES = 32 * 1024 * 1024
MAX_TEXTURE_BYTES = 2 * 1024 * 1024


def _require(ok, why):
    if not ok: raise PipelineError(why)


def _int(v, low=0, high=65535):
    return type(v) is int and low <= v <= high


def _text(v, maximum=128, empty=False, controls=False):
    if not isinstance(v, str): return False
    try:
        length = len(v.encode('utf-16-le')) // 2
    except UnicodeEncodeError:
        return False
    return (0 if empty else 1) <= length <= maximum and (controls or not re.search(r'[\x00-\x1f\x7f]', v))


def _hash(v):
    return isinstance(v, str) and re.fullmatch('[a-f0-9]{64}', v) is not None


def _exact(v, fields): return type(v) is dict and set(v) == set(fields)


def _ids(v, low=0, high=4095, count=512):
    return type(v) is list and len(v) <= count and all(_int(n, low, high) for n in v) and len(set(v)) == len(v)


def _json(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_OBJECT_BYTES, 'Invalid bounded player input JSON')
    def pairs(rows):
        result = {}
        for key, value in rows:
            _require(key not in result, 'Duplicate player input JSON key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(PipelineError('Nonfinite JSON')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PipelineError('Invalid player input JSON') from exc


def _bounded_policy(value, checkpoint):
    """Reject cycles, exotic objects and huge trees before canonical hashing."""
    stack, active, count, characters = [(value, 0, False)], set(), 0, 0
    while stack:
        value, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(value)); continue
        count += 1
        _require(count <= 300000 and depth <= 24, 'Player policy structure budget exceeded')
        if count % 1000 == 0: checkpoint()
        if type(value) in (dict, list):
            _require(id(value) not in active, 'Circular player policy')
            _require(len(stack) + len(value) * (2 if type(value) is dict else 1) + count <= 300000,
                     'Player policy pending structure budget exceeded')
            active.add(id(value)); stack.append((value, depth, True))
            if type(value) is dict:
                _require(all(type(k) is str for k in value), 'Player policy keys must be strings')
                for key, item in value.items():
                    stack.append((key, depth + 1, False)); stack.append((item, depth + 1, False))
            else:
                stack.extend((item, depth + 1, False) for item in value)
        elif isinstance(value, str):
            try: characters += len(value.encode('utf-8'))
            except UnicodeEncodeError as exc: raise PipelineError('Invalid player policy Unicode') from exc
            _require(characters <= 16 * 1024 * 1024, 'Player policy text budget exceeded')
        elif type(value) in (int, float):
            _require(abs(value) <= 2**53 - 1 and math.isfinite(value), 'Invalid player policy number')
        else:
            _require(value is None or type(value) is bool, 'Unsupported player policy value')


def _item_foundation(objects, version):
    """Validate all three bound item roles, not only the IDs used by selection."""
    catalog = _json(objects['items.catalog'])
    _require(type(catalog) is list and len(catalog) == 7 and all(type(c) is list for c in catalog)
             and 0 < len(catalog[0]) <= 65535 and all(len(c) == len(catalog[0]) for c in catalog), 'Invalid item catalog foundation')
    _require(all(_int(n, 1) for n in catalog[0]) and catalog[0] == sorted(set(catalog[0]))
             and all(_text(n, 4096, controls=True) for c in catalog[1:3] for n in c)
             and len(set(catalog[2])) == len(catalog[2]) and all(_int(n) for n in catalog[3])
             and all(_int(n, 1) for n in catalog[4]) and all(_int(n, -1, 255) for n in catalog[5])
             and all(_int(n) for n in catalog[6]), 'Invalid item catalog columns')
    identities = set(catalog[0])
    rules = _json(objects['items.rules'])
    _require(type(rules) is dict and rules.get('version') == version
             and all(type(rules.get(k)) is dict for k in ('prefixes', 'items', 'pools', 'groups'))
             and type(rules.get('fields')) is list and len(rules['fields']) <= 128
             and all(_text(k) for k in rules['fields']) and len(set(rules['fields'])) == len(rules['fields'])
             and {'damage', 'useAnimation', 'mana', 'knockBack', 'accessory', 'vanity', 'dye'} <= set(rules['fields']), 'Invalid item rules foundation')
    def number(v): return type(v) in (int, float) and abs(v) <= 2**53 - 1 and math.isfinite(v)
    prefixes = rules['prefixes']
    _require('0' in prefixes and len(prefixes) <= 256, 'Missing item prefix zero definition')
    for key, row in prefixes.items():
        _require(isinstance(key, str) and re.fullmatch(r'0|[1-9][0-9]{0,2}', key) and _int(int(key), 0, 255)
                 and type(row) is dict and _text(row.get('name'), 4096, controls=True) and type(row.get('stats')) is dict
                 and all(number(n) for n in row['stats'].values()), 'Invalid item prefix foundation')
    for key in POOLS:
        row = rules['pools'].get(key)
        _require(_ids(row, 0, 255, 256) and all(str(n) in prefixes for n in row), 'Invalid item prefix pool foundation')
    for key in GROUPS:
        row = rules['groups'].get(key)
        _require(_ids(row, 1, 65535, 65535) and all(n in identities for n in row), 'Invalid item prefix group foundation')
    row = rules.get('noAccessoryPrefix')
    _require(_ids(row, 1, 65535, 65535) and all(n in identities for n in row), 'Invalid item prefix exclusion foundation')
    for key, values in rules['items'].items():
        _require(isinstance(key, str) and re.fullmatch(r'[1-9][0-9]{0,4}', key) and int(key) in identities
                 and type(values) is list and len(values) == len(rules['fields'])
                 and all(type(n) is bool if field in ('accessory', 'vanity') else number(n) for field, n in zip(rules['fields'], values)),
                 'Invalid item rule row foundation')
    categories = _json(objects['items.categories'])
    _require(type(categories) is dict and type(categories.get('keys')) is list and 0 < len(categories['keys']) <= 65535
             and all(type(k) is list and 0 < len(k) <= 128 and all(number(n) for n in k) for k in categories['keys'])
             and type(categories.get('items')) is list and len(categories['items']) == len(identities), 'Invalid item category foundation')
    seen = set()
    for row in categories['items']:
        _require(type(row) is list and len(row) == 5 and _int(row[0], 1) and row[0] in identities and row[0] not in seen
                 and _int(row[1], 0, 8191) and _int(row[2], 0, len(categories['keys']) - 1)
                 and _int(row[3], 0, 1) and _int(row[4]), 'Invalid item category row foundation')
        seen.add(row[0])
    return catalog


def _wing(rule):
    return (_exact(rule, WING_FIELDS) and _int(rule['frameCount'], 1, 32)
            and all(_int(rule[k], -512, 512) for k in ('offsetX', 'offsetY'))
            and all(_int(rule[k], 0, 512) for k in ('cropRight', 'cropBottom'))
            and rule['anchorMode'] in ('common-wing', 'player-center', 'body-center')
            and (rule['special'] is None or isinstance(rule['special'], str) and rule['special'] in WING_EFFECTS)
            and type(rule['animateWhenIdle']) is bool and type(rule['requires']) is list
            and len(rule['requires']) <= 16
            and all(isinstance(k, str) and re.fullmatch(r'(glow_mask|item_flame|flames|extra)_[0-9]{1,5}', k) for k in rule['requires'])
            and len(set(rule['requires'])) == len(rule['requires']))


def _validate_facts(facts, choices, catalog):
    _require(_exact(facts, ('buffs', 'dyes', 'hairRules', 'wingRules', 'selection', 'versionLabels')), 'Incomplete player facts')
    hair = facts['hairRules']
    _require(_exact(hair, (*HAIR_SETS, 'backHairStyle')) and all(_ids(hair[k]) for k in HAIR_SETS), 'Invalid player hair sets')
    back = hair['backHairStyle']
    _require(_exact(back, ('lowerExclusive', 'upperExclusive', 'excludedRanges', 'excludedIds', 'includedIds'))
             and _int(back['lowerExclusive'], 0, 4095) and _int(back['upperExclusive'], 1, 4095)
             and back['lowerExclusive'] < back['upperExclusive'] and _ids(back['excludedIds']) and _ids(back['includedIds'])
             and type(back['excludedRanges']) is list and len(back['excludedRanges']) <= 128, 'Invalid player back-hair rule')
    previous = back['lowerExclusive']
    for pair in back['excludedRanges']:
        _require(type(pair) is list and len(pair) == 2 and all(_int(n, 0, 4095) for n in pair)
                 and previous < pair[0] <= pair[1] < back['upperExclusive'], 'Invalid player back-hair ranges')
        previous = pair[1]
    _require(all(back['lowerExclusive'] < n < back['upperExclusive'] for n in back['excludedIds']), 'Invalid back-hair excluded ID')
    wings = facts['wingRules']
    _require(_exact(wings, ('default', 'slots')) and _wing(wings['default']) and type(wings['slots']) is dict
             and len(wings['slots']) <= 255, 'Invalid player wing rules')
    for key, value in wings['slots'].items():
        _require(isinstance(key, str) and re.fullmatch(r'[1-9][0-9]{0,2}', key) and _int(int(key), 1, 255) and _wing(value), 'Invalid wing slot')
    buffs = facts['buffs']
    _require(type(buffs) is dict and 0 < len(buffs) <= 4096, 'Missing player buffs')
    for key, row in buffs.items():
        _require(isinstance(key, str) and re.fullmatch(r'[1-9][0-9]{0,4}', key) and _int(int(key), 1)
                 and type(row) is list and len(row) == 2 and _text(row[0], 4096, controls=True)
                 and _text(row[1], 4096, empty=True, controls=True), 'Invalid player buff text')
    dyes = facts['dyes']
    _require(type(dyes) is list and 0 < len(dyes) <= 4096, 'Missing player dye definitions')
    item_ids, seen = set(catalog[0]), set()
    for dye in dyes:
        _require(type(dye) is dict and {'itemId', 'class', 'pass'} <= set(dye)
                 and set(dye) <= {'itemId', 'class', 'pass', 'color', 'saturation', 'secondaryColor', 'image'}
                 and _int(dye['itemId'], 1) and dye['itemId'] in item_ids and dye['itemId'] not in seen
                 and all(isinstance(dye[k], str) and re.fullmatch('[A-Za-z]{1,64}', dye[k]) for k in ('class', 'pass')), 'Invalid player dye')
        seen.add(dye['itemId'])
        for key in ('color', 'secondaryColor'):
            if key in dye:
                _require(type(dye[key]) is list and len(dye[key]) == 3
                         and all(type(n) in (int, float) and -4 <= n <= 4 and math.isfinite(n) for n in dye[key]), 'Invalid dye color')
        if 'saturation' in dye:
            n = dye['saturation']
            _require(type(n) in (int, float) and 0 <= n <= 4 and math.isfinite(n), 'Invalid dye saturation')
        if 'image' in dye: _require(_text(dye['image'], 128, empty=True, controls=True), 'Invalid dye image name')
    selection = facts['selection']
    _require(_exact(selection, ('clothes', 'hairDyeItems', 'unlockItems', 'negativeBuffs', 'commonBuffThrough', 'commonBuffs', 'maxBuffId')),
             'Incomplete player selection policy')
    _require(_ids(selection['clothes'], 0, 255, 256) and selection['clothes']
             and all(f'clothes:{n}' in choices for n in selection['clothes']), 'Missing selected clothes atlas cells')
    hair_dyes = selection['hairDyeItems']
    _require(_ids(hair_dyes, 0, 65535, 256) and hair_dyes and hair_dyes[0] == 0, 'Invalid hair-dye choices')
    by_id = dict(zip(catalog[0], catalog[5]))
    _require(all(by_id.get(identity) == index for index, identity in enumerate(hair_dyes) if index), 'Hair dye/item binding mismatch')
    unlocks = selection['unlockItems']
    _require(type(unlocks) is list and len(unlocks) == len(UNLOCKS), 'Incomplete unlock policy')
    seen = set()
    for row in unlocks:
        _require(type(row) is list and len(row) == 3 and isinstance(row[0], str) and row[0] in UNLOCKS and row[0] not in seen
                 and _text(row[1]) and _int(row[2], 1) and row[2] in item_ids, 'Invalid unlock policy row')
        seen.add(row[0])
    max_buff = max(map(int, buffs))
    _require(type(selection['maxBuffId']) is int and selection['maxBuffId'] == max_buff
             and _int(selection['commonBuffThrough'], 0, max_buff)
             and all(_ids(selection[k], 1, max_buff, 4096) and all(str(n) in buffs for n in selection[k]) for k in ('negativeBuffs', 'commonBuffs')),
             'Player buff selection mismatch')
    labels = facts['versionLabels']
    _require(type(labels) is dict and 0 < len(labels) <= 289, 'Invalid save version labels')
    for key, value in labels.items():
        _require(isinstance(key, str) and re.fullmatch(r'[1-9][0-9]{1,2}', key) and _int(int(key), 38, 326)
                 and _text(value, 64) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:–[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)?', value),
                 'Invalid save version label')


class _Renderer:
    def __init__(self, textures, checkpoint):
        _require(type(textures) is dict and 0 < len(textures) <= 8192, 'Invalid player texture collection')
        _require(all(isinstance(k, str) and type(v) is bytes and 0 < len(v) <= 32 * 1024 * 1024 for k, v in textures.items()), 'Invalid player texture input')
        _require(sum(map(len, textures.values())) <= 128 * 1024 * 1024, 'Player texture input budget exceeded')
        for name in textures:
            relative_path(name)
            _require(re.fullmatch(r'[A-Za-z0-9_./-]+\.png', name), 'Invalid player texture path')
        self.textures, self.checkpoint, self.used, self.images = textures, checkpoint, {}, OrderedDict()
        self.work = 0

    def spend(self, pixels):
        self.work += pixels
        _require(self.work <= 128 * 1024 * 1024, 'Player rendering work budget exceeded')
        self.checkpoint()

    def close(self):
        for im in self.images.values(): im.close()
        self.images.clear()

    def image(self, name, digest):
        _require(name in self.textures and _hash(digest), 'Unknown player source texture')
        if name not in self.used: self.used[name] = sha256(self.textures[name])
        _require(self.used[name] == digest, 'Player texture source hash mismatch')
        if name not in self.images:
            if len(self.images) >= 2:
                _, old = self.images.popitem(last=False)
                old.close()
            image = _png_pixels(self.textures[name])
            self.spend(image.width * image.height)
            self.images[name] = image
        else: self.images.move_to_end(name)
        return self.images[name]

    def render(self, canvas, layers):
        _require(type(canvas) is list and len(canvas) == 2 and all(_int(n, 1, 512) for n in canvas), 'Invalid player canvas')
        _require(type(layers) is list and len(layers) <= 64, 'Player layer budget exceeded')
        self.spend(canvas[0] * canvas[1])
        result = Image.new('RGBA', tuple(canvas))
        try:
            for layer in layers:
                _require(_exact(layer, ('texture', 'textureSha256', 'source', 'destination', 'tint'))
                         and isinstance(layer['texture'], str), 'Invalid explicit player layer')
                source, dest, tint = layer['source'], layer['destination'], layer['tint']
                _require(type(source) is list and len(source) == 4 and all(_int(n, 0, 16384) for n in source)
                         and source[2] > 0 and source[3] > 0 and source[2] <= 512 and source[3] <= 512,
                         'Invalid player source rectangle')
                _require(type(dest) is list and len(dest) == 2 and all(_int(n, -512, 512) for n in dest)
                         and type(tint) is list and len(tint) == 4 and all(_int(n, 0, 255) for n in tint), 'Invalid player placement/tint')
                image = self.image(layer['texture'], layer['textureSha256'])
                x, y, w, h = source
                _require(x + w <= image.width and y + h <= image.height, 'Player crop outside texture')
                self.spend(w * h)
                piece = image.crop((x, y, x + w, y + h))
                tinted = ImageChops.multiply(piece, Image.new('RGBA', piece.size, tuple(tint)))
                result.alpha_composite(tinted, tuple(dest))
                piece.close(); tinted.close()
            return result
        except BaseException:
            result.close()
            raise


def _pack_frames(renderer, recipe):
    _require(_exact(recipe, ('key', 'canvas', 'frames')) and isinstance(recipe['key'], str)
             and re.fullmatch(r'[a-z][a-z0-9_:]{0,100}', recipe['key'])
             and type(recipe['frames']) is list and len(recipe['frames']) == 14, 'Invalid explicit walking recipe')
    frames = []
    try:
        for layers in recipe['frames']: frames.append(renderer.render(recipe['canvas'], layers))
        bounds = [im.getchannel('A').getbbox() for im in frames]
        nonempty = [box for box in bounds if box]
        _require(nonempty, 'Entirely transparent walking recipe requires an explicit absence policy')
        box = (min(b[0] for b in nonempty), min(b[1] for b in nonempty), max(b[2] for b in nonempty), max(b[3] for b in nonempty))
        distinct, indices = [], []
        for frame in frames:
            cropped = frame.crop(box)
            raw = cropped.tobytes(); cropped.close()
            if raw not in distinct: distinct.append(raw)
            indices.append(distinct.index(raw))
        rgba = b''.join(distinct)
        _require(len(rgba) <= MAX_TEXTURE_BYTES, 'Expanded player texture exceeds consumer bound')
        palette, known = [], {}
        for offset in range(0, len(rgba), 4):
            pixel = rgba[offset:offset + 4]
            if pixel not in known:
                known[pixel] = len(palette); palette.append(pixel)
                if len(palette) > 256: break
        if len(palette) <= 256:
            raw = struct.pack('<H', len(palette)) + b''.join(palette) + bytes(known[rgba[n:n + 4]] for n in range(0, len(rgba), 4))
        else: raw = b'\0\0' + rgba
        packed = zlib.compress(raw, 9)
        _require(zlib.decompress(packed) == raw, 'Player compression roundtrip failed')
        return packed, [box[2] - box[0], box[3] - box[1], box[0], box[1], *recipe['canvas'], indices]
    finally:
        for image in frames: image.close()


def assemble_player_resources(*, policy: dict, textures: dict[str, bytes], item_objects: dict[str, bytes], checkpoint=None):
    """Build presentation, atlas and walk bytes. No inferred source draw semantics."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    _bounded_policy(policy, checkpoint)
    policy_bytes = canonical_json(policy)
    _require(len(policy_bytes) <= 16 * 1024 * 1024, 'Player policy byte budget exceeded')
    _require(_exact(policy, ('schemaVersion', 'algorithm', 'gameVersion', 'sourceHashes', 'itemHashes', 'walk', 'choices', 'repairs', 'facts'))
             and type(policy['schemaVersion']) is int and policy['schemaVersion'] == 1 and policy['algorithm'] == ALGORITHM
             and isinstance(policy['gameVersion'], str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', policy['gameVersion']), 'Invalid player assembly policy')
    sources = policy['sourceHashes']
    _require(type(sources) is dict and 0 < len(sources) <= 64
             and all(isinstance(k, str) and re.fullmatch(r'[a-z][a-z0-9.-]{0,63}', k) and _hash(v) for k, v in sources.items()), 'Missing player source hashes')
    _require(type(item_objects) is dict and set(item_objects) == set(ITEM_ROLES)
             and _exact(policy['itemHashes'], ITEM_ROLES), 'Missing exact item foundation roles')
    _require(all(type(raw) is bytes and 0 < len(raw) <= MAX_OBJECT_BYTES for raw in item_objects.values())
             and sum(map(len, item_objects.values())) <= 64 * 1024 * 1024, 'Item foundation input budget exceeded')
    for role in ITEM_ROLES:
        _require(_hash(policy['itemHashes'][role]) and sha256(item_objects[role]) == policy['itemHashes'][role], 'Player item foundation hash mismatch')
    catalog = _item_foundation(item_objects, policy['gameVersion'])
    walk, choices, repairs = policy['walk'], policy['choices'], policy['repairs']
    _require(type(walk) is list and 0 < len(walk) <= 8192, 'Invalid player walk domain')
    _require(_exact(choices, ('columns', 'rows')) and _int(choices['columns'], 1, 32)
             and type(choices['rows']) is list and 0 < len(choices['rows']) <= 2048, 'Invalid player choice policy')
    width, height = choices['columns'] * 40, math.ceil(len(choices['rows']) / choices['columns']) * 56
    _require(height <= 4096 and (width * 4 + 1) * height <= MAX_TEXTURE_BYTES, 'Player atlas decoded-size bound exceeded')
    choice_keys = {}
    for index, row in enumerate(choices['rows']):
        _require(_exact(row, ('key', 'layers')) and isinstance(row['key'], str)
                 and re.fullmatch(r'(hair|clothes):[0-9]{1,4}', row['key']) and row['key'] not in choice_keys, 'Invalid or duplicate choice key')
        choice_keys[row['key']] = index
    _validate_facts(policy['facts'], choice_keys, catalog)
    _require(_exact(repairs, ('translations', 'textures')) and type(repairs['translations']) is dict
             and type(repairs['textures']) is list and len(repairs['textures']) <= len(walk), 'Invalid player repair policy')
    renderer = _Renderer(textures, checkpoint)
    try:
        packed_parts, seen_blobs, offset, walk_index = [], {}, 0, {}
        for recipe in walk:
            packed, geometry = _pack_frames(renderer, recipe)
            key = recipe['key']
            _require(key not in walk_index, 'Duplicate walking recipe key')
            # Compare exact bytes, not only hashes, for content aliases.
            if packed not in seen_blobs:
                seen_blobs[packed] = offset; packed_parts.append(packed); offset += len(packed)
                _require(offset <= MAX_OBJECT_BYTES, 'Player walk pack exceeds consumer bound')
            walk_index[key] = [seen_blobs[packed], len(packed), *geometry]
        binary = b''.join(packed_parts)
        source = f"explicit-rgba-recipes {policy['gameVersion']} sha256:{sha256(policy_bytes)}"
        translations = repairs['translations']
        _require(len(translations) <= len(walk_index), 'Too many repair translations')
        for key, rows in translations.items():
            _require(key in walk_index and type(rows) is list and len(rows) == 14
                     and all(type(p) is list and len(p) == 2 and all(_int(n, -512, 512) for n in p) for p in rows), 'Invalid player frame translation')
        repaired = {}
        for recipe in repairs['textures']:
            packed, (w, h, x, y, ow, oh, frames) = _pack_frames(renderer, recipe)
            _require(recipe['key'] in walk_index and recipe['key'] not in repaired and h <= 56 and y + h <= 56, 'Invalid repaired walking texture')
            encoded = base64.b64encode(packed).decode('ascii')
            _require(len(encoded) <= 512 * 1024, 'Player repair output bound exceeded')
            repaired[recipe['key']] = {'width': w, 'height': h, 'x': x, 'y': y, 'originalWidth': ow, 'frames': frames, 'data': encoded}
        atlas = Image.new('RGBA', (width, height))
        try:
            for index, row in enumerate(choices['rows']):
                frame = renderer.render([40, 56], row['layers'])
                atlas.paste(frame, (index % choices['columns'] * 40, index // choices['columns'] * 56)); frame.close()
            buffer = BytesIO(); atlas.save(buffer, format='PNG', compress_level=9); atlas_bytes = buffer.getvalue()
        finally: atlas.close()
        _require(len(atlas_bytes) <= MAX_TEXTURE_BYTES, 'Player atlas PNG exceeds consumer bound')
        _require(set(renderer.used) == set(textures), 'Unreferenced player input texture')
        presentation = {'schemaVersion': 2, 'gameVersion': policy['gameVersion'],
                        'items': {'gameVersion': policy['gameVersion'], **policy['itemHashes']},
                        'choices': {'keys': choice_keys, 'columns': choices['columns'], 'width': width, 'height': height},
                        'walkIndex': {'format': 1, 'source': source, 'bytes': len(binary), 'sha256': sha256(binary), 'textures': walk_index},
                        'frameRepairs': {'source': source, 'translations': translations, 'textures': repaired}, **policy['facts']}
        objects = {'player.presentation': canonical_json(presentation), 'player.walk': binary, 'player.atlas': atlas_bytes}
        _require(all(0 < len(raw) <= MAX_OBJECT_BYTES for raw in objects.values())
                 and sum(map(len, objects.values())) <= 64 * 1024 * 1024, 'Player resource group output budget exceeded')
        receipt = {'schemaVersion': 1, 'kind': 'consumer-derivation', 'status': 'DERIVED_ONLY', 'algorithm': ALGORITHM,
                   'policySha256': sha256(policy_bytes), 'sourceHashes': dict(sources), 'itemHashes': dict(policy['itemHashes']),
                   'textures': dict(sorted(renderer.used.items())),
                   'objects': {role: {'sha256': sha256(raw), 'bytes': len(raw)} for role, raw in objects.items()},
                   'sourceSemanticsVerified': False, 'publicationApproved': False,
                   'missingProducerProof': ['equipment/frame/layer mappings and repairs', 'buff/dye/hair/wing final facts',
                                            'selection and version-label app policy', 'complete independently selected texture domain']}
        return objects, receipt
    finally: renderer.close()
