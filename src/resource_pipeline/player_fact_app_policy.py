"""Read bounded literal app policy from operator-supplied, immutable source bytes.

No JavaScript evaluator, module loader, subprocess or historical resource payload
is used. Only three named source blobs are accepted by the public entry point.
These values remain application policy and never become fresh game observations.
"""
from __future__ import annotations

import hashlib
import re

from .player_assembler import UNLOCKS, WING_FIELDS, _ids, _int, _text, _wing
from .security import PipelineError, canonical_json, sha256

APP_SOURCE_COMMIT = 'c6cf0cb381b85d6f934c023ce68cfc206ef7b326'
APP_SOURCE_PINS = {
    'services/selection.mjs': ('38a713e07dd620961c798cd782fd89c21fc79abf',
                               'f2817440e417099119048b6c2a49b39be19f3ce0cc32a5c2f83a67c2986e9fc5'),
    'services/versions.mjs': ('5fc07414848354c8411d62365ec995fd8d6539b0',
                              '1ffc9c85cb990a8bd28c61c8bfa2a751d8d959960d8396830cbfad6a0d46222d'),
    'services/render/terraria-player-draw-rules.mjs': ('30a43f9026252dfd6ef5536ba75e8fdb62ee0877',
                                                      'eca664135d67ada48e55dc76299a60cedd07512b8d4e1c936763dceb928f7b5d'),
}


def _need(ok, message):
    if not ok: raise PipelineError('Player app policy: ' + message)


