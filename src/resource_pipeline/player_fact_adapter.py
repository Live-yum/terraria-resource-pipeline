"""Join bounded observations, inspected PE shapes, and pinned app policy.

This module does not run a collector, authenticate an observation, or publish a
resource. It cannot turn its input receipts into source/initialization approval.
The optional, unused dye image is explicitly omitted in dedicated-server mode.
"""
from __future__ import annotations

import copy
import math
import re

from .player_assembler import (ITEM_ROLES, _bounded_policy, _exact, _int, _text,
                               _item_foundation, _json, _validate_facts)
from .player_fact_app_policy import extract_player_app_policy
from .player_fact_source_shapes import SOURCE_SHA256, extract_player_static_facts
from .security import PipelineError, canonical_json, sha256

FACE_SETS = {
    'PreventHairDraw': 'facePreventHairDraw',
    'OverrideHelmet': 'faceOverrideHelmet',
    'DrawInFaceUnderHairLayer': 'faceUnderHairLayer',
    'DrawInFaceMaskLayer': 'faceMaskLayer',
    'DrawInFaceFlowerLayer': 'faceFlowerLayer',
    'DrawInFaceHeadLayer': 'faceHeadLayer',
}
DYE_CLASSES = frozenset(('Terraria.Graphics.Shaders.ArmorShaderData',
    'Terraria.GameContent.Dyes.ReflectiveArmorShaderData', 'Terraria.GameContent.Dyes.TeamArmorShaderData',
    'Terraria.GameContent.Dyes.TwilightDyeShaderData'))
OBSERVATION_FIELDS = frozenset((
    'schemaVersion', 'gameVersion', 'sourceSha256', 'culture', 'dedServ', 'buffCount', 'buffs',
    'dyes', 'faceCount', 'faceSets', 'hairShaderCount', 'hairDyeBindings', 'mainDebuff',
    'omittedFields', 'initializationVerified', 'complete', 'publishable'))


def _need(ok, message):
    if not ok: raise PipelineError('Player fact adapter: ' + message)


def _bool_array(values, count, label):
    _need(type(values) is list and len(values) == count and all(type(v) is bool for v in values),
          'missing/malformed full boolean array: ' + label)
    return [n for n, value in enumerate(values) if value]


