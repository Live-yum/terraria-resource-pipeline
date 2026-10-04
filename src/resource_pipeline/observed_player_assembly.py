"""Join fresh player observation, source-only policy and fresh PNGs.

This adapter performs no game execution and grants no publication authority.
"""
from .player_fact_adapter import adapt_observed_player_facts
from .player_texture_policy import build_player_choice_recipes, assemble_player_from_fresh_textures
from .security import PipelineError, canonical_json, sha256


def assemble_observed_player_resources(*, observation, pe_bytes, app_sources,
                                       textures, item_objects, checkpoint=lambda: None):
    """Produce three private derived roles from one explicit observation fragment.

    Choice keys are derived from the same fresh PNG mapping used by the final
    assembler, rather than accepted as an independently supplied domain. The
    downstream assembler revalidates all facts and exact foundation bytes.
    """
    if (type(item_objects) is not dict or type(textures) is not dict
            or not 0 < len(item_objects) <= 3 or not 0 < len(textures) <= 8192
            or any(type(k) is not str or type(v) is not bytes for source in (item_objects, textures) for k, v in source.items())):
        raise PipelineError('Fresh player assembly requires bounded plain immutable byte mappings')
    if (sum(map(len, item_objects.values())) > 64 * 1024 * 1024
            or sum(map(len, textures.values())) > 128 * 1024 * 1024):
        raise PipelineError('Fresh player assembly byte limit exceeded')
    # Detach from caller-owned mappings before invoking checkpoints or adapters.
    item_objects, textures = dict(item_objects), dict(textures)
    choices, _, choice_receipt = build_player_choice_recipes(textures, checkpoint=checkpoint)
    keys = {row['key']: {} for row in choices['rows']}
    facts, facts_receipt = adapt_observed_player_facts(
        observation, pe_bytes=pe_bytes, app_sources=app_sources,
        item_objects=item_objects, choices=keys, checkpoint=checkpoint)
    objects, assembly_receipt = assemble_player_from_fresh_textures(
        textures=textures, facts=facts, item_objects=item_objects,
        source_hashes={'player-fact-adaptation': sha256(canonical_json(facts_receipt))},
        checkpoint=checkpoint)
    final_choice = assembly_receipt['texturePolicyReceipts']['player-choice-policy']
    # Both passes must bind exactly the same inputs, including transparent or
    # missing-piece witnesses; a mutable caller must not swap mappings mid-join.
    if canonical_json(choice_receipt) != canonical_json(final_choice):
        raise PipelineError('Fresh player choice provenance changed during assembly')
    if facts_receipt.get('itemHashes') != assembly_receipt.get('itemHashes'):
        raise PipelineError('Player item foundation changed during assembly')
    return objects, {'schemaVersion': 1, 'status': 'OBSERVED_PLAYER_DERIVED',
        'factAdaptation': facts_receipt, 'assembly': assembly_receipt,
        'executedInput': False, 'observationAuthenticated': False,
        'runtimeInitializationVerified': False, 'sourceSemanticsVerified': False,
        'complete': False, 'publishable': False}