class _Literal:
    """Small JSON-like literal grammar with JS single quotes/comments/key names.

    Calls, property access, identifiers as values, spread, accessors, computed
    keys, operators and template strings are not values in this grammar.
    """
    def __init__(self, source, offset=0):
        _need(type(source) is str and len(source) <= 256 * 1024, 'literal source bound')
        self.source, self.at, self.nodes = source, offset, 0

    def space(self):
        while self.at < len(self.source):
            if self.source[self.at].isspace(): self.at += 1; continue
            if self.source.startswith('//', self.at):
                end = self.source.find('\n', self.at + 2)
                self.at = len(self.source) if end < 0 else end + 1; continue
            if self.source.startswith('/*', self.at):
                end = self.source.find('*/', self.at + 2)
                _need(end >= 0, 'unterminated comment'); self.at = end + 2; continue
            break

    def token(self, value):
        self.space(); _need(self.source.startswith(value, self.at), 'expected literal delimiter')
        self.at += len(value)

    def string(self):
        quote = self.source[self.at]; self.at += 1; result = []
        while self.at < len(self.source):
            char = self.source[self.at]; self.at += 1
            if char == quote: return ''.join(result)
            _need(ord(char) >= 32 and char not in '\r\n', 'unescaped string control')
            if char == '\\':
                _need(self.at < len(self.source), 'truncated string escape')
                char = self.source[self.at]; self.at += 1
                escapes = {'"': '"', "'": "'", '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}
                if char == 'u':
                    raw = self.source[self.at:self.at + 4]
                    _need(re.fullmatch('[a-fA-F0-9]{4}', raw) is not None, 'invalid Unicode escape')
                    char = chr(int(raw, 16)); self.at += 4
                else:
                    _need(char in escapes, 'unsupported string escape'); char = escapes[char]
            result.append(char); _need(len(result) <= 4096, 'literal string bound')
        raise PipelineError('Player app policy: unterminated string')

    def value(self, depth=0):
        self.nodes += 1; _need(depth <= 12 and self.nodes <= 10000, 'literal structure bound')
        self.space(); _need(self.at < len(self.source), 'missing literal value')
        char = self.source[self.at]
        if char in ('"', "'"): return self.string()
        if char in '[{':
            obj = char == '{'; self.at += 1; result = {} if obj else []; close = '}' if obj else ']'
            self.space()
            if self.source.startswith(close, self.at): self.at += 1; return result
            while True:
                if obj:
                    self.space()
                    _need(self.at < len(self.source), 'missing object key')
                    if self.source[self.at] in ('"', "'"): key = self.string()
                    else:
                        match = re.match(r'(?:[A-Za-z_$][A-Za-z0-9_$]*|0|[1-9][0-9]*)', self.source[self.at:])
                        _need(match is not None, 'unsupported object key'); key = match[0]; self.at += len(key)
                    _need(key not in result and key not in ('__proto__', 'constructor', 'prototype'), 'duplicate/unsafe object key')
                    self.token(':'); result[key] = self.value(depth + 1)
                else: result.append(self.value(depth + 1))
                self.space()
                if self.source.startswith(close, self.at): self.at += 1; return result
                self.token(','); self.space()
                if self.source.startswith(close, self.at): self.at += 1; return result
        for word, value in (('true', True), ('false', False), ('null', None)):
            if self.source.startswith(word, self.at):
                self.at += len(word); return value
        match = re.match(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', self.source[self.at:])
        _need(match is not None and len(match[0]) <= 20, 'unsupported literal expression')
        self.at += len(match[0]); value = float(match[0]) if '.' in match[0] else int(match[0])
        _need(abs(value) <= 2**53 - 1, 'literal number bound'); return value


def _declaration(source, name, wrapper=None):
    # Whole-file cryptographic pins are checked before this extraction in public
    # use. Anchors select named source declarations, not expressions elsewhere.
    matches = list(re.finditer(r'(?m)^[ \t]*(?:export[ \t]+)?const[ \t]+' + re.escape(name) + r'[ \t]*=', source))
    _need(len(matches) == 1, 'missing/ambiguous declaration: ' + name)
    reader = _Literal(source, matches[0].end())
    if wrapper == 'freeze': reader.token('Object.freeze'); reader.token('(')
    elif wrapper == 'set': reader.token('new Set'); reader.token('(')
    else: _need(wrapper is None, 'unknown literal wrapper')
    value = reader.value()
    if wrapper: reader.token(')')
    # No call/operator/property suffix can be ignored after the literal.
    suffix = source[reader.at:]
    boundary = re.match(r'[ \t]*(;|\r?\n|$)', suffix)
    _need(boundary is not None, 'nonliteral declaration suffix')
    if boundary[1] not in (';', ''):
        # A newline alone is not a JavaScript expression terminator: e.g. an
        # operator or property access on the following line would continue it.
        reader.at += boundary.end(); reader.space()
        tail = source[reader.at:]
        _need(not tail or re.match(r'(?:export|const|function|import|for)\b', tail) is not None,
              'unsupported declaration continuation')
    return value


def _extract_policy_sources(sources):
    selection = sources['services/selection.mjs']; wings = sources['services/render/terraria-player-draw-rules.mjs']
    unlocks = _declaration(selection, 'UNLOCKS'); negatives = _declaration(selection, 'negative', 'set')
    common = list(re.finditer(r'known:id<=(\d+)\|\|(\[[0-9, ]*\])\.includes\(id\)', selection))
    _need(len(common) == 1, 'missing/ambiguous common buff policy')
    reader = _Literal(common[0][2]); common_ids = reader.value()
    _need(reader.at == len(reader.source), 'common buff literal suffix')
    through = int(common[0][1])
    default = _declaration(wings, 'DEFAULT_WING_RULE', 'freeze')
    slots = _declaration(wings, 'WING_RULES', 'freeze')
    _need(type(default) is dict and set(default) == WING_FIELDS | {'fidelity'}
          and default.pop('fidelity') == 'source-port' and _wing(default), 'unsupported wing default')
    _need(type(slots) is dict and len(slots) <= 255, 'wing slot bound')
    joined = {}
    for slot, rule in slots.items():
        _need(re.fullmatch(r'[1-9][0-9]{0,2}', slot) is not None and _int(int(slot), 1, 255)
              and type(rule) is dict and set(rule) <= WING_FIELDS, 'invalid wing override')
        joined[slot] = {**default, **rule}
        _need(_wing(joined[slot]), 'invalid merged wing rule')
    _need(type(unlocks) is list and len(unlocks) == len(UNLOCKS), 'unlock count mismatch')
    minimums = {}; rows = []
    for row in unlocks:
        _need(type(row) is list and len(row) == 4 and type(row[0]) is str and row[0] in UNLOCKS
              and row[0] not in minimums and _text(row[1]) and _int(row[2], 1) and _int(row[3], 38, 326), 'invalid unlock literal')
        minimums[row[0]] = row[3]; rows.append(row[:3])
    _need(_ids(negatives, 1, 65535, 4096) and _ids(common_ids, 1, 65535, 4096)
          and _int(through, 0, 65535), 'invalid buff classification policy')
    labels = _declaration(sources['services/versions.mjs'], 'VERSION_LABELS')
    _need(type(labels) is dict and 0 < len(labels) <= 289, 'historical label bound')
    for version, label in labels.items():
        _need(re.fullmatch(r'[1-9][0-9]{1,2}', version) is not None and _int(int(version), 38, 326)
              and _text(label, 64) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:–[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)?', label), 'invalid version label')
    return {'selection': {'unlockItems': rows, 'negativeBuffs': negatives, 'commonBuffThrough': through, 'commonBuffs': common_ids},
            'wingRules': {'default': default, 'slots': joined}, 'versionLabels': labels}, minimums


def extract_player_app_policy(source_bytes):
    """The caller supplies exactly the three pinned source files, never payloads."""
    _need(type(source_bytes) is dict and set(source_bytes) == set(APP_SOURCE_PINS), 'expected three pinned app sources')
    sources, evidence = {}, []
    for path, (git_pin, digest) in APP_SOURCE_PINS.items():
        raw = source_bytes[path]
        _need(type(raw) is bytes and 0 < len(raw) <= 256 * 1024, 'source byte bound')
        git_sha = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
        _need(git_sha == git_pin and sha256(raw) == digest, 'immutable app source mismatch: ' + path)
        try: sources[path] = raw.decode('utf-8')
        except UnicodeError as exc: raise PipelineError('Invalid app policy source UTF-8') from exc
        evidence.append({'path': path, 'gitBlobSha1': git_sha, 'sha256': digest, 'bytes': len(raw)})
    result, minimums = _extract_policy_sources(sources)
    return result, {'schemaVersion': 1, 'status': 'PINNED_APPLICATION_POLICY', 'repository': 'Live-yan/viewer-app', 'sourceCommit': APP_SOURCE_COMMIT,
                    'sources': evidence, 'resultSha256': sha256(canonical_json(result)),
                    'unlockMinimumVersions': minimums, 'executedInput': False, 'freshGameObservation': False,
                    'sourceSemanticsVerified': False, 'complete': False, 'publishable': False,
                    'scope': ['wing preview policy', 'curated buff classification', 'unlock captions and compatibility', 'historical release labels']}
