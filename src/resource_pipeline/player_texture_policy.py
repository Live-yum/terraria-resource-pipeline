"""Existing app naming/walk policy lowered onto fresh, bounded PNG inputs.

Never reads a legacy atlas or infers game-source completeness from filenames.
"""
import math
import re
from .security import PipelineError, relative_path, canonical_json, sha256
from .player_assembler import _Renderer

NAMING_REFERENCE = {'repositoryId': 1358343481, 'repository': 'Live-yum/PlayerWebsite',
                    'commit': '9e09f4e3aef37befc267aa51c190bea472726c8b',
                    'path': 'src/terraria-player-renderer-core.mjs'}
WALK_REFERENCE = {'repository': 'Live-yan/viewer-app', 'commit': '638e770db844174ff990e42e40a7e96290ff6968',
                  'path': 'scripts/generate-player-walk.py',
                  'sha256': '8c1e3378387bc632fd7d915242337c93665c077706e9790ca06b7b673a910416'}
SELECTED = re.compile(r'(?:player_[0-9]+_[0-9]+|player_hair(?:alt)?_[0-9]+|armor_(?:head|body|legs|arm|composite)_[0-9]+|female_body_[0-9]+|wings_[0-9]+|acc_(?:handson|handsoff|back|front|shoes|waist|shield|neck|face|balloon|beard)_[0-9]+|accessories_acc_(?:handson|handsoff)_[0-9]+)')
WING_COUNTS = {22: 7, 34: 6, 39: 6, 40: 14, 43: 7, 44: 7, 45: 6, 47: 11, 48: 8, 49: 11, 50: 11, 51: 8}


def _need(condition, message):
    if not condition: raise PipelineError(message)


def map_player_texture_paths(paths):
    """Map root-relative decoded Images paths, rejecting any ambiguous key.

    Valid non-player PNGs are explicitly excluded by existing app selection
    policy. Invalid paths/extensions and collisions are never silently skipped.
    """
    _need(type(paths) is list and len(paths) <= 32768, 'Invalid texture path inventory')
    seen, mapping, excluded = set(), {}, []
    for path in paths:
        _need(type(path) is str and re.fullmatch(r'[A-Za-z0-9_./-]+\.[Pp][Nn][Gg]', path), 'Unsupported decoded texture path')
        relative_path(path)
        _need(path not in seen, 'Duplicate texture path'); seen.add(path)
        normalized = re.sub(r'\.xnb\.png$', '.png', '/' + path, flags=re.I).lower()
        base = normalized.rsplit('/', 1)[-1][:-4]
        if re.search(r'/accessories/acc_hands(?:on|off)_[0-9]+\.png$', normalized): key = 'accessories_' + base
        elif re.search(r'/armor/armor_[0-9]+\.png$', normalized): key = 'armor_composite_' + base[6:]
        else: key = base
        if not SELECTED.fullmatch(key): excluded.append(path); continue
        _need(all(part == str(int(part)) for part in key.split('_') if part.isdigit()),
              'Noncanonical numeric player texture identity')
        _need(key not in mapping, 'Canonical player texture collision: ' + key)
        mapping[key] = path
    return dict(sorted(mapping.items())), sorted(excluded)


def _cells(piece, frame, female, armor):
    row = 2 if female else 0
    x = 3 if frame in (6, 11, 12, 13, 18, 19) else 4 if frame <= 10 else 5 if frame in (14, 17) else 6
    torso, back, arm = (0, row), (x, 3), (x, 1)
    if armor: return [back, (1, 1 + row), torso, (0, 1 + row), arm]
    if piece == 3: return [torso]
    if piece in (4, 6): return [(1, 1 + row), torso, (0, 1 + row)]
    if piece in (5, 7, 8, 9, 13): return [back, arm]
    return []