def _join_player_facts(observation, static, app_policy, item_objects, choices, checkpoint=lambda: None):
    """Internal join, separately fixture-tested; public API derives static/policy."""
    _bounded_policy(observation, checkpoint)
    _need(_exact(observation, OBSERVATION_FIELDS) and type(observation['schemaVersion']) is int
          and observation['schemaVersion'] == 1, 'unexpected observation schema')
    _need(observation['sourceSha256'] == SOURCE_SHA256 and observation['culture'] == 'zh-Hans'
          and observation['gameVersion'] == static['currentVersion']['gameVersion']
          and observation['dedServ'] is True, 'source/version/culture/mode mismatch')
    _need(all(observation[key] is False for key in ('initializationVerified', 'complete', 'publishable')),
          'observation cannot declare acceptance or completeness')
    _need(observation['omittedFields'] == ['dyes.image'], 'dedicated-server image omission must be explicit')
    _need(type(item_objects) is dict and set(item_objects) == set(ITEM_ROLES)
          and all(type(value) is bytes and 0 < len(value) <= 32 * 1024 * 1024 for value in item_objects.values()),
          'three bounded item foundation objects required')
    catalog = _item_foundation(item_objects, observation['gameVersion'])
    rules = _json(item_objects['items.rules'])
    _need(set(rules['items']) == {str(n) for n in catalog[0]}, 'incomplete item gameplay foundation')
    dye_column = rules['fields'].index('dye')
    expected_dyes = {}
    for identity, row in rules['items'].items():
        value = row[dye_column]
        _need(_int(value, 0, 4095), 'item dye binding outside bounded integer domain')
        if value > 0: expected_dyes[int(identity)] = value
    _need(type(choices) is dict and 0 < len(choices) <= 8192
          and all(type(k) is str and re.fullmatch(r'[a-z][a-z0-9_:]{0,100}', k) for k in choices),
          'bounded choice keys required')
    buff_count = observation['buffCount']
    _need(_int(buff_count, 2, 4096) and buff_count == static['buffCount']
          and type(observation['buffs']) is list and len(observation['buffs']) == buff_count - 1,
          'buff Count/domain mismatch')
    buffs = {}
    for row in observation['buffs']:
        checkpoint()
        _need(_exact(row, ('id', 'name', 'description')) and _int(row['id'], 1, buff_count - 1)
              and str(row['id']) not in buffs and _text(row['name'], 4096, controls=True)
              and _text(row['description'], 4096, empty=True, controls=True), 'invalid Lang getter observation')
        buffs[str(row['id'])] = [row['name'], row['description']]
    _need(set(buffs) == {str(n) for n in range(1, buff_count)}, 'incomplete Lang getter domain')
    observed_dyes = observation['dyes']
    _need(type(observed_dyes) is list and 0 < len(observed_dyes) <= 4096
          and len(observed_dyes) == len(expected_dyes), 'incomplete observed armor dye domain')
    dyes, seen = [], set()
    for row in observed_dyes:
        checkpoint()
        _need(_exact(row, ('itemId', 'shaderId', 'class', 'pass', 'color', 'secondaryColor', 'saturation'))
              and _int(row['itemId'], 1) and row['itemId'] not in seen
              and _int(row['shaderId'], 1, 4095) and expected_dyes.get(row['itemId']) == row['shaderId']
              and type(row['class']) is str and row['class'] in DYE_CLASSES
              and type(row['pass']) is str and re.fullmatch(r'[A-Za-z]{1,64}', row['pass']), 'invalid registry-bound armor dye')
        for key in ('color', 'secondaryColor'):
            _need(type(row[key]) is list and len(row[key]) == 3
                  and all(type(n) in (int, float) and math.isfinite(n) and -4 <= n <= 4 for n in row[key]),
                  'missing/nonfinite observed Vector3 field')
        saturation = row['saturation']
        _need(type(saturation) in (int, float) and math.isfinite(saturation) and 0 <= saturation <= 4,
              'missing/nonfinite observed saturation')
        seen.add(row['itemId'])
        dyes.append({key: copy.deepcopy(value) for key, value in row.items() if key not in ('shaderId', 'class')}
                    | {'class': row['class'].rsplit('.', 1)[1]})
    dyes.sort(key=lambda row: row['itemId'])
    face_count = observation['faceCount']
    _need(_int(face_count, 1, 4096) and face_count == static['faceCount']
          and _exact(observation['faceSets'], FACE_SETS), 'Face Count/registry domain mismatch')
    hair = copy.deepcopy(static['hairRules'])
    for source, output in FACE_SETS.items():
        hair[output] = _bool_array(observation['faceSets'][source], face_count, source)
    expected_hair = {identity: binding for identity, binding in zip(catalog[0], catalog[5]) if binding > 0}
    hair_count, bindings = observation['hairShaderCount'], observation['hairDyeBindings']
    _need(_int(hair_count, 1, 255) and type(bindings) is list and len(bindings) == hair_count
          and len(expected_hair) == hair_count, 'incomplete observed hair dye registry')
    hair_by_shader, hair_items = {}, set()
    for row in bindings:
        _need(_exact(row, ('itemId', 'shaderId')) and _int(row['itemId'], 1)
              and _int(row['shaderId'], 1, hair_count) and row['itemId'] not in hair_items
              and row['shaderId'] not in hair_by_shader and expected_hair.get(row['itemId']) == row['shaderId'],
              'duplicate/gapped/mismatched hair dye binding')
        hair_by_shader[row['shaderId']] = row['itemId']; hair_items.add(row['itemId'])
    _need(set(hair_by_shader) == set(range(1, hair_count + 1)), 'noncontiguous hair dye shader IDs')
    main_debuff = _bool_array(observation['mainDebuff'], buff_count, 'Main.debuff')
    # Zero is the app's no-dye sentinel. It is not an invented game binding.
    selection = copy.deepcopy(app_policy['selection']) | {
        'clothes': copy.deepcopy(static['clothes']), 'hairDyeItems': [0] + [hair_by_shader[n] for n in range(1, hair_count + 1)],
        'maxBuffId': max(map(int, buffs))}
    labels = copy.deepcopy(app_policy['versionLabels'])
    current = static['currentVersion']; version_key = str(current['saveVersion'])
    _need(labels.get(version_key) == current['gameVersion'], 'current Constant/app release label mismatch')
    # The current row is now sourced from the inspected game Constant values;
    # historical rows retain their separately recorded app-policy provenance.
    labels[version_key] = current['gameVersion']
    facts = {'buffs': buffs, 'dyes': dyes, 'hairRules': hair, 'wingRules': copy.deepcopy(app_policy['wingRules']),
             'selection': selection, 'versionLabels': labels}
    _validate_facts(facts, choices, catalog)
    evidence = {'itemHashes': {role: sha256(item_objects[role]) for role in ITEM_ROLES},
                'mainDebuffSha256': sha256(canonical_json(observation['mainDebuff'])),
                'mainDebuffTrueCount': len(main_debuff), 'negativeBuffPolicyCount': len(selection['negativeBuffs']),
                'negativeBuffPolicyEqualsMainDebuff': sorted(selection['negativeBuffs']) == main_debuff,
                'currentVersionKey': version_key, 'buffRows': len(buffs), 'dyeRows': len(dyes), 'hairDyeRows': hair_count}
    return facts, evidence


