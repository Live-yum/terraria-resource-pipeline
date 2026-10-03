#!/usr/bin/env python3
"""Offline source binding only; policy must be separately reviewed by the operator."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from resource_pipeline.security import read_json
from resource_pipeline.source_attestation import ClientInstallationAttestation, build_attested_client

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--client-executable', type=Path, required=True)
    parser.add_argument('--operator-policy', type=Path, required=True,
                        help='Separately reviewed local configuration; never select an uploaded manifest')
    parser.add_argument('--output', type=Path, required=True, help='New private output directory')
    args = parser.parse_args()
    policy = ClientInstallationAttestation(**read_json(args.operator_policy, maximum=16 * 1024))
    print(json.dumps(build_attested_client(args.source_archive, args.client_executable, args.output, policy),
                     ensure_ascii=False, indent=2))