def build_player_walk_recipes(textures, *, checkpoint=None):
    """Produce explicit walking recipes plus fresh transparent-absence evidence.

    This does not produce player facts, choices, repairs, or a complete player
    bundle. Caller retains the converter's original-source provenance separately.
    """
    checkpoint = checkpoint or (lambda: None)
    _need(type(textures) is dict, 'Invalid fresh texture input')
    mapping, excluded = map_player_texture_paths(list(textures))
    selected = {path: textures[path] for path in mapping.values()}
    _need(selected, 'No selected player textures')
    renderer = _Renderer(selected, checkpoint)
    recipes, omissions, used = [], [], set()
    try:
        for key, path in mapping.items():
            checkpoint(); digest = sha256(selected[path]); image = renderer.image(path, digest)
            w, h = image.size
            player = re.fullmatch(r'player_([0-9]+)_([0-9]+)', key)
            if player and int(player[2]) > 13:
                omissions.append({'key': key, 'textureSha256': digest, 'reason': 'app-excludes-player-piece-above-13'}); continue
            for female in ([False, True] if key.startswith('armor_composite_') else [False]):
                frames, canvas = [], None
                for k in range(14):
                    f, fw, fh, requests = k + 6, w, min(h, 56), []
                    if w >= 360 and h >= 224:
                        piece = int(player[2]) if player else 9
                        sex = female or bool(player and int(player[1]) in (4, 5, 6, 7, 9, 11))
                        cells = _cells(piece, f, sex, key.startswith('armor_composite_'))
                        if key.startswith('accessories_acc_handson_'): cells = cells[-1:]
                        if key.startswith('accessories_acc_handsoff_'): cells = cells[:1]
                        fw, fh = 40, 56
                        requests = [(cx * 40, cy * 56, 40, 56, 0, 0) for cx, cy in cells]
                    else:
                        y = f * 56 if h >= 1120 else 0
                        if key.startswith('player_hair'): y, fh = k * 56, 54
                        if key.startswith('wings_'):
                            slot = int(key.split('_')[1]); count = WING_COUNTS.get(slot, 4); fh = h // count; y = 0
                            if slot in (22, 28, 34, 39, 45, 48): y = k % count * fh
                        if key.startswith('acc_balloon_') and int(key.rsplit('_', 1)[1]) not in (5, 10, 12, 17): fh = h // 4; y = (k // 3) % 4 * fh
                        requests = [(0, y, fw, min(h, y + fh) - y, 0, 0)]
                        if key == 'wings_40':
                            requests = []
                            for frame in range(8, 14):
                                phase = (frame - 8) % 3; angle = (k * .07 * (2 + phase) + phase * .5) * math.pi * 2
                                dx, dy = round(3 - math.sin(angle) * .5 * (phase + 1)), round(math.cos(angle) * .5 * (phase + 1))
                                requests.append((0, frame * fh, fw - 2, fh - 2, dx, dy))
                    _need(0 < fw <= 512 and 0 < fh <= 512, 'Unsupported player frame geometry')
                    if canvas is None: canvas = [fw, fh]
                    _need(canvas == [fw, fh], 'Player frame canvas changes')
                    layers = []
                    for x, y, cw, ch, dx, dy in requests:
                        _need(cw >= 0 and ch >= 0, 'Source sheet does not contain requested frames')
                        left, top, right, bottom = max(0, x), max(0, y), min(w, x + cw), min(h, y + ch)
                        if left >= right or top >= bottom: continue
                        layers.append({'texture': path, 'textureSha256': digest,
                                       'source': [left, top, right - left, bottom - top],
                                       'destination': [dx + left - x, dy + top - y], 'tint': [255] * 4,
                                       **({'composition': 'copy'} if not (w >= 360 and h >= 224) and key != 'wings_40' else {})})
                    frames.append(layers)
                recipe = {'key': key + (':female' if female else ''), 'canvas': canvas, 'frames': frames}
                nonempty = False
                for layers in frames:
                    rendered = renderer.render(canvas, layers)
                    try: nonempty |= rendered.getchannel('A').getbbox() is not None
                    finally: rendered.close()
                if nonempty: recipes.append(recipe); used.add(path)
                else: omissions.append({'key': recipe['key'], 'textureSha256': digest, 'reason': 'fresh-render-all-14-frames-transparent'})
        receipt = {'schemaVersion': 1, 'status': 'APP_POLICY_DERIVED', 'namingReference': NAMING_REFERENCE,
                   'walkReference': WALK_REFERENCE, 'selectedPathMapSha256': sha256(canonical_json(mapping)),
                   'sourceTextures': dict(renderer.used), 'excludedNonPlayerPaths': excluded, 'omissions': omissions,
                   'complete': False, 'publishable': False, 'sourceDomainVerified': False}
        return recipes, {p: textures[p] for p in sorted(used)}, receipt
    finally: renderer.close()