def adapt_observed_player_facts(observation, *, pe_bytes, app_sources, item_objects, choices, checkpoint=lambda: None):
    """Return existing assembler facts and an explicitly unaccepted join receipt.

    observation is the collector's nested playerObservation fragment. app_sources
    maps the three APP_SOURCE_PINS paths to operator-provided bytes. item_objects
    is the freshly assembled three-role item foundation. choices is the actual
    choice-key mapping that the later player assembler will consume. Exact hashes
    bind those inputs but cannot authenticate the caller's freshness assertion.
    """
    checkpoint()
    static, source_receipt = extract_player_static_facts(pe_bytes, checkpoint)
    app_policy, policy_receipt = extract_player_app_policy(app_sources)
    facts, evidence = _join_player_facts(observation, static, app_policy, item_objects, choices, checkpoint)
    receipt = {'schemaVersion': 1, 'status': 'PLAYER_FACTS_ADAPTED',
               'sourceSha256': SOURCE_SHA256, 'observationSha256': sha256(canonical_json(observation)),
               'factsSha256': sha256(canonical_json(facts)), 'staticSource': source_receipt,
               'applicationPolicy': policy_receipt, **evidence,
               'omittedFields': [{'field': 'dyes.image', 'reason': 'dedServ UseImage does not retain asset; current CPU consumer does not read image'}],
               'sourceKinds': {'buffs': 'observed Lang getters', 'dyes': 'observed armor registry parameters',
                    'hairRules.headAndBack': 'bounded PE source shapes', 'hairRules.face': 'observed bool registries',
                    'selection.clothes': 'bounded PE constructor prefix and FieldRVA',
                    'selection.hairDyeItems': 'observed registry IDs cross-bound to item foundation; app zero sentinel',
                    'selection.classificationAndUnlocks': 'pinned application source policy',
                    'wingRules': 'pinned application renderer policy',
                    'versionLabels.current': 'PE Constant rows', 'versionLabels.historical': 'pinned application compatibility policy'},
               'executedInput': False, 'observationAuthenticated': False, 'runtimeInitializationVerified': False,
               'sourceSemanticsVerified': False, 'complete': False, 'publishable': False}
    checkpoint()
    return facts, receipt