PREVIEW_REFERENCE = {'repository': 'Live-yan/viewer-app', 'commit': WALK_REFERENCE['commit'],
                     'path': 'scripts/generate-player-preview.py',
                     'sha256': 'e3c7928400e89c0b01c14bc472c3c048e19a4c73a811c346cec2aa594f24ab87'}
FALLBACKS = ((0,), (1, 0), (2, 0), (3, 0), (4, 0), (5, 4, 0), (6, 4, 0), (7, 4, 0),
             (8, 0), (9, 4, 0), (10, 0), (11, 10, 0))
COLORS = {'skin': (240, 182, 144), 'hair': (106, 70, 48), 'eye': (75, 100, 135),
          'pants': (72, 88, 127), 'shoe': (97, 70, 56), 'shirt': (116, 166, 169), 'under': (232, 225, 208)}
PIECES = ((10, 'skin'), (11, 'pants'), (12, 'shoe'), (3, 'skin'), (4, 'under'), (6, 'shirt'),
          (7, 'skin'), (8, 'under'), (13, 'shirt'), (5, 'skin'), (9, 'skin'), (0, 'skin'), (1, None), (2, 'eye'))


def _clip_layer(path, digest, size, rectangle, destination=(0, 0), tint=(255, 255, 255, 255), *, composition='source-over'):
    x, y, width, height = rectangle; w, h = size
    left, top, right, bottom = max(0, x), max(0, y), min(w, x + width), min(h, y + height)
    if left >= right or top >= bottom: return []
    return [{'texture': path, 'textureSha256': digest, 'source': [left, top, right - left, bottom - top],
             'destination': [destination[0] + left - x, destination[1] + top - y], 'tint': list(tint),
             **({'composition': composition} if composition != 'source-over' else {})}]


def build_player_choice_recipes(textures, *, checkpoint=None):
    """Lower app standing previews, preserving composite-then-tint rounding.

    Intermediate PNGs are explicitly derived from fresh source rectangles.
    Tinting each component before composing would not be pixel-equivalent.
    """
    from io import BytesIO
    checkpoint = checkpoint or (lambda: None)
    _need(type(textures) is dict, 'Invalid fresh texture input')
    mapping, excluded = map_player_texture_paths(list(textures))
    renderer = _Renderer({p: textures[p] for p in mapping.values()}, checkpoint)
    derived, frames, witnesses, missing = {}, {}, {}, []
    try:
        selections = []
        for variant in range(12):
            for piece in range(14):
                key = next((f'player_{v}_{piece}' for v in FALLBACKS[variant] if f'player_{v}_{piece}' in mapping), None)
                target = f'player_{variant}_{piece}'
                if key is None: missing.append(target); continue
                path = mapping[key]; digest = sha256(textures[path]); image = renderer.image(path, digest)
                row = 2 if variant in (4, 5, 6, 7, 9, 11) else 0
                cells = [(0, 0)]
                if image.width >= 360 and image.height >= 224:
                    cells = [(0, row)] if piece == 3 else [(1, 1 + row), (0, row), (0, 1 + row)] if piece in (4, 6) else [(2, 2), (2, 0)]
                layers = [layer for x, y in cells for layer in _clip_layer(path, digest, image.size, (x * 40, y * 56, 40, 56))]
                selections.append((target, layers))
        for key, path in mapping.items():
            if not re.fullmatch(r'player_hair_[0-9]+', key): continue
            _need(int(key.rsplit('_', 1)[1]) > 0, 'Hair texture number must be positive')
            digest = sha256(textures[path]); image = renderer.image(path, digest)
            layers = _clip_layer(path, digest, image.size, ((image.width - 40) // 2, 0, 40, 56), composition='copy')
            selections.append((key, layers))
        for key, layers in selections:
            checkpoint(); image = renderer.render([40, 56], layers)
            try:
                stream = BytesIO(); image.save(stream, format='PNG'); raw = stream.getvalue()
            finally: image.close()
            name = 'derived-player-preview/' + key + '.png'
            derived[name] = raw; frames[key] = name
            witnesses[name] = {'sha256': sha256(raw), 'layers': layers}
        rows = []
        hair_ids = sorted(int(k.rsplit('_', 1)[1]) - 1 for k in frames if k.startswith('player_hair_'))
        for label, variant, hair in [(f'hair:{h}', 0, h) for h in hair_ids] + [(f'clothes:{v}', v, 0) for v in range(12)]:
            layers = []
            for key, color in [(f'player_{variant}_{piece}', color) for piece, color in PIECES] + [(f'player_hair_{hair + 1}', 'hair')]:
                if key not in frames: continue
                name = frames[key]; tint = (*COLORS[color], 255) if color else (255, 255, 255, 255)
                layers.extend(_clip_layer(name, sha256(derived[name]), (40, 56), (0, 0, 40, 56), tint=tint))
            rows.append({'key': label, 'layers': layers})
        used = {layer['texture'] for row in rows for layer in row['layers']}
        return {'columns': 16, 'rows': rows}, {name: derived[name] for name in sorted(used)}, {
            'schemaVersion': 1, 'status': 'APP_POLICY_DERIVED', 'reference': PREVIEW_REFERENCE,
            'namingReference': NAMING_REFERENCE, 'sourceTextures': dict(renderer.used),
            'derivedTextures': {name: witnesses[name] for name in sorted(used)},
            'missingStandingPieces': missing, 'excludedNonPlayerPaths': excluded,
            'complete': False, 'publishable': False, 'sourceDomainVerified': False}
    finally: renderer.close()

REPAIR_REFERENCE = {'repository': 'Live-yan/viewer-app', 'commit': WALK_REFERENCE['commit'],
                    'path': 'scripts/generate-player-polish-assets.py',
                    'sha256': '58f3de6fdb7db9b29939a9dcce8713e7835089a02436c5855d1a8a06a204046f'}


def build_player_frame_repairs(textures, walk_keys, *, checkpoint=None):
    """Derive short-sheet repair decisions from exact fresh frame pixels."""
    checkpoint = checkpoint or (lambda: None)
    _need(type(textures) is dict and type(walk_keys) is list
          and all(type(key) is str for key in walk_keys) and len(walk_keys) == len(set(walk_keys))
          and len(walk_keys) <= 16384, 'Invalid repair input domain')
    mapping, excluded = map_player_texture_paths(list(textures))
    allowed = set(walk_keys)
    _need(all(key in mapping or (key.startswith('armor_composite_') and key.endswith(':female')
                                  and key.removesuffix(':female') in mapping) for key in allowed),
          'Unknown repair walking target')
    renderer = _Renderer({p: textures[p] for p in mapping.values()}, checkpoint)
    translations, recipes, omissions, used = {}, [], [], set()
    try:
        for key, path in mapping.items():
            checkpoint(); digest = sha256(textures[path]); image = renderer.image(path, digest)
            w, h = image.size
            if not 1064 <= h < 1120 or key.startswith('player_hair'): continue
            if key not in allowed:
                omissions.append({'key': key, 'reason': 'not-in-selected-walk-domain', 'textureSha256': digest}); continue
            canvas = [w, 56]
            frame_layers = [_clip_layer(path, digest, image.size, (0, f * 56, w, 56), composition='copy') for f in range(6, 20)]
            base = renderer.render(canvas, _clip_layer(path, digest, image.size, (0, 0, w, 56), composition='copy'))
            frames = []
            try:
                for layers in frame_layers: frames.append(renderer.render(canvas, layers))
                bounds = base.getchannel('A').getbbox(); shifts = []
                for frame in frames:
                    box = frame.getchannel('A').getbbox()
                    if bounds is None or box is None: break
                    original, current = base.crop(bounds), frame.crop(box)
                    try:
                        if current.size != original.size or current.tobytes() != original.tobytes(): break
                    finally: original.close(); current.close()
                    shifts.append([box[0] - bounds[0], box[1] - bounds[1]])
                if len(shifts) == 14: translations[key] = shifts; used.add(path)
                elif any(frame.getchannel('A').getbbox() is not None for frame in frames):
                    recipes.append({'key': key, 'canvas': canvas, 'frames': frame_layers}); used.add(path)
                else: omissions.append({'key': key, 'reason': 'fresh-repair-frames-transparent', 'textureSha256': digest})
            finally:
                base.close()
                for frame in frames: frame.close()
        return {'translations': translations, 'textures': recipes}, {p: textures[p] for p in sorted(used)}, {
            'schemaVersion': 1, 'status': 'APP_POLICY_DERIVED', 'reference': REPAIR_REFERENCE,
            'sourceTextures': dict(renderer.used), 'omissions': omissions, 'excludedNonPlayerPaths': excluded,
            'complete': False, 'publishable': False, 'sourceDomainVerified': False}
    finally: renderer.close()


def assemble_player_from_fresh_textures(*, textures, facts, item_objects, source_hashes, checkpoint=None):
    """Assemble the pinned 1.4.5.8 presentation from fresh PNGs and explicit facts.

    Input facts are still mandatory; no dye/hair/buff/wing/selection/save-version
    facts are invented from image names. Receipts never authorize publication.
    """
    from .player_assembler import ALGORITHM, ITEM_ROLES, MAX_OBJECT_BYTES, assemble_player_resources
    _need(type(source_hashes) is dict and 0 < len(source_hashes) <= 61
          and all(type(k) is str and re.fullmatch(r'[a-z][a-z0-9.-]{0,63}', k)
                  and type(v) is str and re.fullmatch(r'[a-f0-9]{64}', v) for k, v in source_hashes.items()),
          'Fresh fact provenance hashes are required')
    _need(type(item_objects) is dict and set(item_objects) == set(ITEM_ROLES)
          and all(type(raw) is bytes and 0 < len(raw) <= MAX_OBJECT_BYTES for raw in item_objects.values())
          and sum(map(len, item_objects.values())) <= 64 * 1024 * 1024, 'Invalid item foundation objects')
    walks, walk_textures, walk_receipt = build_player_walk_recipes(textures, checkpoint=checkpoint)
    choices, choice_textures, choice_receipt = build_player_choice_recipes(textures, checkpoint=checkpoint)
    repairs, repair_textures, repair_receipt = build_player_frame_repairs(textures, [row['key'] for row in walks], checkpoint=checkpoint)
    inputs = {}
    for group in (walk_textures, choice_textures, repair_textures):
        for name, raw in group.items():
            _need(name not in inputs or inputs[name] == raw, 'Derived player texture path collision')
            inputs[name] = raw
    receipts = {'player-walk-policy': walk_receipt, 'player-choice-policy': choice_receipt, 'player-repair-policy': repair_receipt}
    _need(not set(source_hashes).intersection(receipts), 'Reserved player policy provenance key')
    policy = {'schemaVersion': 1, 'algorithm': ALGORITHM, 'gameVersion': '1.4.5.8',
              'sourceHashes': {**source_hashes, **{key: sha256(canonical_json(value)) for key, value in receipts.items()}},
              'itemHashes': {key: sha256(raw) for key, raw in item_objects.items()},
              'walk': walks, 'choices': choices, 'repairs': repairs, 'facts': facts}
    objects, receipt = assemble_player_resources(policy=policy, textures=inputs, item_objects=item_objects, checkpoint=checkpoint)
    return objects, {**receipt, 'texturePolicyReceipts': receipts, 'runtimeFactsVerified': False,
                     'sourceDomainVerified': False, 'complete': False, 'publishable': False}
